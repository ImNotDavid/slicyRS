"""Printer gateways (A.7). A gateway is a small process on the printers' own network. It connects *out* to this server over a
WebSocket, so the server needs no route to the printers and the site needs no inbound port.

Protocol (JSON text frames; the gateway speaks first):
  gateway -> core   hello    {token, name, version}                      authenticates; a bad token closes the socket
  core -> gateway   welcome  {gateway}
  core -> gateway   printers {printers: [{id, host, port, ftp_port, serial, access_code, bed_type}]}   the printers it serves
                                                                          (sent on connect and whenever they change)
  gateway -> core   status   {printer_id, update}                        partial status report, as the LAN link produced it
  core -> gateway   print    {req, printer_id, remote_name, plate, size, transfer}   fetch the file from `transfer`, upload, start
                                                                          (`transfer` is a random path, fetchable only with that
                                                                          gateway's token and only for 15 minutes)
  core -> gateway   probe    {req, host, port, serial, access_code}      connection test for the admin page
  gateway -> core   result   {req, ok, started?, error?, ...}           answer to print / probe
The sliced file is not sent over the socket: the gateway downloads it from `transfer` with its token. Printer access codes cross the socket in the clear (the connection is TLS in production) and live only in the gateway's
memory. Bed state stays here: the gateway reports what the printer says and the server derives the plate state from it.
"""
import asyncio
import concurrent.futures
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import store
from .adapter import AdapterError
from .auth import audit, require_admin
from .crypto import unseal
from .printerstate import apply as apply_status

log = logging.getLogger("gateways")
router = APIRouter()
gateways = store.gateways
HELLO_TIMEOUT_S = 10
# What a gateway may write into a printer's status: the keys a LAN link produces, nothing else (and nothing large).
STATUS_KEYS = {"state", "progress", "remaining_min", "nozzle_c", "bed_c", "hms", "print_error", "layer_num", "total_layer_num",
               "subtask_name", "ams", "error", "nozzle_mm", "light"}
MAX_STATUS_BYTES = 20_000
PRINT_TIMEOUT_S = 240          # upload and start of a large file over a slow link
CONTROL_TIMEOUT_S = 30           # pause, resume or stop: the printer answers within seconds
PROBE_TIMEOUT_S = 20
TRANSFER_TTL_S = 900


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    digest = _hash(token)
    return next((g for g in gateways.all() if hmac.compare_digest(g["token_hash"], digest)), None)


def clean_status(update: Any) -> dict[str, Any]:
    """A gateway is trusted for the printers it serves, but not to store arbitrary or huge values in the database."""
    if not isinstance(update, dict):
        return {}
    out = {k: v for k, v in update.items() if k in STATUS_KEYS}
    return out if len(json.dumps(out)) <= MAX_STATUS_BYTES else {}


def printers_for(gateway_id: str) -> list[dict[str, Any]]:
    out = []
    for p in store.printers.all():
        if p.get("gateway") == gateway_id and p.get("host") and p.get("serial") and p.get("access_code") and p.get("enabled", True):
            out.append({"id": p["id"], "host": p["host"], "port": p.get("port") or 8883, "ftp_port": p.get("ftp_port") or 990,
                        "serial": p["serial"], "access_code": unseal(p["access_code"]) or "", "bed_type": p.get("bed_type")})
    return out


class Conn:
    def __init__(self, gateway: dict[str, Any], ws: WebSocket, loop: asyncio.AbstractEventLoop):
        self.gateway, self.ws, self.loop = gateway, ws, loop
        self.pending: dict[str, concurrent.futures.Future] = {}

    def send(self, msg: dict[str, Any]) -> None:
        """From any thread other than the socket's own."""
        asyncio.run_coroutine_threadsafe(self.ws.send_text(json.dumps(msg)), self.loop).result(timeout=10)

    def request(self, msg: dict[str, Any], timeout: float) -> dict[str, Any]:
        rid = uuid.uuid4().hex[:12]
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self.pending[rid] = fut
        try:
            self.send({**msg, "req": rid})
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise AdapterError(f"The printer gateway '{self.gateway['name']}' did not answer in time")
        except (RuntimeError, OSError, concurrent.futures.CancelledError):
            raise AdapterError(f"The printer gateway '{self.gateway['name']}' disconnected")
        finally:
            self.pending.pop(rid, None)

    def fail_pending(self) -> None:
        for fut in list(self.pending.values()):
            if not fut.done():
                fut.cancel()


class Hub:
    def __init__(self) -> None:
        self.conns: dict[str, Conn] = {}
        self.transfers: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def online(self, gateway_id: str) -> bool:
        return gateway_id in self.conns

    def request(self, gateway_id: str, msg: dict[str, Any], timeout: float) -> dict[str, Any]:
        conn = self.conns.get(gateway_id)
        if conn is None:
            g = gateways.get(gateway_id)
            raise AdapterError(f"The printer gateway '{g['name'] if g else gateway_id}' is offline, so this printer cannot be reached")
        return conn.request(msg, timeout)

    def push_all(self) -> None:
        """Tell every connected gateway which printers it serves (after printers were added, changed or removed)."""
        for gid, conn in list(self.conns.items()):
            try:
                conn.send({"type": "printers", "printers": printers_for(gid)})
            except Exception:
                log.warning("could not update gateway %s", gid)

    def offer_file(self, path: Path, gateway_id: str) -> str:
        now = time.time()
        with self._lock:
            for t in [t for t, v in self.transfers.items() if v["expires"] < now]:
                self.transfers.pop(t)
            token = secrets.token_urlsafe(24)
            self.transfers[token] = {"path": path, "gateway": gateway_id, "expires": now + TRANSFER_TTL_S}
        return token

    def take_file(self, token: str, gateway_id: str) -> Path | None:
        with self._lock:
            t = self.transfers.get(token)
        if not t or t["expires"] < time.time() or t["gateway"] != gateway_id or not t["path"].exists():
            return None
        return t["path"]


hub = Hub()


class RemoteLink:
    """The printer adapter for a printer behind a gateway: the same two methods as a local LAN link."""

    def __init__(self, printer_id: str, gateway_id: str):
        self.printer_id, self.gateway_id = printer_id, gateway_id

    def send_print(self, local_path: Path, remote_name: str, plate: str = "Metadata/plate_1.gcode", timelapse: bool = False) -> bool:
        token = hub.offer_file(local_path, self.gateway_id)
        res = hub.request(self.gateway_id, {"type": "print", "printer_id": self.printer_id, "remote_name": remote_name, "plate": plate, "timelapse": bool(timelapse),
                                            "size": local_path.stat().st_size, "transfer": f"/gateway/transfer/{token}"}, PRINT_TIMEOUT_S)
        if not res.get("ok"):
            from .bambulan import PrinterError
            raise PrinterError(res.get("error") or "The gateway could not print this")
        return bool(res.get("started"))

    def snapshot(self) -> bytes:
        import base64
        from .camera import CameraError
        res = hub.request(self.gateway_id, {"type": "snapshot", "printer_id": self.printer_id}, CONTROL_TIMEOUT_S)
        if not res.get("ok"):
            raise CameraError(res.get("error") or "The gateway could not take a picture")
        return base64.b64decode(res["jpeg"])

    def light(self, on: bool) -> None:
        res = hub.request(self.gateway_id, {"type": "light", "printer_id": self.printer_id, "on": bool(on)}, CONTROL_TIMEOUT_S)
        if not res.get("ok"):
            from .bambulan import PrinterError
            raise PrinterError(res.get("error") or "The gateway could not switch the light")

    def control(self, action: str) -> bool:
        res = hub.request(self.gateway_id, {"type": "control", "printer_id": self.printer_id, "action": action}, CONTROL_TIMEOUT_S)
        if not res.get("ok"):
            from .bambulan import PrinterError
            raise PrinterError(res.get("error") or f"The gateway could not {action} the print")
        return bool(res.get("done"))

    def stop(self) -> None:
        pass


def link_for(printer_id: str) -> RemoteLink | None:
    p = store.printers.get(printer_id)
    return RemoteLink(printer_id, p["gateway"]) if p and p.get("gateway") else None


def probe(gateway_id: str, host: str, port: int, serial: str, code: str) -> dict[str, Any]:
    res = hub.request(gateway_id, {"type": "probe", "host": host, "port": port, "serial": serial, "access_code": code}, PROBE_TIMEOUT_S)
    return {k: v for k, v in res.items() if k not in ("type", "req")}


# ---------------------------------------------------------------------------------------------- socket

@router.websocket("/gateway/ws")
async def gateway_socket(ws: WebSocket) -> None:
    await ws.accept()
    conn: Conn | None = None
    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT_S))
        gw = await run_in_threadpool(authenticate, hello.get("token") if hello.get("type") == "hello" else None)
        if gw is None:
            await ws.close(code=4401)
            return
        old = hub.conns.get(gw["id"])
        conn = Conn(gw, ws, asyncio.get_running_loop())
        hub.conns[gw["id"]] = conn
        if old:
            old.fail_pending()
            asyncio.create_task(old.ws.close(code=4409))
        gateways.update(gw["id"], last_seen=time.time(), version=str(hello.get("version") or "")[:20], host_name=str(hello.get("name") or "")[:60])
        await ws.send_text(json.dumps({"type": "welcome", "gateway": gw["id"]}))
        await ws.send_text(json.dumps({"type": "printers", "printers": await run_in_threadpool(printers_for, gw["id"])}))
        log.info("gateway %s connected", gw["name"])
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("type")
            if kind == "status":
                p = store.printers.get(str(msg.get("printer_id")))
                update = clean_status(msg.get("update"))
                if p and p.get("gateway") == gw["id"] and update:   # a gateway only reports its own printers
                    try:
                        await run_in_threadpool(apply_status, p["id"], update)
                    except Exception:
                        log.exception("could not apply a status report for printer %s", p["id"])
            elif kind == "result":
                fut = conn.pending.get(str(msg.get("req")))
                if fut and not fut.done():
                    fut.set_result(msg)
    except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, KeyError):
        pass
    finally:
        if conn is not None and hub.conns.get(conn.gateway["id"]) is conn:
            hub.conns.pop(conn.gateway["id"], None)
            conn.fail_pending()
            gateways.update(conn.gateway["id"], last_seen=time.time())
            await run_in_threadpool(_mark_offline, conn.gateway["id"])
            log.info("gateway %s disconnected", conn.gateway["name"])


def _mark_offline(gateway_id: str) -> None:
    for p in store.printers.all():
        if p.get("gateway") == gateway_id:
            apply_status(p["id"], {"state": "OFFLINE"})


@router.get("/gateway/transfer/{token}")
def transfer(token: str, authorization: str | None = Header(None)):
    gw = authenticate(authorization.removeprefix("Bearer ").strip() if authorization else None)
    path = hub.take_file(token, gw["id"]) if gw else None
    if path is None:
        raise HTTPException(404, "Unknown or expired transfer")
    return FileResponse(path, media_type="application/octet-stream")


# ---------------------------------------------------------------------------------------------- admin

class GatewayIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


def _public(g: dict[str, Any]) -> dict[str, Any]:
    return {"id": g["id"], "name": g["name"], "created": g.get("created"), "last_seen": g.get("last_seen"),
            "version": g.get("version"), "online": hub.online(g["id"]),
            "printers": [p["id"] for p in store.printers.all() if p.get("gateway") == g["id"]]}


@router.get("/api/admin/gateways")
def list_gateways(admin: dict = Depends(require_admin)):
    return [_public(g) for g in gateways.all()]


@router.post("/api/admin/gateways")
def add_gateway(body: GatewayIn, admin: dict = Depends(require_admin)):
    """The token is shown once; only its hash is kept."""
    if any(g["name"].lower() == body.name.strip().lower() for g in gateways.all()):
        raise HTTPException(409, "A gateway with that name already exists")
    gid, token = uuid.uuid4().hex[:8], "gw_" + secrets.token_urlsafe(32)
    rec = gateways.put(gid, {"id": gid, "name": body.name.strip(), "token_hash": _hash(token), "created": time.time(), "last_seen": None})
    audit(admin, "gateway_add", rec["name"])
    return {**_public(rec), "token": token}


@router.delete("/api/admin/gateways/{gateway_id}")
def delete_gateway(gateway_id: str, admin: dict = Depends(require_admin)):
    g = gateways.get(gateway_id)
    if not g:
        raise HTTPException(404, "Unknown gateway")
    if any(p.get("gateway") == gateway_id for p in store.printers.all()):
        raise HTTPException(409, "Printers still use this gateway; move them first")
    conn = hub.conns.pop(gateway_id, None)
    if conn:
        asyncio.run_coroutine_threadsafe(conn.ws.close(code=4403), conn.loop)
    gateways.delete(gateway_id)
    audit(admin, "gateway_delete", g["name"])
    return {"ok": True}
