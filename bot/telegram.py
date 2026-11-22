"""Minimal Telegram Bot API client (standard library only)."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("deukgeuni.telegram")
MAX_LEN = 4000  # Telegram limit is 4096 characters per message


class Telegram:
    def __init__(self, token: str, base: str = "https://api.telegram.org"):
        self.url = f"{base}/bot{token}/"

    def call(self, method: str, params: dict | None = None, http_timeout: float = 30) -> dict:
        req = urllib.request.Request(self.url + method, data=json.dumps(params or {}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            data = json.loads(e.read() or b"{}") or {"ok": False, "description": str(e)}
        except (urllib.error.URLError, TimeoutError, OSError) as e:  # network hiccup: caller decides to retry
            data = {"ok": False, "description": f"network: {e}"}
        if not data.get("ok"):
            log.warning("telegram %s failed: %s", method, data.get("description"))
        return data

    def send(self, chat_id: int, text: str) -> None:
        for chunk in split(text):
            self.call("sendMessage", {"chat_id": chat_id, "text": chunk, "link_preview_options": {"is_disabled": True}})

    def send_document(self, chat_id: int, filename: str, data: bytes, caption: str = "") -> dict:
        import uuid
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (("chat_id", str(chat_id)), ("caption", caption)):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(self.url + "sendDocument", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log.warning("sendDocument failed: %s", e)
            return {"ok": False}

    def typing(self, chat_id: int) -> None:
        self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"}, http_timeout=10)

    def set_webhook(self, url: str, secret: str) -> dict:
        return self.call("setWebhook", {"url": url, "secret_token": secret, "allowed_updates": ["message"]})

    def delete_webhook(self) -> dict:
        return self.call("deleteWebhook", {})

    def get_updates(self, offset: int, timeout: int = 50) -> list[dict]:
        return self.call("getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
                         http_timeout=timeout + 10).get("result", [])


def split(text: str, n: int = MAX_LEN) -> list[str]:
    """Split on line breaks so messages stay readable."""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > n:
            out.append(cur + line[: n - len(cur)]) if cur else out.append(line[:n])
            line = line[n - len(cur):] if cur else line[n:]
            cur = ""
        if len(cur) + len(line) > n:
            out.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        out.append(cur)
    return out or [text[:n]]
