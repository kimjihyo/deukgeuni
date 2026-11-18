"""Bot configuration from environment variables (see deploy/.env.example).
Secrets live only in the environment, never in the repo."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


@dataclass
class Config:
    telegram_token: str = ""
    webhook_secret: str = ""
    public_url: str = ""                 # https://bot.example.com  (webhook mode)
    listen_host: str = "127.0.0.1"
    listen_port: int = 8080
    allowed_ids: set[int] = field(default_factory=set)
    llm_provider: str = "anthropic"      # anthropic | openai
    llm_model: str = ""
    max_tokens: int = 1500
    mongo_uri: str = ""
    mongo_db: str = "deukgeuni"
    history_turns: int = 20              # user+assistant messages kept as conversation context
    max_tool_rounds: int = 10
    timezone: str = "Asia/Seoul"

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        c = cls(
            telegram_token=e("TELEGRAM_BOT_TOKEN", ""),
            webhook_secret=e("TELEGRAM_WEBHOOK_SECRET", ""),
            public_url=e("PUBLIC_URL", "").rstrip("/"),
            listen_host=e("LISTEN_HOST", "127.0.0.1"),
            listen_port=int(e("LISTEN_PORT", "8080")),
            allowed_ids=_ids(e("ALLOWED_TELEGRAM_IDS", "")),
            llm_provider=e("LLM_PROVIDER", "anthropic"),
            llm_model=e("LLM_MODEL", ""),
            max_tokens=int(e("LLM_MAX_TOKENS", "1500")),
            mongo_uri=e("WL_MONGO_URI", ""),
            mongo_db=e("WL_MONGO_DB", "deukgeuni"),
            history_turns=int(e("HISTORY_TURNS", "20")),
            max_tool_rounds=int(e("MAX_TOOL_ROUNDS", "10")),
            timezone=e("TIMEZONE", "Asia/Seoul"),
        )
        if not c.llm_model and c.llm_provider == "anthropic":
            c.llm_model = "claude-sonnet-5-5"
        return c

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            env = {"telegram_token": "TELEGRAM_BOT_TOKEN", "webhook_secret": "TELEGRAM_WEBHOOK_SECRET",
                   "public_url": "PUBLIC_URL", "mongo_uri": "WL_MONGO_URI", "llm_model": "LLM_MODEL",
                   "allowed_ids": "ALLOWED_TELEGRAM_IDS"}
            raise SystemExit("환경변수 필요: " + ", ".join(env.get(n, n) for n in missing))
