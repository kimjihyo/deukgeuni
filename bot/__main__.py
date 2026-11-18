"""득근이 Telegram bot.

  python -m bot serve                      웹훅 서버 (PUBLIC_URL 앞단에 TLS 프록시, 예: Caddy)
  python -m bot set-webhook                텔레그램에 웹훅 주소 등록 (PUBLIC_URL + /telegram/webhook)
  python -m bot poll                       롱 폴링 (공개 주소 없이 테스트할 때)
  python -m bot import-user --telegram-id 123 --from ../workout-log [--yes]
                                           파일 데이터 폴더 → MongoDB 사용자로 옮기기
  python -m bot backup --telegram-id 123 --to ../workout-log
                                           MongoDB 사용자 → 파일 데이터 폴더 (csv + 문서). git 커밋은 deploy/backup.sh
  python -m bot remind --telegram-id 123 --message "오늘 운동 안내"
                                           득근이가 먼저 메시지 보내기 (cron)

환경변수: deploy/.env.example
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import REPO, Config

sys.path.insert(0, str(REPO / "scripts"))
from wlcore.store import Store  # noqa: E402


def connect(cfg: Config):
    cfg.require("mongo_uri")
    from pymongo import MongoClient
    client = MongoClient(cfg.mongo_uri, appname="deukgeuni-bot")
    return client, client[cfg.mongo_db]


def build_app(cfg: Config, need_llm: bool = True):
    from .app import BotApp
    from .llm import make_llm
    from .telegram import Telegram
    cfg.require("telegram_token")
    client, db = connect(cfg)
    llm = make_llm(cfg.llm_provider, cfg.llm_model, cfg.max_tokens) if need_llm else None
    return BotApp(cfg, db, llm, Telegram(cfg.telegram_token), mongo_client=client)


def user_store(cfg: Config, tg_id: int):
    from .app import user_key
    client, db = connect(cfg)
    return Store(config={"type": "mongodb", "user": user_key(tg_id), "client": client, "database": db.name})


def copy_all(src: Store, dst: Store) -> str:
    dst.replace_all(src.all())
    docs = src.docs()
    for d in docs:
        dst.put_doc(d, src.doc(d))
    return ", ".join(f"{n} {len(v)}" for n, v in src.all().items()) + f", docs {len(docs)}"


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m bot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("serve")
    sp.add_parser("poll")
    sp.add_parser("set-webhook")
    sp.add_parser("delete-webhook")
    x = sp.add_parser("import-user"); x.add_argument("--telegram-id", type=int, required=True); x.add_argument("--from", dest="src", required=True); x.add_argument("--yes", action="store_true")
    x = sp.add_parser("backup"); x.add_argument("--telegram-id", type=int, required=True); x.add_argument("--to", dest="dst", required=True)
    x = sp.add_parser("remind"); x.add_argument("--telegram-id", type=int, action="append", required=True)
    x.add_argument("--message", default="오늘 운동이 있으면 처방을 짧게 알려 주고, 기록이 비어 있는 지난 세션이 있으면 물어봐 줘. 쉬는 날이면 한 줄만.")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()

    if a.cmd == "serve":
        from .server import serve_webhook
        cfg.require("webhook_secret", "allowed_ids")
        serve_webhook(build_app(cfg), cfg.listen_host, cfg.listen_port, cfg.webhook_secret)
    elif a.cmd == "poll":
        from .server import poll
        cfg.require("allowed_ids")
        app = build_app(cfg)
        poll(app, app.tg)
    elif a.cmd == "set-webhook":
        from .server import WEBHOOK_PATH
        from .telegram import Telegram
        cfg.require("telegram_token", "public_url", "webhook_secret")
        print(Telegram(cfg.telegram_token).set_webhook(cfg.public_url + WEBHOOK_PATH, cfg.webhook_secret))
    elif a.cmd == "delete-webhook":
        from .telegram import Telegram
        cfg.require("telegram_token")
        print(Telegram(cfg.telegram_token).delete_webhook())
    elif a.cmd == "import-user":
        dst = user_store(cfg, a.telegram_id)
        if (dst.rows("sets") or dst.docs()) and not a.yes:
            raise SystemExit("이 사용자에게 이미 데이터가 있음. 덮어쓰려면 --yes")
        print("가져옴:", copy_all(Store(data_dir=a.src), dst))
    elif a.cmd == "backup":
        print("내보냄:", copy_all(user_store(cfg, a.telegram_id), Store(data_dir=a.dst)))
    elif a.cmd == "remind":
        app = build_app(cfg)
        for tg_id in a.telegram_id:
            print(tg_id, app.remind(tg_id, a.message)[:80])


if __name__ == "__main__":
    main()
