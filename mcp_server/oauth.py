"""OAuth 2.1 authorization server for the 득근이 MCP server, stored in MongoDB.

The MCP SDK provides the HTTP endpoints (/authorize, /token, /register, /revoke, metadata).
This module provides the storage and the one human step: the login page.

Who is the user?  A person proves who they are with a one-time **link code**, issued by
  - the server admin:   python -m mcp_server issue-code --telegram-id 123
  - (later) the Telegram bot:  /link
The code maps to the same user id the Telegram bot uses ("tg:<id>"), so every app sees the
same records.

Tokens are random strings; only their SHA-256 is stored.
"""
from __future__ import annotations

import hashlib
import html
import secrets
import time
from urllib.parse import parse_qs

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
                                      OAuthAuthorizationServerProvider, RefreshToken, TokenError,
                                      construct_redirect_uri)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from deukgeuni_server import accounts, linkcodes

ACCESS_TTL = 60 * 60            # 1 hour
REFRESH_TTL = 60 * 60 * 24 * 60  # 60 days
CODE_TTL = 5 * 60               # authorization code
PENDING_TTL = 15 * 60           # login page must be completed within
SCOPE = "deukgeuni"


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> float:
    return time.time()


class MongoOAuthProvider(OAuthAuthorizationServerProvider):
    def __init__(self, db, issuer_url: str, resource_url: str, allow_signup: bool = True):
        self.db = db
        self.allow_signup = allow_signup
        self.issuer_url = issuer_url.rstrip("/")
        self.resource_url = resource_url
        self.clients = db["oauth_clients"]
        self.pending = db["oauth_pending"]
        self.codes = db["oauth_codes"]
        self.tokens = db["oauth_tokens"]          # access + refresh + personal tokens
        self.links = db["link_codes"]
        self.clients.create_index("client_id", unique=True)
        self.tokens.create_index("hash", unique=True)
        self.codes.create_index("hash", unique=True)
        self.links.create_index("hash", unique=True)

    # ---------------------------------------------------------------- clients (Dynamic Client Registration)
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        d = self.clients.find_one({"client_id": client_id}, {"_id": 0})
        return OAuthClientInformationFull.model_validate(d["info"]) if d else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.clients.update_one({"client_id": client_info.client_id},
                                {"$set": {"info": client_info.model_dump(mode="json"), "created": _now()}}, upsert=True)

    # ---------------------------------------------------------------- authorization: send the user to our login page
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        req = secrets.token_urlsafe(24)
        self.pending.insert_one({"req": req, "client_id": client.client_id, "params": params.model_dump(mode="json"),
                                 "client_name": client.client_name or client.client_id, "expires_at": _now() + PENDING_TTL})
        return f"{self.issuer_url}/login?req={req}"

    def complete_signup(self, req: str, ip: str | None) -> str:
        """'새로 시작하기': create a fresh account and finish the login."""
        p = self.pending.find_one({"req": req})
        if not p or p["expires_at"] < _now():
            raise AuthorizeError("access_denied", "로그인 요청이 만료됨. 앱에서 다시 연결해 주세요.")
        if not self.allow_signup:
            raise AuthorizeError("access_denied", "지금은 새 계정을 만들 수 없어요. 연결 코드로 로그인해 주세요.")
        try:
            user = accounts.create_web(self.db, ip)
        except PermissionError as e:
            raise AuthorizeError("access_denied", str(e))
        return self._finish(p, user)

    def complete_login(self, req: str, link_code: str) -> str:
        """Called by the login page. Returns the client's redirect URL (with code) or raises AuthorizeError."""
        p = self.pending.find_one({"req": req})
        if not p or p["expires_at"] < _now():
            raise AuthorizeError("access_denied", "로그인 요청이 만료됨. 앱에서 다시 연결해 주세요.")
        user = self.redeem_link_code(link_code)
        if not user:
            fails = p.get("fails", 0) + 1
            if fails >= 5:  # brute-force guard: the login request dies after 5 wrong codes
                self.pending.delete_one({"req": req})
                raise AuthorizeError("access_denied", "코드를 5번 틀려서 이 연결 요청은 닫혔어요. 앱에서 다시 연결해 주세요.")
            self.pending.update_one({"req": req}, {"$set": {"fails": fails}})
            raise AuthorizeError("access_denied", f"연결 코드가 맞지 않거나 만료됨 ({fails}/5)")
        return self._finish(p, user)

    def _finish(self, p: dict, user: str) -> str:
        self.pending.delete_one({"req": p["req"]})
        params = AuthorizationParams.model_validate(p["params"])
        code = secrets.token_urlsafe(32)
        self.codes.insert_one({
            "hash": _h(code), "client_id": p["client_id"], "subject": user,
            "scopes": params.scopes or [SCOPE], "expires_at": _now() + CODE_TTL,
            "code_challenge": params.code_challenge, "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": params.resource or self.resource_url,
        })
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        d = self.codes.find_one({"hash": _h(authorization_code), "client_id": client.client_id})
        if not d or d["expires_at"] < _now():
            return None
        return AuthorizationCode(code=authorization_code, scopes=d["scopes"], expires_at=d["expires_at"],
                                 client_id=d["client_id"], code_challenge=d["code_challenge"],
                                 redirect_uri=d["redirect_uri"],
                                 redirect_uri_provided_explicitly=d["redirect_uri_provided_explicitly"],
                                 resource=d.get("resource"), subject=d["subject"])

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        if self.codes.delete_one({"hash": _h(authorization_code.code)}).deleted_count != 1:  # single use
            raise TokenError("invalid_grant", "authorization code already used")
        return self._issue(client.client_id, authorization_code.subject, authorization_code.scopes, authorization_code.resource)

    # ---------------------------------------------------------------- tokens
    def _issue(self, client_id: str, subject: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = _now()
        base = {"client_id": client_id, "subject": subject, "scopes": scopes, "resource": resource or self.resource_url}
        self.tokens.insert_many([
            {**base, "hash": _h(access), "kind": "access", "expires_at": int(now + ACCESS_TTL), "created": now},
            {**base, "hash": _h(refresh), "kind": "refresh", "expires_at": int(now + REFRESH_TTL), "created": now},
        ])
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL,
                          scope=" ".join(scopes), refresh_token=refresh)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        d = self.tokens.find_one({"hash": _h(refresh_token), "kind": "refresh", "client_id": client.client_id})
        if not d or d["expires_at"] < _now():
            return None
        return RefreshToken(token=refresh_token, client_id=d["client_id"], scopes=d["scopes"],
                            expires_at=d["expires_at"], resource=d.get("resource"), subject=d["subject"])

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        if self.tokens.delete_one({"hash": _h(refresh_token.token), "kind": "refresh"}).deleted_count != 1:  # rotate
            raise TokenError("invalid_grant", "refresh token already used")
        return self._issue(client.client_id, refresh_token.subject, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        d = self.tokens.find_one({"hash": _h(token), "kind": {"$in": ["access", "personal"]}})
        if not d or (d.get("expires_at") and d["expires_at"] < _now()):
            return None
        return AccessToken(token=token, client_id=d["client_id"], scopes=d["scopes"], expires_at=d.get("expires_at"),
                           resource=d.get("resource"), subject=d["subject"])

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.tokens.delete_one({"hash": _h(token.token)})

    # ---------------------------------------------------------------- admin: link codes and personal tokens
    def issue_link_code(self, user_id: str) -> str:
        return linkcodes.issue(self.db, user_id)

    def redeem_link_code(self, code: str) -> str | None:
        return linkcodes.redeem(self.db, code)

    def issue_personal_token(self, user_id: str, name: str, days: int | None = 365) -> str:
        """Long-lived token for clients that send a fixed header (Claude Code, Cursor, Claude custom connector 'Request headers')."""
        token = "dgk_" + secrets.token_urlsafe(32)
        self.tokens.insert_one({"hash": _h(token), "kind": "personal", "client_id": f"personal:{name}", "subject": user_id,
                                "scopes": [SCOPE], "resource": self.resource_url, "name": name, "created": _now(),
                                "expires_at": int(_now() + days * 86400) if days else None})
        return token

    def list_tokens(self, user_id: str) -> list[dict]:
        return list(self.tokens.find({"subject": user_id, "kind": "personal"}, {"_id": 0, "hash": 0}))

    def revoke_user(self, user_id: str, name: str | None = None) -> int:
        q = {"subject": user_id}
        if name:
            q.update({"kind": "personal", "name": name})
        return self.tokens.delete_many(q).deleted_count


# ---------------------------------------------------------------- login page
PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>득근이 연결</title>
<style>
:root{{--bg:#f6f5f2;--fg:#1d1d1b;--muted:#6b6a65;--card:#fff;--line:#dedbd3;--accent:#1f6f50}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--muted:#a19f97;--card:#1f1f1d;--line:#34332f;--accent:#5fbf95}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}}
main{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px;margin:16px;max-width:380px;width:100%;box-sizing:border-box}}
h1{{font-size:1.25rem;margin:0 0 4px}} p{{color:var(--muted);margin:0 0 18px;font-size:.95rem}}
input{{width:100%;box-sizing:border-box;font:inherit;font-size:1.2rem;letter-spacing:.08em;padding:12px;border:1px solid var(--line);border-radius:10px;background:transparent;color:inherit;text-transform:uppercase}}
button{{margin-top:14px;width:100%;padding:12px;border:0;border-radius:10px;background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}}
.err{{color:#c0392b;margin:10px 0 0;font-size:.9rem}}
h2{{font-size:1rem;margin:22px 0 4px}} .fine{{font-size:.8rem;margin:18px 0 0}}
.secondary{{background:transparent;color:var(--accent);border:1px solid var(--accent)}}
</style></head><body><main>
<h1>득근이 연결</h1>
<p><b>{client}</b>에서 득근이를 쓰려고 해요.</p>
{signup}
<h2>이미 득근이를 쓰고 있어요</h2>
<p>텔레그램 득근이에게 <b>/link</b>, 또는 연결된 다른 앱의 득근이에게 "연결 코드 줘"라고 하면 코드를 받을 수 있어요.</p>
<form method="post" action="/login"><input type="hidden" name="req" value="{req}">
<input name="code" placeholder="XXXX-XXXX-XXXX" autocomplete="one-time-code" required>
<button type="submit">내 기록 불러오기</button></form>{error}
<p class="fine">기록과 대화는 득근이 서버에 저장돼요. 언제든 내려받거나 지울 수 있어요.</p>
</main></body></html>"""


SIGNUP = """<h2>처음이에요</h2>
<form method="post" action="/login"><input type="hidden" name="req" value="{req}"><input type="hidden" name="action" value="signup">
<button type="submit">새로 시작하기</button></form>"""


def login_routes(provider: MongoOAuthProvider):
    def page(p: dict, req: str, error: str = "", status: int = 200) -> HTMLResponse:
        signup = SIGNUP.format(req=html.escape(req)) if provider.allow_signup else ""
        err = f'<p class="err">{html.escape(error)}</p>' if error else ""
        return HTMLResponse(PAGE.format(client=html.escape(p["client_name"]), req=html.escape(req), error=err, signup=signup),
                            status_code=status)

    async def login(request: Request) -> Response:
        if request.method == "GET":
            req = request.query_params.get("req", "")
            p = provider.pending.find_one({"req": req})
            if not p:
                return HTMLResponse("<p>로그인 요청이 없거나 만료됐어요. 앱에서 다시 연결해 주세요.</p>", status_code=400)
            return page(p, req)
        form = parse_qs((await request.body()).decode())
        req = (form.get("req") or [""])[0]
        try:
            if (form.get("action") or [""])[0] == "signup":
                ip = request.client.host if request.client else None
                return RedirectResponse(provider.complete_signup(req, ip), status_code=302)
            return RedirectResponse(provider.complete_login(req, (form.get("code") or [""])[0]), status_code=302)
        except AuthorizeError as e:
            p = provider.pending.find_one({"req": req})
            if not p:
                return HTMLResponse(f"<p>{html.escape(e.error_description or '')}</p>", status_code=400)
            return page(p, req, e.error_description or "", 400)
    return login
