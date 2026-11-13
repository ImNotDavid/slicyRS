"""Notification service: something happens, a message goes to the right people over the right channels.

Callers describe the *event* (`emit`); this module decides who gets it and how.
  - People: the owners named by the caller, and (for events that operators care about) every active operator. Each person can
    switch an event type on or off for themselves; the catalogue below says what is on by default.
  - Channels: email to each person, and shared Discord webhook channels that an admin sets up and points at event types.
    More channels (Slack or Teams, SMS) plug in by adding a function to CHANNELS.
Delivery never blocks or breaks the caller: it runs in a background thread, failures are logged and shown to admins.
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any
from urllib.parse import urlparse

from . import config, mailer, store

log = logging.getLogger("notify")

# kind -> what it is, who it concerns, and whether a person gets it unless they switch it off.
# owner_default: default for the job's owners; operator_default: for operators (None: operators are not told).
EVENTS: dict[str, dict[str, Any]] = {
    "finished": {"label": "My print finished and is ready to collect", "owner_default": True, "operator_default": None},
    "failed": {"label": "My print failed or was stopped", "owner_default": True, "operator_default": True},
    "print_paused": {"label": "My print was paused", "owner_default": True, "operator_default": True},
    "print_error": {"label": "The printer reported an error during my print", "owner_default": True, "operator_default": True},
    "printer_lost": {"label": "The printer went offline during my print", "owner_default": True, "operator_default": True},
    "plate_waiting": {"label": "A printer's plate has not been cleared for a while", "owner_default": None, "operator_default": True},
    "plate_cleared": {"label": "Someone cleared the plate that held my part", "owner_default": True, "operator_default": None},
    "pickup_at_risk": {"label": "My print may miss its pickup time", "owner_default": False, "operator_default": None},
    "pickup_late": {"label": "My print is past its pickup time", "owner_default": False, "operator_default": None},
}

deliveries: deque[dict[str, Any]] = deque(maxlen=100)   # recent deliveries, newest last, for admins (and tests)
_DISCORD_HOSTS = {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}


def wants(user: dict[str, Any], kind: str, as_operator: bool) -> bool:
    """A person's own choice wins; otherwise the catalogue default for how they are involved."""
    choice = (user.get("notify") or {}).get(kind)
    if choice is not None:
        return bool(choice)
    return bool(EVENTS[kind]["operator_default" if as_operator else "owner_default"])


def preferences(user: dict[str, Any]) -> list[dict[str, Any]]:
    """What the person sees on their notification settings: each event they could get, and whether it is on."""
    from . import permissions
    operator = permissions.is_operator(user)
    out = []
    for kind, e in EVENTS.items():
        for_owner, for_op = e["owner_default"] is not None, e["operator_default"] is not None
        if not for_owner and not (for_op and operator):
            continue
        out.append({"kind": kind, "label": e["label"], "on": wants(user, kind, not for_owner)})
    return out


def valid_webhook(url: str) -> bool:
    p = urlparse(url)
    return p.scheme == "https" and (p.hostname or "") in _DISCORD_HOSTS and p.path.startswith("/api/webhooks/")


def _record(kind: str, channel: str, to: str, ok: bool, error: str = "") -> None:
    deliveries.append({"at": time.time(), "kind": kind, "channel": channel, "to": to, "ok": ok, "error": error})


def _email(kind: str, to: str, subject: str, body: str) -> None:
    try:
        mailer.send(to, subject, body)
        _record(kind, "email", to, True)
    except Exception as e:  # noqa: BLE001 - delivery problems are shown to admins, never raised to the caller
        log.exception("could not email %s", to)
        _record(kind, "email", to, False, str(e)[:200])


def _discord(kind: str, ch: dict[str, Any], subject: str, body: str) -> None:
    text = f"**{subject}**\n{body}"[:1900]
    req = urllib.request.Request(ch["url"], data=json.dumps({"content": text, "allowed_mentions": {"parse": []}}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": f"{config.APP_NAME}-notifier"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10).read()   # noqa: S310 - the URL is restricted to Discord's webhook host
        _record(kind, "discord", ch["name"], True)
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("could not post to Discord channel %s: %s", ch["name"], e)
        _record(kind, "discord", ch["name"], False, str(e)[:200])


def _run(jobs: list[tuple]) -> None:
    for fn, args in jobs:
        fn(*args)


def emit(kind: str, subject: str, body: str, *, owners: list[str] | None = None, operator_body: str | None = None,
         link: str | None = None, job: dict[str, Any] | None = None) -> int:
    """Send an event. `owners` are email addresses of the people it is about; operators are told too when the event is one they
    follow (with `operator_body` if given, a shorter text). Returns how many deliveries were queued."""
    if kind not in EVENTS:
        raise ValueError(f"Unknown notification event {kind}")
    url = f"{config.PUBLIC_URL}{link}" if link else None
    tail = f"\n\n{url}" if url else ""
    queued: list[tuple] = []
    told: set[str] = set()
    users = {e: u for u in store.users.all() for e in u.get("emails", [u["email"]]) if u.get("active", True)}
    for email in dict.fromkeys(owners or []):
        u = users.get(email)
        if u and wants(u, kind, False) and EVENTS[kind]["owner_default"] is not None:
            queued.append((_email, (kind, u["email"], subject, body + tail)))
            told.add(u["id"])
    if EVENTS[kind]["operator_default"] is not None:
        for u in store.users.all():
            if u.get("active", True) and (u.get("operator")) and u["id"] not in told and wants(u, kind, True):
                queued.append((_email, (kind, u["email"], subject, (operator_body or body) + tail)))
    for ch in store.channels.all():
        if ch.get("enabled", True) and kind in (ch.get("events") or []):
            queued.append((_discord, (kind, ch, subject, (operator_body or body) + tail)))
    if queued and os.environ.get("NOTIFY_SYNC") == "1":   # tests want the deliveries before the next assertion
        _run(queued)
    elif queued:
        threading.Thread(target=_run, args=(queued,), daemon=True).start()
    return len(queued)
