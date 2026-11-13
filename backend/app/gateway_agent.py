"""Printer gateway (A.7): run this on the printers' network. It connects out to the server (see gateways.py for the protocol),
keeps the LAN links to the printers it is given, reports their status, and uploads and starts prints on request. It needs no
database and no inbound port.

  CORE_URL=https://slicy.example.org GATEWAY_TOKEN=gw_... python -m app.gateway_agent

The token is created by an admin on the server's Gateways page. Printer access codes arrive over the connection and are kept in
memory only.
"""
import asyncio
import json
import logging
import os
import socket
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import websockets

from . import bambulan

log = logging.getLogger("gateway")
VERSION = "1"


class Agent:
    def __init__(self, core_url: str, token: str, name: str = "gateway",
                 link_factory: Callable[..., Any] | None = None, probe: Callable[..., dict] | None = None):
        self.core_url = core_url.rstrip("/")
        self.token, self.name = token, name
        self.link_factory = link_factory or self._lan_link
        self.probe = probe or bambulan.probe
        self.links: dict[str, Any] = {}
        self.configs: dict[str, dict[str, Any]] = {}
        self.status: dict[str, dict[str, Any]] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws: Any = None
        self.connected = threading.Event()

    # ---- links
    def _lan_link(self, cfg: dict[str, Any]) -> Any:
        pid = cfg["id"]
        link = bambulan.LanLink({"id": pid, "host": cfg["host"], "port": cfg["port"], "ftp_port": cfg.get("ftp_port"), "serial": cfg["serial"]},
                                cfg["access_code"], self.on_status, lambda: self.status.get(pid, {}),
                                lambda: self.configs.get(pid, {}).get("bed_type"))
        link.start()
        return link

    def on_status(self, printer_id: str, update: dict[str, Any]) -> None:
        """Called from the link's threads."""
        self.status.setdefault(printer_id, {}).update(update)
        self._send({"type": "status", "printer_id": printer_id, "update": update})

    def _send(self, msg: dict[str, Any]) -> None:
        if self.loop and self.ws is not None and self.connected.is_set():
            try:
                asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(msg)), self.loop)
            except RuntimeError:
                pass

    def set_printers(self, printers: list[dict[str, Any]]) -> None:
        wanted = {p["id"]: p for p in printers}
        for pid in list(self.links):
            if pid not in wanted or self.configs.get(pid) != wanted[pid]:
                try:
                    self.links.pop(pid).stop()
                except Exception:
                    log.exception("stopping link %s", pid)
                self.configs.pop(pid, None)
                if pid not in wanted:
                    self.status.pop(pid, None)
        for pid, cfg in wanted.items():
            if pid not in self.links:
                self.configs[pid] = cfg
                try:
                    self.links[pid] = self.link_factory(cfg)
                except Exception:
                    log.exception("could not start link for %s", pid)
            else:
                self.configs[pid] = cfg
        for pid, st in self.status.items():           # a (re)connected server gets everything we know
            if pid in wanted and st:
                self._send({"type": "status", "printer_id": pid, "update": dict(st)})

    # ---- requests from the server
    def _download(self, path: str) -> Path:
        req = urllib.request.Request(self.core_url + path, headers={"Authorization": f"Bearer {self.token}"})
        fd, name = tempfile.mkstemp(suffix=".gcode.3mf")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, os.fdopen(fd, "wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
        except Exception:
            Path(name).unlink(missing_ok=True)
            raise
        return Path(name)

    def handle_print(self, msg: dict[str, Any]) -> dict[str, Any]:
        link = self.links.get(msg["printer_id"])
        if link is None:
            return {"ok": False, "error": "This gateway has no connection to that printer"}
        try:
            path = self._download(msg["transfer"])
        except (urllib.error.URLError, OSError) as e:
            return {"ok": False, "error": f"The gateway could not fetch the file from the server: {e}"}
        try:
            if msg.get("size") and path.stat().st_size != msg["size"]:
                return {"ok": False, "error": "The file arrived incomplete at the gateway"}
            return {"ok": True, "started": bool(link.send_print(path, msg["remote_name"], msg.get("plate") or "Metadata/plate_1.gcode"))}
        except bambulan.PrinterError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.exception("print failed")
            return {"ok": False, "error": f"Unexpected error at the gateway: {type(e).__name__}: {e}"}
        finally:
            path.unlink(missing_ok=True)

    def handle_control(self, msg: dict[str, Any]) -> dict[str, Any]:
        link = self.links.get(msg["printer_id"])
        if link is None:
            return {"ok": False, "error": "This gateway has no connection to that printer"}
        try:
            return {"ok": True, "done": bool(link.control(msg["action"]))}
        except (bambulan.PrinterError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    def handle_probe(self, msg: dict[str, Any]) -> dict[str, Any]:
        try:
            bambulan.check_host(msg["host"], resolve=False)
            return self.probe(msg["host"], msg["port"], msg["serial"], msg["access_code"])
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    async def _answer(self, msg: dict[str, Any], fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        res = await asyncio.get_running_loop().run_in_executor(None, fn, msg)
        await self.ws.send(json.dumps({"type": "result", "req": msg["req"], **res}))

    # ---- connection
    async def run_once(self) -> None:
        url = self.core_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/gateway/ws"
        self.loop = asyncio.get_running_loop()
        async with websockets.connect(url, max_size=2 ** 22, open_timeout=15) as ws:
            self.ws = ws
            await ws.send(json.dumps({"type": "hello", "token": self.token, "name": self.name, "version": VERSION}))
            tasks: set[asyncio.Task] = set()
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    kind = msg.get("type")
                    if kind == "welcome":
                        self.connected.set()
                        log.info("connected to %s", self.core_url)
                    elif kind == "printers":
                        await self.loop.run_in_executor(None, self.set_printers, msg["printers"])
                    elif kind in ("print", "probe", "control"):
                        handler = {"print": self.handle_print, "probe": self.handle_probe, "control": self.handle_control}[kind]
                        t = asyncio.create_task(self._answer(msg, handler))
                        tasks.add(t)
                        t.add_done_callback(tasks.discard)
            finally:
                self.connected.clear()
                self.ws = None
                for t in tasks:
                    t.cancel()

    async def run_forever(self) -> None:
        delay = 1.0
        while True:
            try:
                await self.run_once()
                delay = 1.0
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as e:
                log.warning("not connected (%s); retrying in %.0fs", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    url, token = os.environ.get("CORE_URL"), os.environ.get("GATEWAY_TOKEN")
    if not url or not token:
        raise SystemExit("Set CORE_URL (the server's address) and GATEWAY_TOKEN (from the server's Gateways page)")
    if url.startswith("http://") and not any(h in url for h in ("localhost", "127.0.0.1", "[::1]")):
        log.warning("CORE_URL is not https: the gateway token and printer access codes would cross the network unencrypted")
    asyncio.run(Agent(url, token, os.environ.get("GATEWAY_NAME") or socket.gethostname()).run_forever())


if __name__ == "__main__":
    main()
