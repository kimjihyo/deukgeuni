"""Wires Telegram ⇄ Agent ⇄ per-user Store. Transport-agnostic except for `handle_update`."""
from __future__ import annotations

import logging
import sys
import threading
from collections import OrderedDict

from .agent import Agent
from .config import REPO, Config
from .memory import Memory

sys.path.insert(0, str(REPO / "scripts"))
from wlcore.store import Store  # noqa: E402

log = logging.getLogger("deukgeuni.app")

HELP = """득근이예요. 운동한 걸 편하게 말해 주시면 기록하고, 다음 루틴을 알려드려요.
예) "스쿼트 160 4개 rpe9", "오늘 루틴", "월화 못 했어", "이번 주 어땠어?"

/reset  최근 대화 맥락 지우기 (기록·계획은 그대로)
/whoami  내 텔레그램 ID 보기"""


def user_key(telegram_id: int) -> str:
    return f"tg:{telegram_id}"


class BotApp:
    def __init__(self, cfg: Config, db, llm, telegram, mongo_client=None):
        self.cfg, self.db, self.tg = cfg, db, telegram
        self.mongo_client = mongo_client
        self.memory = Memory(db)
        self.agent = Agent(cfg, llm, self.memory, self.store_for)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._seen: OrderedDict[int, None] = OrderedDict()  # update_id dedupe (Telegram may redeliver)

    # ---- per-user data ----
    def store_for(self, uid: str) -> Store:
        return Store(config={"type": "mongodb", "user": uid, "client": self.mongo_client or self.db.client,
                             "database": self.db.name})

    def ensure_user_data(self, uid: str) -> bool:
        """First contact: seed the user's data from templates/. Returns True if seeded."""
        st = self.store_for(uid)
        try:
            if st.rows("exercises") or st.docs():
                return False
            tpl = Store(data_dir=REPO / "templates", backend="csv")
            st.replace_all(tpl.all())
            for d in tpl.docs():
                st.put_doc(d, tpl.doc(d))
            log.info("seeded new user %s", uid)
            return True
        finally:
            st.close()

    def _lock(self, uid: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(uid, threading.Lock())

    # ---- Telegram update ----
    def handle_update(self, update: dict) -> None:
        uid_n = update.get("update_id")
        if uid_n is not None:
            if uid_n in self._seen:
                return
            self._seen[uid_n] = None
            while len(self._seen) > 1000:
                self._seen.popitem(last=False)
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        frm = msg.get("from") or {}
        tg_id = frm.get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id or not tg_id:
            return
        if (msg.get("chat") or {}).get("type") != "private":
            return  # 1:1 대화만
        if text == "/whoami":
            self.tg.send(chat_id, f"텔레그램 ID: {tg_id}")
            return
        if self.cfg.allowed_ids and tg_id not in self.cfg.allowed_ids:
            log.warning("blocked user %s", tg_id)
            self.tg.send(chat_id, "아직 등록되지 않은 사용자예요. 관리자에게 /whoami 결과를 알려 주세요.")
            return
        if not text:
            self.tg.send(chat_id, "지금은 글자 메시지만 이해할 수 있어요.")
            return
        uid = user_key(tg_id)
        with self._lock(uid):  # one message at a time per user (order matters for logging)
            self.memory.touch_user(uid, frm.get("first_name", ""))
            seeded = self.ensure_user_data(uid)
            if text in ("/start", "/help"):
                self.tg.send(chat_id, HELP)
                if not seeded:
                    return
                text = "안녕, 처음이야. 시작하자"
            if text == "/reset":
                n = self.memory.forget(uid)
                self.tg.send(chat_id, f"최근 대화 {n}개를 지웠어요. 기록과 계획은 그대로예요.")
                return
            self.tg.typing(chat_id)
            try:
                reply = self.agent.handle(uid, text)
            except Exception:
                log.exception("agent failed for %s", uid)
                reply = "처리 중에 문제가 생겼어요. 잠시 뒤 다시 보내 주세요."
            self.tg.send(chat_id, reply)

    # ---- proactive message (cron) ----
    def remind(self, tg_id: int, instruction: str) -> str:
        uid = user_key(tg_id)
        with self._lock(uid):
            reply = self.agent.handle(uid, f"[자동 알림 요청 — 사용자가 보낸 말 아님] {instruction}", save_user_text=False)
        self.tg.send(tg_id, reply)  # private chat id == user id
        return reply
