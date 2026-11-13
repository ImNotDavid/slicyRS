"""Notification settings: each person's own on/off choices, and the shared channels admins set up."""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import notify, store
from ..auth import audit, require_admin
from .common import User

router = APIRouter()


@router.get("/api/me/notifications")
def my_notifications(user: dict = User):
    return notify.preferences(store.users.get(user["id"]) or user)


@router.put("/api/me/notifications")
def set_my_notifications(body: dict[str, bool], user: dict = User):
    unknown = [k for k in body if k not in notify.EVENTS]
    if unknown:
        raise HTTPException(400, f"Unknown notification: {', '.join(unknown)}")
    rec = store.users.update(user["id"], notify={**((store.users.get(user["id"]) or {}).get("notify") or {}), **body})
    return notify.preferences(rec or user)


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    url: str | None = None        # Discord webhook URL; kept when left empty on an update
    events: list[str] = []
    enabled: bool = True


def _public(ch: dict[str, Any]) -> dict[str, Any]:
    return {"id": ch["id"], "kind": ch["kind"], "name": ch["name"], "events": ch.get("events", []), "enabled": ch.get("enabled", True),
            "url_hint": "…" + ch["url"][-6:]}      # the webhook URL is a secret: the full address is never sent back


def _check(body: ChannelIn, *, need_url: bool) -> None:
    bad = [e for e in body.events if e not in notify.EVENTS]
    if bad:
        raise HTTPException(400, f"Unknown event: {', '.join(bad)}")
    if (need_url or body.url) and not notify.valid_webhook(body.url or ""):
        raise HTTPException(400, "That is not a Discord webhook address (it should start with https://discord.com/api/webhooks/)")


@router.get("/api/admin/notifications")
def overview(admin: dict = Depends(require_admin)):
    return {"events": [{"kind": k, "label": e["label"], "owners": e["owner_default"] is not None, "operators": e["operator_default"] is not None}
                       for k, e in notify.EVENTS.items()],
            "channels": [_public(c) for c in store.channels.all()], "recent": list(notify.deliveries)[::-1]}


@router.post("/api/admin/notifications/channels")
def add_channel(body: ChannelIn, admin: dict = Depends(require_admin)):
    _check(body, need_url=True)
    cid = uuid.uuid4().hex[:8]
    ch = store.channels.put(cid, {"id": cid, "kind": "discord", "name": body.name.strip(), "url": body.url, "events": body.events, "enabled": body.enabled})
    audit(admin, "channel_add", f"{ch['name']} ({', '.join(ch['events']) or 'no events'})")
    return _public(ch)


@router.put("/api/admin/notifications/channels/{cid}")
def update_channel(cid: str, body: ChannelIn, admin: dict = Depends(require_admin)):
    if not store.channels.get(cid):
        raise HTTPException(404, "Unknown channel")
    _check(body, need_url=False)
    fields: dict[str, Any] = {"name": body.name.strip(), "events": body.events, "enabled": body.enabled}
    if body.url:
        fields["url"] = body.url
    ch = store.channels.update(cid, **fields)
    audit(admin, "channel_update", f"{ch['name']}")  # type: ignore[index]
    return _public(ch)  # type: ignore[arg-type]


@router.delete("/api/admin/notifications/channels/{cid}")
def delete_channel(cid: str, admin: dict = Depends(require_admin)):
    ch = store.channels.get(cid)
    if not ch:
        raise HTTPException(404, "Unknown channel")
    store.channels.delete(cid)
    audit(admin, "channel_delete", ch["name"])
    return {"ok": True}


@router.post("/api/admin/notifications/channels/{cid}/test")
def test_channel(cid: str, admin: dict = Depends(require_admin)):
    ch = store.channels.get(cid)
    if not ch:
        raise HTTPException(404, "Unknown channel")
    notify._discord("test", ch, f"{notify.config.APP_NAME} test message", "If you can read this, the channel works.")
    last = list(notify.deliveries)[-1]
    if not last["ok"]:
        raise HTTPException(502, f"Discord did not accept the message: {last['error']}")
    return {"ok": True}
