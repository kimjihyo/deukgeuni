"""Accounts shared by every runtime (Telegram bot, MCP server).

A user id is the key of all their data. Telegram-born accounts keep the id "tg:<telegram id>";
accounts created from another app (MCP "새로 시작하기") get "u:<random>". A Telegram id can be
linked to an existing account with a link code, so one person has one record across apps.
"""
from __future__ import annotations

import secrets
import time

SIGNUP_WINDOW = 3600          # seconds
SIGNUPS_PER_IP_PER_WINDOW = 5


def _coll(db):
    c = db["accounts"]
    return c


def ensure_indexes(db) -> None:
    _coll(db).create_index("user_id", unique=True)
    _coll(db).create_index("telegram_id", unique=True, sparse=True)


def for_telegram(db, telegram_id: int, name: str = "") -> tuple[str, bool]:
    """Return (user_id, created). Creates the account on first contact."""
    c = _coll(db)
    d = c.find_one({"telegram_id": telegram_id})
    if d:
        return d["user_id"], False
    uid = f"tg:{telegram_id}"
    created = c.update_one({"user_id": uid}, {"$setOnInsert": {"created": time.time(), "via": "telegram", "name": name},
                                               "$set": {"telegram_id": telegram_id}}, upsert=True).upserted_id is not None
    return uid, created


def create_web(db, ip: str | None = None) -> str:
    """New account from another app's login page. Rate-limited per IP."""
    c = _coll(db)
    if ip:
        recent = c.count_documents({"via": "web", "ip": ip, "created": {"$gt": time.time() - SIGNUP_WINDOW}})
        if recent >= SIGNUPS_PER_IP_PER_WINDOW:
            raise PermissionError("잠시 뒤 다시 시도해 주세요 (새 계정을 너무 많이 만들었어요)")
    uid = "u:" + secrets.token_hex(6)
    c.insert_one({"user_id": uid, "created": time.time(), "via": "web", "ip": ip})
    return uid


def link_telegram(db, telegram_id: int, user_id: str, has_data) -> str:
    """Point this Telegram id at user_id. has_data(uid) -> bool tells whether an account holds records.
    Refuses to orphan a Telegram account that already has its own records."""
    c = _coll(db)
    if not c.find_one({"user_id": user_id}):
        raise ValueError("연결할 계정이 없어요")
    cur = c.find_one({"telegram_id": telegram_id})
    if cur and cur["user_id"] == user_id:
        return "이미 연결돼 있어요"
    if cur and has_data(cur["user_id"]):
        raise ValueError("이 텔레그램 계정에 이미 기록이 있어요. 두 계정을 합치는 건 아직 지원하지 않아요.")
    if cur:
        c.update_one({"user_id": cur["user_id"]}, {"$unset": {"telegram_id": ""}})
    c.update_one({"user_id": user_id}, {"$set": {"telegram_id": telegram_id}})
    return "연결했어요"


def exists(db, user_id: str) -> bool:
    return _coll(db).find_one({"user_id": user_id}) is not None


def all_ids(db) -> list[str]:
    return [d["user_id"] for d in _coll(db).find({}, {"user_id": 1})]
