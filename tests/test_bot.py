"""Offline end-to-end tests: fake Telegram, fake LLM, in-memory MongoDB (mongomock).
Run: python3 -m unittest discover -s tests -v   (needs: pip install mongomock anthropic openai)"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import mongomock  # noqa: E402

from bot.app import BotApp  # noqa: E402
from bot.config import Config  # noqa: E402
from bot.llm import AnthropicLLM, LLM, OpenAILLM, Reply, Segment, Tool  # noqa: E402
from wlcore.store import Store  # noqa: E402


class FakeTelegram:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.docs: list[tuple[int, str, bytes]] = []

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))

    def send_document(self, chat_id, filename, data, caption=""):
        self.docs.append((chat_id, filename, data))

    def typing(self, chat_id):
        pass


class ScriptedLLM(LLM):
    """Plays back tool calls, then a final text. Records what it was given."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def chat(self, system, messages, tools, user_key=""):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        step = self.script.pop(0)
        if isinstance(step, str):
            return Reply(text=step)
        return Reply(text="", tool_calls=[{"id": f"c{i}", "name": n, "input": inp} for i, (n, inp) in enumerate(step)])


def update(uid, text, update_id=1, chat_type="private"):
    return {"update_id": update_id, "message": {"chat": {"id": uid, "type": chat_type}, "from": {"id": uid, "first_name": "t"}, "text": text}}


def make_app(llm, allowed=(111,), mode="open", **kw):
    cfg = Config(allowed_ids=set(allowed), signup_mode=mode, telegram_token="x", llm_model="m", **kw)
    client = mongomock.MongoClient()
    return BotApp(cfg, client["deukgeuni"], llm, FakeTelegram(), mongo_client=client)


class BotTests(unittest.TestCase):
    def test_new_user_onboarding_seeds_templates(self):
        llm = ScriptedLLM(["안녕하세요, 득근이예요! 목표가 뭐예요?"])
        app = make_app(llm)
        app.handle_update(update(111, "/start"))
        st = app.store_for("tg:111")
        self.assertGreater(len(st.rows("exercises")), 10)          # default exercise dictionary
        self.assertIn("profile.md", st.docs())
        system_text = "\n".join(s.text for s in llm.calls[0]["system"])
        self.assertIn("skills/onboarding/SKILL.md", system_text)   # onboarding hint for a new user
        self.assertTrue(any("득근이" in t for _, t in app.tg.sent))

    def test_logging_flow_with_tools_and_memory(self):
        llm = ScriptedLLM([
            [("read_knowledge", {"path": "skills/log-workout/SKILL.md"})],
            [("wl", {"args": "session add 2026-11-21 --title '스쿼트 헤비'"})],
            [("wl", {"args": "set add 2026-11-21 barbell_back_squat 160x4@9 --type top"}),
             ("wl", {"args": "e1rm --lift barbell_back_squat"})],
            [("user_doc_get", {"path": "status.md"})],
            [("user_doc_put", {"path": "status.md", "content": "# 현재 상태\n\n- 다음: 일요일 데드\n- 메모: 왼쪽 어깨 뻐근 (11/21)\n"})],
            "기록했어요. 스쿼트 e1RM 185.",
            "네, RPE 9였던 그 세트 기준이에요.",
        ])
        app = make_app(llm)
        app.handle_update(update(111, "스쿼트 160 4개 rpe9, 어깨가 좀 뻐근해", 1))
        st = app.store_for("tg:111")
        sets = st.find("sets", session_id="2026-11-21")
        self.assertEqual([(s["weight_kg"], s["reps"], s["rpe"], s["set_type"]) for s in sets], [(160, 4, 9, "top")])
        self.assertIn("어깨", st.doc("status.md"))
        # tool results were fed back to the model
        tool_msgs = [m for m in llm.calls[3]["messages"] if m["role"] == "tool"]
        self.assertTrue(any("e1RM" in m["content"] for m in tool_msgs))
        self.assertTrue(any("SKILL" in m["content"] or "운동 기록" in m["content"] for m in tool_msgs))
        # next message carries the short-term conversation, and status.md (with the note) is in the prompt
        app.handle_update(update(111, "그거 몇 RPE였지?", 2))
        last = llm.calls[-1]
        self.assertEqual([m["role"] for m in last["messages"]], ["user", "assistant", "user"])
        self.assertIn("왼쪽 어깨", last["system"][2].text)
        self.assertFalse(last["system"][2].cache)
        self.assertTrue(last["system"][0].cache and last["system"][1].cache)

    def test_users_are_isolated_and_blocked_users_rejected(self):
        llm = ScriptedLLM([[("wl", {"args": "session add 2026-11-21"})], "ok"])
        app = make_app(llm, allowed=(111, 222), mode="allowlist")
        app.handle_update(update(111, "기록", 1))
        self.assertEqual(len(app.store_for("tg:111").rows("sessions")), 1)
        self.assertEqual(len(app.store_for("tg:222").rows("sessions")), 0)
        app.handle_update(update(999, "hi", 2))
        self.assertIn("초대된", app.tg.sent[-1][1])
        app.handle_update(update(999, "/whoami", 3))
        self.assertIn("999", app.tg.sent[-1][1])
        app.handle_update(update(111, "group msg", 4, chat_type="group"))
        self.assertEqual(len(llm.calls), 2)  # group message ignored

    def test_duplicate_update_ignored_and_reset(self):
        llm = ScriptedLLM(["a", "b"])
        app = make_app(llm)
        app.handle_update(update(111, "hi", 7))
        app.handle_update(update(111, "hi", 7))
        self.assertEqual(len(llm.calls), 1)
        app.handle_update(update(111, "/reset", 8))
        self.assertIn("지웠어요", app.tg.sent[-1][1])

    def test_link_code_for_other_apps(self):
        app = make_app(ScriptedLLM([]))
        app.handle_update(update(111, "/link", 31))
        msg = app.tg.sent[-1][1]
        self.assertIn("연결 코드", msg)
        code = msg.split("연결 코드: ")[1].split()[0]
        from deukgeuni_server import linkcodes
        self.assertEqual(linkcodes.redeem(app.db, code), "tg:111")
        self.assertIsNone(linkcodes.redeem(app.db, code))  # single use

    def test_tool_guards(self):
        from bot.tools import run_tool
        st = make_app(ScriptedLLM([])).store_for("tg:1")
        self.assertIn("쓸 수 없는", run_tool(st, "wl", {"args": "export /tmp/x"}))
        self.assertIn("쓸 수 없는", run_tool(st, "wl", {"args": "check --backend csv"}))
        self.assertIn("읽을 수 없는", run_tool(st, "read_knowledge", {"path": "../../etc/passwd"}))
        self.assertIn("읽을 수 없는", run_tool(st, "read_knowledge", {"path": "bot/config.py"}))
        self.assertIn("실패", run_tool(st, "user_doc_put", {"path": "../x.md", "content": "x"}))
        self.assertIn("[실패", run_tool(st, "wl", {"args": "set add nope barbell_back_squat 100x5"}))
        self.assertIn("[실패", run_tool(st, "wl", {"args": "nonsense"}))

    def test_import_and_backup_round_trip(self):
        from bot.__main__ import copy_all
        src = Store(data_dir=REPO / "templates", backend="csv")
        app = make_app(ScriptedLLM([]))
        dst = app.store_for("tg:5")
        copy_all(src, dst)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config.toml").write_text('[backend]\ntype = "csv"\n')
            out = Store(data_dir=d)
            copy_all(dst, out)
            self.assertEqual(out.all(), src.all())
            self.assertEqual(sorted(out.docs()), sorted(src.docs()))

    def test_remind_is_not_saved_as_user_text(self):
        llm = ScriptedLLM(["오늘은 스쿼트 데이!"])
        app = make_app(llm)
        app.ensure_user_data("tg:111")
        app.remind(111, "오늘 운동 안내")
        self.assertEqual(app.tg.sent[-1], (111, "오늘은 스쿼트 데이!"))
        self.assertEqual(app.memory.recent("tg:111", 10), [])  # history must start with a real user turn
        app.memory.append("tg:111", "user", "q")
        app.memory.append("tg:111", "assistant", "a")
        app.memory.append("tg:111", "assistant", "알림")
        self.assertEqual([m["role"] for m in app.memory.recent("tg:111", 10)], ["user", "assistant"])  # merged

    def test_real_data_import_and_tools(self):
        import os
        src = Path(os.environ.get("WL_TEST_DATA", "/nonexistent"))
        if not src.exists():
            self.skipTest("set WL_TEST_DATA=<a real data folder> to run")
        from bot.__main__ import copy_all
        from bot.tools import run_tool
        app = make_app(ScriptedLLM([]))
        st = app.store_for("tg:111")
        copy_all(Store(data_dir=src), st)
        self.assertIn("합계", run_tool(st, "wl", {"args": "e1rm"}))
        self.assertEqual(sorted(st.docs()), sorted(Store(data_dir=src).docs()))


class ThirdPartyTests(unittest.TestCase):
    """A stranger finds the bot and just starts talking (open signup, operator pays)."""

    def test_stranger_first_message_gets_welcome_and_onboarding(self):
        llm = ScriptedLLM(["반가워요! 목표부터 알려 주세요."])
        app = make_app(llm, allowed=())
        app.handle_update(update(5551, "안녕하세요", 1))
        texts = [t for _, t in app.tg.sent]
        self.assertTrue(any("득근이예요" in t and "/delete" in t for t in texts))  # welcome + privacy notice
        self.assertIn("목표", texts[-1])
        self.assertIn("skills/onboarding", llm.calls[0]["system"][2].text)
        self.assertGreater(len(app.store_for("tg:5551").rows("exercises")), 10)

    def test_daily_limits_and_admin_bypass(self):
        llm = ScriptedLLM(["a", "b", "c", "d"])
        app = make_app(llm, allowed=(), daily_user_limit=2, admin_ids={9})
        for i in range(3):
            app.handle_update(update(7001, f"m{i}", 10 + i))
        self.assertIn("한도", app.tg.sent[-1][1])
        self.assertEqual(len(llm.calls), 2)
        for i in range(2):
            app.handle_update(update(9, f"admin{i}", 20 + i))
        self.assertEqual(len(llm.calls), 4)  # admin not limited
        u = app.db["usage"].find_one({"user_id": "tg:7001"})
        self.assertEqual(u["bot_calls"], 2)

    def test_export_and_delete(self):
        import io
        import zipfile
        llm = ScriptedLLM([[("wl", {"args": "session add 2026-11-21"})],
                           [("wl", {"args": "set add 2026-11-21 barbell_back_squat 100x5@8"})], "ok"])
        app = make_app(llm, allowed=())
        app.handle_update(update(8001, "스쿼트 100 5개 rpe8", 1))
        app.handle_update(update(8001, "/export", 2))
        names = zipfile.ZipFile(io.BytesIO(app.tg.docs[-1][2])).namelist()
        self.assertIn("deukgeuni-data/sets.csv", names)
        self.assertIn("deukgeuni-data/profile.md", names)
        sets_csv = zipfile.ZipFile(io.BytesIO(app.tg.docs[-1][2])).read("deukgeuni-data/sets.csv").decode()
        self.assertIn("barbell_back_squat", sets_csv)
        app.handle_update(update(8001, "/delete 확인", 3))           # no prior prompt -> only asks
        self.assertIn("정말 지울까요", app.tg.sent[-1][1])
        app.handle_update(update(8001, "/delete 확인", 4))
        self.assertIn("지웠어요", app.tg.sent[-1][1])
        for coll in ("sets", "sessions", "exercises", "docs", "conversations", "usage", "accounts"):
            self.assertEqual(app.db[coll].count_documents({"user_id": "tg:8001"}), 0, coll)

    def test_connect_telegram_to_account_made_in_another_app(self):
        from deukgeuni_server import accounts, linkcodes
        app = make_app(ScriptedLLM(["ok"]), allowed=())
        web_uid = accounts.create_web(app.db, "1.2.3.4")
        st = app.store_for(web_uid)
        from wlcore import service
        service.seed_from_templates(st)
        service.log_sets(st, "2026-11-22", "barbell_deadlift", [{"weight_kg": 200, "reps": 5, "rpe": 9, "set_type": "top"}])
        app.handle_update(update(6001, "/start", 1))                 # telegram account created (empty)
        code = linkcodes.issue(app.db, web_uid)
        app.handle_update(update(6001, f"/connect {code.lower()}", 2))
        self.assertIn("연결했어요", app.tg.sent[-1][1])
        self.assertEqual(app.user_id(6001), web_uid)
        self.assertEqual(app.db["sets"].count_documents({"user_id": "tg:6001"}), 0)  # empty tg account cleaned up
        # a Telegram account that already has records is not silently orphaned
        other = accounts.create_web(app.db, "1.2.3.5")
        code2 = linkcodes.issue(app.db, other)
        app.handle_update(update(6001, f"/connect {code2}", 3))
        self.assertIn("이미 기록", app.tg.sent[-1][1])
        self.assertEqual(app.user_id(6001), web_uid)

    def test_allowlist_mode_still_available(self):
        app = make_app(ScriptedLLM([]), allowed=(1,), mode="allowlist")
        app.handle_update(update(2, "hi", 1))
        self.assertIn("초대된", app.tg.sent[-1][1])

    def test_signup_rate_limit(self):
        from deukgeuni_server import accounts
        app = make_app(ScriptedLLM([]))
        for _ in range(accounts.SIGNUPS_PER_IP_PER_WINDOW):
            accounts.create_web(app.db, "9.9.9.9")
        with self.assertRaises(PermissionError):
            accounts.create_web(app.db, "9.9.9.9")
        accounts.create_web(app.db, "9.9.9.10")  # other IPs unaffected


class WebhookTests(unittest.TestCase):
    def test_secret_and_dispatch(self):
        from bot.server import WEBHOOK_PATH, serve_webhook
        got = []
        app = SimpleNamespace(handle_update=lambda u: got.append(u))
        t = threading.Thread(target=serve_webhook, args=(app, "127.0.0.1", 18765, "s3cret"), daemon=True)
        t.start()
        time.sleep(0.3)

        def post(secret):
            req = urllib.request.Request(f"http://127.0.0.1:18765{WEBHOOK_PATH}", data=json.dumps({"update_id": 1}).encode(),
                                         headers={"X-Telegram-Bot-Api-Secret-Token": secret, "Content-Type": "application/json"})
            try:
                return urllib.request.urlopen(req, timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code

        self.assertEqual(post("wrong"), 401)
        self.assertEqual(post("s3cret"), 200)
        time.sleep(0.2)
        self.assertEqual(got, [{"update_id": 1}])
        self.assertEqual(urllib.request.urlopen("http://127.0.0.1:18765/healthz", timeout=5).status, 200)


class AdapterTests(unittest.TestCase):
    """Request shapes sent to each SDK (no network)."""

    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "wl", "input": {"args": "e1rm"}},
                                                                 {"id": "t2", "name": "wl", "input": {"args": "check"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "r1"},
            {"role": "tool", "tool_call_id": "t2", "content": "r2"}]
    system = [Segment("AGENTS", cache=True), Segment("PROFILE", cache=True), Segment("STATUS")]
    tools = [Tool("wl", "d", {"type": "object", "properties": {"args": {"type": "string"}}})]

    def test_anthropic_request(self):
        captured = {}
        block = SimpleNamespace(type="text", text="done")
        fake = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: captured.update(kw) or SimpleNamespace(
            content=[block], usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=5, cache_creation_input_tokens=0))))
        r = AnthropicLLM("claude-sonnet-5-5", client=fake).chat(self.system, self.msgs, self.tools)
        self.assertEqual(r.text, "done")
        sysb = captured["system"]
        self.assertEqual(sysb[1]["cache_control"], {"type": "ephemeral", "ttl": "1h"})  # last cached segment
        self.assertNotIn("cache_control", sysb[0])
        self.assertNotIn("cache_control", sysb[2])
        self.assertEqual(captured["cache_control"], {"type": "ephemeral"})
        m = captured["messages"]
        self.assertEqual([x["role"] for x in m], ["user", "assistant", "user"])  # two tool results merged into one turn
        self.assertEqual([b["tool_use_id"] for b in m[2]["content"]], ["t1", "t2"])
        self.assertEqual(captured["tools"][0]["input_schema"]["type"], "object")

    def test_openai_request(self):
        captured = {}
        msg = SimpleNamespace(content="done", tool_calls=None)
        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: captured.update(kw) or SimpleNamespace(
            choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1)))))
        r = OpenAILLM("some-model", client=fake).chat(self.system, self.msgs, self.tools, user_key="tg:1")
        self.assertEqual(r.text, "done")
        m = captured["messages"]
        self.assertEqual(m[0]["role"], "system")
        self.assertEqual([x["role"] for x in m[1:]], ["user", "assistant", "tool", "tool"])
        self.assertEqual(json.loads(m[2]["tool_calls"][0]["function"]["arguments"]), {"args": "e1rm"})
        self.assertEqual(captured["prompt_cache_key"], "tg:1")


if __name__ == "__main__":
    unittest.main()
