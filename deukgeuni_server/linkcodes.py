"""One-time link codes (standard library + pymongo only), shared by the MCP login page and the Telegram bot."""
from __future__ import annotations

import hashlib
import secrets
import time

LINK_CODE_TTL = 15 * 60


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def issue(db, user_id: str) -> str:
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))  # e.g. 3F9A-0C21-B7E4 (48 bits, 15 min, single use)
    db["link_codes"].insert_one({"hash": _h(code), "user_id": user_id, "expires_at": time.time() + LINK_CODE_TTL})
    return code


def redeem(db, code: str) -> str | None:
    d = db["link_codes"].find_one_and_delete({"hash": _h(code.strip().upper())})
    if not d or d["expires_at"] < time.time():
        return None
    return d["user_id"]
