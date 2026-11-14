"""Terms and safety acknowledgement (3.6). Owner decisions: admins write the text; people accept it before their first print;
a new version asks everyone to accept again before their next print; acceptances are logged.

With no text set, nothing is asked."""
import time
from typing import Any

from . import store

KEY = "terms"


def current() -> dict[str, Any] | None:
    t = store.settings.get(KEY)
    return t if t and (t.get("text") or "").strip() else None


def needed(user: dict[str, Any]) -> bool:
    t = current()
    return bool(t) and (user.get("terms_accepted") or {}).get("version") != t["version"]


def publish(text: str, user: dict[str, Any]) -> dict[str, Any]:
    old = store.settings.get(KEY) or {}
    t = {"version": int(old.get("version") or 0) + 1, "text": text.strip(), "updated": time.time(), "by": user["email"]}
    store.settings.put(KEY, t)
    return t


def accept(user: dict[str, Any]) -> dict[str, Any]:
    t = current()
    if not t:
        raise ValueError("There are no terms to accept")
    rec = {"version": t["version"], "at": time.time()}
    store.users.update(user["id"], terms_accepted=rec)
    from .auth import audit
    audit(user, "terms_accept", f"version {t['version']}")
    return rec
