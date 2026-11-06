"""Encrypt printer access codes at rest (Fernet, key derived from SECRET_KEY)."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import SECRET_KEY

_f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"slicyrs-printer-codes:" + SECRET_KEY.encode()).digest()))
_PREFIX = "enc:"


def seal(plain: str | None) -> str | None:
    if not plain:
        return None
    return _PREFIX + _f.encrypt(plain.encode()).decode()


def unseal(stored: str | None) -> str | None:
    if not stored:
        return None
    if not stored.startswith(_PREFIX):  # tolerate legacy plaintext
        return stored
    try:
        return _f.decrypt(stored[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        return None  # key changed: treat as unset rather than crash
