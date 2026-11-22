"""Wires Telegram ⇄ Agent ⇄ per-user Store. Transport-agnostic except for `handle_update`."""
from __future__ import annotations

import logging
import sys
import threading
from collections import OrderedDict

from .agent import Agent
from .config import REPO, Config
from .memory import Memory

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from deukgeuni_server import accounts, linkcodes, usage, userdata  # noqa: E402
from wlcore.store import Store  # noqa: E402

log = logging.getLogger("deukgeuni.app")

WELCOME = """안녕하세요, 득근이예요! 💪
파워빌딩(3대 근력 + 근육) 코치 겸 기록 담당이에요. 운동한 걸 편하게 말해 주면 기록하고, e1RM을 계산하고, 다음 루틴을 짜 드려요.

시작하기 전에 몇 가지만 여쭤볼게요.

ℹ️ 기록과 대화는 득근이 서버에 저장되고, 답변은 AI 모델이 만들어요. 언제든 /export 로 내려받고 /delete 로 지울 수 있어요."""

HELP = """득근이 사용법
예) "스쿼트 160 4개 rpe9", "오늘 루틴", "월화 못 했어", "이번 주 어땠어?"

/link  다른 앱(Claude, ChatGPT 등)에 득근이 연결 코드 받기
/connect 코드  다른 앱에서 받은 코드로 이 텔레그램을 그 계정에 연결
/export  내 기록 파일로 받기
/delete  내 데이터 전부 지우기
/reset  최근 대화 맥락만 지우기 (기록·계획은 그대로)
/whoami  내 텔레그램 ID"""


class BotApp:
    def __init__(self, cfg: Config, db, llm, telegram, mongo_client=None):
        self.cfg, self.db, self.tg = cfg, db, telegram
        self.mongo_client = mongo_client
        self.memory = Memory(db)
        accounts.ensure_indexes(db)
        self.agent = Agent(cfg, llm, self.memory, self.store_for)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._seen: OrderedDict[int, None] = OrderedDict()  # update_id dedupe (Telegram may redeliver)
        self._pending_delete: set[str] = set()

    # ---- per-user data ----
    def store_for(self, uid: str) -> Store:
        return userdata.store_for(self.db, uid, self.mongo_client or self.db.client)

    def user_id(self, tg_id: int, name: str = "") -> str:
        return accounts.for_telegram(self.db, tg_id, name)[0]

    def ensure_user_data(self, uid: str) -> bool:
        """First contact: seed the user's data from templates/. Returns True if seeded."""
        from wlcore import service
        st = self.store_for(uid)
        try:
            seeded = service.seed_from_templates(st)
            if seeded:
                log.info("seeded new user %s", uid)
            return seeded
        finally:
            st.close()

    def _lock(self, uid: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(uid, threading.Lock())

    def _allowed(self, tg_id: int) -> bool:
        if tg_id in self.cfg.admin_ids:
            return True
        if self.cfg.signup_mode == "allowlist":
            return tg_id in self.cfg.allowed_ids
        return True  # open

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
        chat = msg.get("chat") or {}
        frm = msg.get("from") or {}
        chat_id, tg_id = chat.get("id"), frm.get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id or not tg_id or chat.get("type") != "private":
            return  # 1:1 대화만
        if text == "/whoami":
            self.tg.send(chat_id, f"텔레그램 ID: {tg_id}")
            return
        if not self._allowed(tg_id):
            log.warning("blocked user %s", tg_id)
            self.tg.send(chat_id, "지금은 초대된 분만 쓸 수 있어요. 관리자에게 /whoami 결과를 알려 주세요.")
            return
        if not text:
            self.tg.send(chat_id, "지금은 글자 메시지만 이해할 수 있어요.")
            return

        uid = self.user_id(tg_id, frm.get("first_name", ""))
        with self._lock(uid):  # one message at a time per user (order matters for logging)
            self.memory.touch_user(uid, frm.get("first_name", ""))
            cmd, _, arg = text.partition(" ")
            if cmd == "/connect":
                return self._connect(chat_id, tg_id, uid, arg)
            if cmd == "/delete":
                return self._delete(chat_id, uid, arg)
            self._pending_delete.discard(uid)
            seeded = self.ensure_user_data(uid)
            if cmd in ("/start", "/help"):
                if not seeded:
                    self.tg.send(chat_id, HELP)
                    return
                self.tg.send(chat_id, WELCOME)
                text = "안녕, 처음이야. 시작하자"
            elif seeded:
                self.tg.send(chat_id, WELCOME)  # first message was not /start: still greet and disclose
            if cmd == "/link":
                code = linkcodes.issue(self.db, uid)
                self.tg.send(chat_id, f"연결 코드: {code}\n\n15분 안에, 한 번만 쓸 수 있어요. 다른 앱에서 득근이 커넥터를 추가하면 뜨는 연결 화면에 넣으세요.")
                return
            if cmd == "/export":
                data = userdata.export_zip(self.db, uid, self.mongo_client)
                self.tg.send_document(chat_id, "deukgeuni-data.zip", data, "내 득근이 기록 (CSV + 문서). 득근이 레포의 wl.py로 바로 열 수 있어요.")
                return
            if cmd == "/reset":
                n = self.memory.forget(uid)
                self.tg.send(chat_id, f"최근 대화 {n}개를 지웠어요. 기록과 계획은 그대로예요.")
                return
            if tg_id not in self.cfg.admin_ids:
                blocked = usage.check(self.db, uid, self.cfg.daily_user_limit, self.cfg.daily_total_limit, self.cfg.timezone)
                if blocked:
                    self.tg.send(chat_id, blocked)
                    return
            self.tg.typing(chat_id)
            try:
                reply = self.agent.handle(uid, text)
            except Exception:
                log.exception("agent failed for %s", uid)
                reply = "처리 중에 문제가 생겼어요. 잠시 뒤 다시 보내 주세요."
            usage.record(self.db, uid, "bot", self.cfg.timezone, **self.agent.last_usage.pop(uid, {}))
            self.tg.send(chat_id, reply)

    def _connect(self, chat_id: int, tg_id: int, uid: str, code: str) -> None:
        if not code.strip():
            self.tg.send(chat_id, "다른 앱에서 득근이에게 '연결 코드 줘'라고 해서 받은 코드를 함께 보내 주세요. 예) /connect 3F9A-0C21-B7E4")
            return
        target = linkcodes.redeem(self.db, code)
        if not target:
            self.tg.send(chat_id, "코드가 맞지 않거나 만료됐어요. 새 코드를 받아 다시 시도해 주세요.")
            return
        try:
            msg = accounts.link_telegram(self.db, tg_id, target, lambda u: userdata.has_records(self.db, u))
        except ValueError as e:
            self.tg.send(chat_id, str(e))
            return
        if uid != target and not userdata.has_records(self.db, uid):
            for name in userdata.USER_COLLECTIONS:  # the empty Telegram-only account is no longer used
                self.db[name].delete_many({"user_id": uid})
            self.db["accounts"].delete_many({"user_id": uid, "telegram_id": {"$exists": False}})
        self.tg.send(chat_id, f"{msg}! 이제 텔레그램과 다른 앱이 같은 기록을 써요.")

    def _delete(self, chat_id: int, uid: str, arg: str) -> None:
        if arg.strip() == "확인" and uid in self._pending_delete:
            self._pending_delete.discard(uid)
            n = userdata.delete_all(self.db, uid)
            log.info("deleted user %s: %s", uid, n)
            self.tg.send(chat_id, "모든 기록과 대화, 연결을 지웠어요. 다시 시작하려면 /start")
            return
        self._pending_delete.add(uid)
        self.tg.send(chat_id, "정말 지울까요? 기록·문서·대화·다른 앱 연결이 전부 지워지고 되돌릴 수 없어요.\n"
                              "먼저 /export 로 받아 두는 걸 권해요.\n\n지우려면: /delete 확인")

    # ---- proactive message (cron) ----
    def remind(self, tg_id: int, instruction: str) -> str:
        uid = self.user_id(tg_id)
        with self._lock(uid):
            reply = self.agent.handle(uid, f"[자동 알림 요청 — 사용자가 보낸 말 아님] {instruction}", save_user_text=False)
            usage.record(self.db, uid, "bot", self.cfg.timezone, **self.agent.last_usage.pop(uid, {}))
        self.tg.send(tg_id, reply)  # private chat id == user id
        return reply


def user_key(telegram_id: int) -> str:  # legacy helper (default id of a Telegram-born account)
    return f"tg:{telegram_id}"
