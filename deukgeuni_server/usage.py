"""Usage accounting and daily limits (the operator pays for LLM calls made by the Telegram bot).
MCP calls cost the operator nothing in LLM fees (the connecting app's model does the work), but are counted too."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def _day(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")


def record(db, user_id: str, kind: str, tz: str = "Asia/Seoul", **tokens) -> None:
    inc = {f"{kind}_calls": 1}
    for k, v in tokens.items():
        if isinstance(v, (int, float)) and v:
            inc[f"{kind}_{k}"] = v
    db["usage"].update_one({"user_id": user_id, "day": _day(tz)}, {"$inc": inc}, upsert=True)


def check(db, user_id: str, per_user: int, total: int, tz: str = "Asia/Seoul") -> str | None:
    """None if allowed, else a message for the user. Limits count bot messages per day; 0 = unlimited."""
    day = _day(tz)
    if per_user:
        d = db["usage"].find_one({"user_id": user_id, "day": day}) or {}
        if d.get("bot_calls", 0) >= per_user:
            return f"오늘 대화 한도({per_user}회)를 다 썼어요. 내일 다시 이어가요. 기록은 다 저장돼 있어요."
    if total:
        used = sum(x.get("bot_calls", 0) for x in db["usage"].find({"day": day}, {"bot_calls": 1}))
        if used >= total:
            return "오늘 득근이가 너무 바빠서 잠시 쉬는 중이에요. 내일 다시 이어가요."
    return None


def report(db, days: int = 7) -> list[dict]:
    rows = list(db["usage"].find({}, {"_id": 0}).sort([("day", -1)]).limit(days * 1000))
    cutoff = sorted({r["day"] for r in rows}, reverse=True)[:days]
    return [r for r in rows if r["day"] in cutoff]
