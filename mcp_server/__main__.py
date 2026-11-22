"""득근이 MCP server.

  python -m mcp_server serve                         Streamable HTTP + OAuth (MCP_LISTEN_HOST:MCP_LISTEN_PORT, 앞단에 TLS 프록시)
  python -m mcp_server issue-code --telegram-id 123  앱 연결용 일회용 코드 (15분)
  python -m mcp_server issue-token --telegram-id 123 --name laptop [--days 365]
                                                     고정 헤더용 개인 토큰 (Claude Code, Cursor 등)
  python -m mcp_server list-tokens --telegram-id 123
  python -m mcp_server revoke --telegram-id 123 [--name laptop]   (이름 없으면 이 사용자의 모든 토큰·세션 해제)

환경변수: PUBLIC_URL, WL_MONGO_URI, WL_MONGO_DB, MCP_LISTEN_HOST(127.0.0.1), MCP_LISTEN_PORT(8081), MCP_PATH(/mcp), TIMEZONE
"""
from __future__ import annotations

import argparse
import os


def config() -> dict:
    e = os.environ.get
    public = e("PUBLIC_URL", "")
    if not public.startswith("https://") and not public.startswith("http://127.0.0.1") and not public.startswith("http://localhost"):
        raise SystemExit("PUBLIC_URL 필요 (https://…)")
    return {"public_url": public.rstrip("/"), "mcp_path": e("MCP_PATH", "/mcp"), "timezone": e("TIMEZONE", "Asia/Seoul"),
            "listen_host": e("MCP_LISTEN_HOST", "127.0.0.1"), "listen_port": int(e("MCP_LISTEN_PORT", "8081"))}


def connect():
    from pymongo import MongoClient
    uri = os.environ.get("WL_MONGO_URI")
    if not uri:
        raise SystemExit("WL_MONGO_URI 필요")
    client = MongoClient(uri, appname="deukgeuni-mcp")
    return client, client[os.environ.get("WL_MONGO_DB", "deukgeuni")]


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m mcp_server", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("serve")
    for name in ("issue-code", "issue-token", "list-tokens", "revoke"):
        x = sp.add_parser(name)
        x.add_argument("--telegram-id", type=int, required=True)
        if name in ("issue-token", "revoke"):
            x.add_argument("--name", required=(name == "issue-token"))
        if name == "issue-token":
            x.add_argument("--days", type=int, default=365)
    a = p.parse_args(argv)

    cfg = config()
    client, db = connect()
    from .server import asgi_app, build
    mcp = build(cfg, db, client)
    uid = f"tg:{a.telegram_id}" if hasattr(a, "telegram_id") else None

    if a.cmd == "serve":
        import uvicorn
        uvicorn.run(asgi_app(mcp, cfg), host=cfg["listen_host"], port=cfg["listen_port"], proxy_headers=True,
                    forwarded_allow_ips="127.0.0.1")
    elif a.cmd == "issue-code":
        print(mcp.provider.issue_link_code(uid), "(15분 안에 앱의 득근이 연결 화면에 입력)")
    elif a.cmd == "issue-token":
        print(mcp.provider.issue_personal_token(uid, a.name, a.days))
        print("이 토큰은 다시 볼 수 없어요. 클라이언트에 'Authorization: Bearer <토큰>' 헤더로 넣으세요.")
    elif a.cmd == "list-tokens":
        for t in mcp.provider.list_tokens(uid):
            print(t.get("name"), "만료:", t.get("expires_at"))
    elif a.cmd == "revoke":
        print(mcp.provider.revoke_user(uid, a.name), "개 해제")


if __name__ == "__main__":
    main()
