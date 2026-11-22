"""Per-user data export and deletion (privacy)."""
from __future__ import annotations

import io
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from wlcore.store import Store  # noqa: E402

USER_COLLECTIONS = ("exercises", "sessions", "sets", "docs", "conversations", "usage")


def store_for(db, user_id: str, client=None) -> Store:
    return Store(config={"type": "mongodb", "user": user_id, "client": client or db.client, "database": db.name})


def has_records(db, user_id: str) -> bool:
    return db["sets"].count_documents({"user_id": user_id}, limit=1) > 0


def export_zip(db, user_id: str, client=None) -> bytes:
    """CSV records + Markdown docs, the same layout as a file data folder (usable with wl.py)."""
    src = store_for(db, user_id, client)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "config.toml").write_text('[backend]\ntype = "csv"\n', encoding="utf-8")
        dst = Store(data_dir=d)
        dst.replace_all(src.all())
        for p in src.docs():
            dst.put_doc(p, src.doc(p))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(Path(d).rglob("*")):
                if f.is_file():
                    z.write(f, f"deukgeuni-data/{f.relative_to(d)}")
        return buf.getvalue()


def delete_all(db, user_id: str) -> dict:
    """Remove everything about this user: records, docs, conversations, usage, tokens, codes, account."""
    out = {}
    for name in USER_COLLECTIONS:
        out[name] = db[name].delete_many({"user_id": user_id}).deleted_count
    out["oauth_tokens"] = db["oauth_tokens"].delete_many({"subject": user_id}).deleted_count
    out["oauth_codes"] = db["oauth_codes"].delete_many({"subject": user_id}).deleted_count
    out["link_codes"] = db["link_codes"].delete_many({"user_id": user_id}).deleted_count
    out["users"] = db["users"].delete_many({"user_id": user_id}).deleted_count
    out["accounts"] = db["accounts"].delete_many({"user_id": user_id}).deleted_count
    return out
