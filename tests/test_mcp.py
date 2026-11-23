"""End-to-end: real HTTP server (uvicorn) + OAuth (DCR, PKCE, login page, refresh, revoke) + real MCP client.
In-memory MongoDB (mongomock). Run: python3 -m unittest tests.test_mcp -v   (needs: pip install mcp mongomock)"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import httpx2 as httpx  # noqa: E402  (the HTTP client the MCP SDK ships)
import mongomock  # noqa: E402
import uvicorn  # noqa: E402

from mcp_server.server import asgi_app, build  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class MCPEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cfg = {"public_url": cls.base}
        cls.client = mongomock.MongoClient()
        cls.db = cls.client["deukgeuni"]
        cls.mcp = build(cfg, cls.db, cls.client)
        cls.provider = cls.mcp.provider
        server = uvicorn.Server(uvicorn.Config(asgi_app(cls.mcp, cfg), host="127.0.0.1", port=cls.port, log_level="warning"))
        cls.thread = threading.Thread(target=server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            try:
                httpx.get(cls.base + "/healthz", timeout=0.5)
                break
            except httpx.HTTPError:
                time.sleep(0.05)

    # ---------------------------------------------------------------- helpers
    def oauth_login(self, telegram_id: int) -> dict:
        h = httpx.Client(base_url=self.base, follow_redirects=False, timeout=10)
        reg = h.post("/register", json={"redirect_uris": ["http://localhost:9999/cb"], "client_name": "테스트 앱",
                                        "token_endpoint_auth_method": "none",
                                        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
        self.assertEqual(reg.status_code, 201, reg.text)
        client_id = reg.json()["client_id"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        r = h.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": "http://localhost:9999/cb",
                                        "code_challenge": challenge, "code_challenge_method": "S256", "state": "st8",
                                        "scope": "deukgeuni", "resource": self.base + "/mcp"})
        self.assertEqual(r.status_code, 302, r.text)
        login_url = r.headers["location"]
        self.assertIn("/login?req=", login_url)
        req = parse_qs(urlparse(login_url).query)["req"][0]
        page = h.get("/login", params={"req": req})
        self.assertIn("테스트 앱", page.text)
        bad = h.post("/login", content=f"req={req}&code=WRONG-CODE-XXXX", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("1/5", bad.text)
        code = self.provider.issue_link_code(f"tg:{telegram_id}")
        ok = h.post("/login", content=f"req={req}&code={code.lower()}", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(ok.status_code, 302, ok.text)
        cb = parse_qs(urlparse(ok.headers["location"]).query)
        self.assertEqual(cb["state"], ["st8"])
        tok = h.post("/token", data={"grant_type": "authorization_code", "code": cb["code"][0], "redirect_uri": "http://localhost:9999/cb",
                                     "client_id": client_id, "code_verifier": verifier})
        self.assertEqual(tok.status_code, 200, tok.text)
        again = h.post("/token", data={"grant_type": "authorization_code", "code": cb["code"][0], "redirect_uri": "http://localhost:9999/cb",
                                       "client_id": client_id, "code_verifier": verifier})
        self.assertNotEqual(again.status_code, 200)  # codes are single use
        self.assertIsNone(self.provider.redeem_link_code(code))  # link codes are single use
        return {**tok.json(), "client_id": client_id}

    async def session(self, token: str, fn):
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import httpx2, streamable_http_client
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http:
            async with streamable_http_client(self.base + "/mcp", http_client=http) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as s:
                    init = await s.initialize()
                    return await fn(s, init)

    @staticmethod
    def text(result) -> str:
        return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")

    # ---------------------------------------------------------------- tests
    def test_1_unauthenticated_is_rejected_with_metadata(self):
        r = httpx.post(self.base + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                       headers={"Accept": "application/json, text/event-stream"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("resource_metadata", r.headers.get("www-authenticate", ""))
        prm = httpx.get(self.base + "/.well-known/oauth-protected-resource/mcp").json()
        self.assertEqual(prm["resource"], self.base + "/mcp")
        asm = httpx.get(self.base + "/.well-known/oauth-authorization-server").json()
        self.assertTrue(asm["registration_endpoint"].endswith("/register"))
        self.assertIn("S256", asm["code_challenge_methods_supported"])

    def test_2_full_flow_tools_prompts_resources(self):
        tok = self.oauth_login(111)

        async def flow(s, init):
            out = {"instructions": init.instructions}
            tools = await s.list_tools()
            out["tools"] = {t.name: t for t in tools.tools}
            out["state"] = self.text(await s.call_tool("get_coaching_state", {}))
            out["find"] = self.text(await s.call_tool("find_exercise", {"text": "스쿼트"}))
            out["log"] = self.text(await s.call_tool("log_sets", {
                "date": "2026-11-21", "exercise_id": "barbell_back_squat",
                "sets": [{"weight_kg": 170, "reps": 4, "rpe": 9, "set_type": "top", "target": "165x3+@8"},
                         {"weight_kg": 150, "reps": 3, "rpe": 7.5, "set_type": "backoff", "times": 3}]}))
            out["e1rm"] = self.text(await s.call_tool("e1rm", {"lift": "barbell_back_squat"}))
            out["presc"] = self.text(await s.call_tool("prescribe", {"lift": "barbell_back_squat", "reps": 3, "rpe": 8}))
            out["fatigue"] = self.text(await s.call_tool("fatigue_check", {}))
            out["edit"] = self.text(await s.call_tool("edit_set", {"session_id": "2026-11-21", "exercise_id": "barbell_back_squat",
                                                                   "set_no": 1, "changes": {"note": "정정 테스트"}}))
            out["del_no"] = self.text(await s.call_tool("delete_set", {"session_id": "2026-11-21", "exercise_id": "barbell_back_squat", "set_no": 4}))
            out["del_yes"] = self.text(await s.call_tool("delete_set", {"session_id": "2026-11-21", "exercise_id": "barbell_back_squat",
                                                                        "set_no": 4, "confirm": True}))
            out["bad"] = self.text(await s.call_tool("log_sets", {"date": "2026-11-21", "exercise_id": "nope",
                                                                  "sets": [{"weight_kg": 100, "reps": 5}]}))
            out["doc_put"] = self.text(await s.call_tool("update_doc", {"path": "status.md", "content": "# 상태\n- 다음: 일요일 데드\n"}))
            out["doc_bad"] = self.text(await s.call_tool("update_doc", {"path": "../x.md", "content": "x"}))
            out["vol"] = self.text(await s.call_tool("weekly_volume", {"end": "2026-11-21"}))
            out["skill"] = self.text(await s.call_tool("read_knowledge", {"path": "skills/log-workout/SKILL.md"}))
            out["escape"] = self.text(await s.call_tool("read_knowledge", {"path": "mcp_server/oauth.py"}))
            out["prompts"] = [p.name for p in (await s.list_prompts()).prompts]
            p = await s.get_prompt("log", {"workout": "벤치 135 6개 rpe9"})
            out["prompt_log"] = p.messages[0].content.text
            res = await s.list_resources()
            out["resources"] = [str(r.uri) for r in res.resources]
            me = await s.read_resource("deukgeuni://me/state")
            out["me"] = me.contents[0].text
            return out

        out = asyncio.run(self.session(tok["access_token"], flow))
        self.assertIn("득근이", out["instructions"])
        self.assertTrue(out["tools"]["e1rm"].annotations.read_only_hint)
        self.assertTrue(out["tools"]["delete_set"].annotations.destructive_hint)
        self.assertFalse(out["tools"]["log_sets"].annotations.read_only_hint)
        self.assertIn("오늘:", out["state"])
        self.assertIn("profile.md", out["state"])            # new user was seeded from templates
        self.assertIn("barbell_back_squat", out["find"])
        self.assertIn("170x4@9", out["log"])
        self.assertIn("e1RM 197", out["log"])
        self.assertIn("197.0", out["e1rm"])
        self.assertIn("170 kg", out["presc"])
        self.assertIn("근거 2026-11-21 170x4@9", out["presc"])
        self.assertIn("피로 신호: 없음", out["fatigue"])
        self.assertIn("note", out["edit"])
        self.assertIn("confirm", out["del_no"])
        self.assertIn("1행 삭제", out["del_yes"])
        self.assertIn("[실패]", out["bad"])
        self.assertIn("저장됨", out["doc_put"])
        self.assertIn("[실패]", out["doc_bad"])
        self.assertIn("대퇴사두", out["vol"])
        self.assertIn("운동 기록", out["skill"])
        self.assertIn("[실패]", out["escape"])
        self.assertEqual(set(out["prompts"]), {"start", "today", "log", "weekly_review", "reschedule"})
        self.assertIn("벤치 135 6개", out["prompt_log"])
        self.assertIn("deukgeuni://knowledge/skills/log-workout/SKILL.md", out["resources"])
        self.assertIn("일요일 데드", out["me"])

        # refresh rotates; old refresh token cannot be reused; revoke kills the access token
        h = httpx.Client(base_url=self.base, timeout=10)
        r1 = h.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": tok["client_id"]})
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = h.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": tok["client_id"]})
        self.assertNotEqual(r2.status_code, 200)
        new_access = r1.json()["access_token"]
        rv = h.post("/revoke", data={"token": new_access, "client_id": tok["client_id"], "client_secret": ""})  # SDK form requires the field even for public clients
        self.assertEqual(rv.status_code, 200, rv.text)
        dead = httpx.post(self.base + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                          headers={"Authorization": f"Bearer {new_access}", "Accept": "application/json, text/event-stream"})
        self.assertEqual(dead.status_code, 401)

    def test_3_users_isolated_and_personal_token(self):
        tok_a = self.oauth_login(201)
        personal_b = self.provider.issue_personal_token("tg:202", "laptop")

        async def write_a(s, init):
            return self.text(await s.call_tool("log_sets", {"date": "2026-11-22", "exercise_id": "barbell_deadlift",
                                                            "sets": [{"weight_kg": 200, "reps": 5, "rpe": 9, "set_type": "top"}]}))

        async def read_b(s, init):
            return self.text(await s.call_tool("get_session", {"date": "2026-11-22"}))

        self.assertIn("200x5@9", asyncio.run(self.session(tok_a["access_token"], write_a)))
        self.assertNotIn("200x5", asyncio.run(self.session(personal_b, read_b)))  # B sees nothing of A
        self.assertEqual(len(self.provider.list_tokens("tg:202")), 1)
        self.assertEqual(self.provider.revoke_user("tg:202", "laptop"), 1)
        r = httpx.post(self.base + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                       headers={"Authorization": f"Bearer {personal_b}", "Accept": "application/json, text/event-stream"})
        self.assertEqual(r.status_code, 401)

    def test_5_stranger_signs_up_from_an_app_and_links_telegram(self):
        h = httpx.Client(base_url=self.base, follow_redirects=False, timeout=10)
        client_id = h.post("/register", json={"redirect_uris": ["http://localhost:9999/cb"], "client_name": "ChatGPT",
                                              "token_endpoint_auth_method": "none",
                                              "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}).json()["client_id"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        r = h.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": "http://localhost:9999/cb",
                                        "code_challenge": challenge, "code_challenge_method": "S256", "state": "s", "scope": "deukgeuni"})
        req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
        page = h.get("/login", params={"req": req}).text
        self.assertIn("새로 시작하기", page)
        self.assertIn("내 기록 불러오기", page)
        ok = h.post("/login", content=f"req={req}&action=signup", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(ok.status_code, 302, ok.text)
        code = parse_qs(urlparse(ok.headers["location"]).query)["code"][0]
        tok = h.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": "http://localhost:9999/cb",
                                     "client_id": client_id, "code_verifier": verifier}).json()

        async def flow(s, init):
            state = self.text(await s.call_tool("get_coaching_state", {}))
            link = self.text(await s.call_tool("get_link_code", {}))
            return state, link

        state, link = asyncio.run(self.session(tok["access_token"], flow))
        self.assertIn("새 사용자", state)
        self.assertIn("skills/onboarding", state)
        link_code = link.split("연결 코드: ")[1].split()[0]
        subject = self.db["oauth_tokens"].find_one({"kind": "access", "client_id": client_id})["subject"]
        self.assertTrue(subject.startswith("u:"))

        # the same person now opens the Telegram bot and connects with that code
        sys.path.insert(0, str(REPO / "tests"))
        from test_bot import FakeTelegram, ScriptedLLM, update  # noqa: E402
        from bot.app import BotApp
        from bot.config import Config
        app = BotApp(Config(signup_mode="open", telegram_token="x", llm_model="m"), self.db, ScriptedLLM([]), FakeTelegram(),
                     mongo_client=self.client)
        app.handle_update(update(4242, f"/connect {link_code}", 1))
        self.assertIn("연결했어요", app.tg.sent[-1][1])
        self.assertEqual(app.user_id(4242), subject)

    def test_4_login_brute_force_guard(self):
        h = httpx.Client(base_url=self.base, follow_redirects=False, timeout=10)
        client_id = h.post("/register", json={"redirect_uris": ["http://localhost:9999/cb"], "token_endpoint_auth_method": "none",
                                              "grant_types": ["authorization_code"], "response_types": ["code"]}).json()["client_id"]
        r = h.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": "http://localhost:9999/cb",
                                        "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s", "scope": "deukgeuni"})
        req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
        for i in range(5):
            last = h.post("/login", content=f"req={req}&code=NOPE-{i}", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertIn("5번", last.text)
        good = self.provider.issue_link_code("tg:1")
        closed = h.post("/login", content=f"req={req}&code={good}", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(closed.status_code, 400)  # request is dead even with a valid code


if __name__ == "__main__":
    unittest.main()
