"""MongoDB backend — multi-user. Requires `pip install pymongo`.

One database holds every user. Each document carries `user_id`; a Store sees
only its own user's rows.

options (config.toml [backend] or Store(config=...)):
    type     = "mongodb"
    user     = "tg:123456"          # required: whose data
    uri      = env WL_MONGO_URI      # never put the URI/password in a file in git
    database = "deukgeuni"           # default

Collections: exercises, sessions, sets, docs (user Markdown), plus whatever the
bot adds (e.g. conversations). Writes for one table run in a transaction when
the server supports it (Atlas does); otherwise delete+insert.
"""
from __future__ import annotations

import os

from ..model import TABLES, Table
from .base import Backend


class MongoBackend(Backend):
    name = "mongodb"

    def __init__(self, options: dict):
        super().__init__(options)
        self.user = options.get("user") or os.environ.get("WL_USER")
        if not self.user:
            raise SystemExit("mongodb: user 옵션(또는 WL_USER)이 필요함")
        client = options.get("client")  # tests / bots may pass a ready client
        if client is None:
            from pymongo import MongoClient
            uri = options.get("uri") or os.environ.get("WL_MONGO_URI")
            if not uri:
                raise SystemExit("mongodb: WL_MONGO_URI 환경변수가 필요함")
            client = MongoClient(uri, appname="deukgeuni")
            self._own_client = True
        else:
            self._own_client = False
        self.client = client
        self.db = client[options.get("database") or os.environ.get("WL_MONGO_DB") or "deukgeuni"]
        self._ensure_indexes()

    def _ensure_indexes(self):
        for t in TABLES.values():
            self.db[t.name].create_index([("user_id", 1)] + [(k, 1) for k in t.key], unique=True)
        self.db["docs"].create_index([("user_id", 1), ("path", 1)], unique=True)

    # ---- records ----
    def load(self, table: Table) -> list[dict]:
        return list(self.db[table.name].find({"user_id": self.user}, {"_id": 0, "user_id": 0}))

    def save(self, table: Table, rows: list[dict]) -> None:
        coll = self.db[table.name]
        docs = [{"user_id": self.user, **{k: v for k, v in r.items() if v is not None}} for r in rows]

        def write(session=None):
            coll.delete_many({"user_id": self.user}, session=session)
            if docs:
                coll.insert_many(docs, session=session)

        try:
            with self.client.start_session() as s:
                s.with_transaction(lambda sess: write(sess))
        except Exception as e:  # no transactions (standalone server, mongomock): best effort
            if type(e).__name__ not in ("NotImplementedError", "OperationFailure", "ConfigurationError", "InvalidOperation"):
                raise
            write()

    # ---- documents ----
    def load_doc(self, path: str) -> str | None:
        d = self.db["docs"].find_one({"user_id": self.user, "path": path})
        return d["content"] if d else None

    def save_doc(self, path: str, text: str) -> None:
        self.db["docs"].update_one({"user_id": self.user, "path": path},
                                   {"$set": {"content": text}}, upsert=True)

    def list_docs(self) -> list[str]:
        return [d["path"] for d in self.db["docs"].find({"user_id": self.user}, {"path": 1})]

    def close(self):
        if self._own_client:
            self.client.close()
