"""Per-user conversation context (short-term memory) and user registry, in MongoDB.
Long-term memory lives in the user's records and docs (wlcore Store)."""
from __future__ import annotations

from datetime import datetime, timezone


class Memory:
    def __init__(self, db):
        self.conv = db["conversations"]
        self.users = db["users"]
        self.conv.create_index([("user_id", 1), ("_id", -1)])
        self.users.create_index("user_id", unique=True)

    def recent(self, user_id: str, n: int) -> list[dict]:
        # _id (ObjectId) keeps insertion order even within the same millisecond, unlike ts
        docs = list(self.conv.find({"user_id": user_id}, {"_id": 0, "role": 1, "content": 1}).sort("_id", -1).limit(n))
        msgs = [{"role": d["role"], "content": d["content"]} for d in reversed(docs)]
        while msgs and msgs[0]["role"] != "user":  # providers want the history to start with a user turn
            msgs.pop(0)
        merged: list[dict] = []  # e.g. a reminder followed by a reply: keep roles alternating
        for m in msgs:
            if merged and merged[-1]["role"] == m["role"]:
                merged[-1] = {"role": m["role"], "content": merged[-1]["content"] + "\n\n" + m["content"]}
            else:
                merged.append(m)
        return merged

    def append(self, user_id: str, role: str, content: str) -> None:
        self.conv.insert_one({"user_id": user_id, "role": role, "content": content, "ts": datetime.now(timezone.utc)})

    def forget(self, user_id: str) -> int:
        return self.conv.delete_many({"user_id": user_id}).deleted_count

    def touch_user(self, user_id: str, name: str = "") -> bool:
        """Register/update a user. Returns True if the user is new."""
        r = self.users.update_one({"user_id": user_id},
                                  {"$set": {"name": name, "last_seen": datetime.now(timezone.utc)},
                                   "$setOnInsert": {"created": datetime.now(timezone.utc)}}, upsert=True)
        return r.upserted_id is not None

    def all_users(self) -> list[str]:
        return [u["user_id"] for u in self.users.find({}, {"user_id": 1})]
