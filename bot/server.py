"""Transports: webhook (HTTP server behind a TLS reverse proxy such as Caddy) and long polling."""
from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("deukgeuni.server")
WEBHOOK_PATH = "/telegram/webhook"


def serve_webhook(app, host: str, port: int, secret: str) -> None:
    """Telegram POSTs updates here. We answer 200 at once and process in a background thread,
    because Telegram retries if the response is slow (LLM calls take seconds)."""

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: bytes = b"ok"):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # health check for the proxy / monitoring
            self._reply(200 if self.path == "/healthz" else 404, b"ok" if self.path == "/healthz" else b"not found")

        def do_POST(self):
            if self.path != WEBHOOK_PATH:
                return self._reply(404, b"not found")
            got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not secret or not hmac.compare_digest(got, secret):
                log.warning("webhook: bad secret from %s", self.client_address[0])
                return self._reply(401, b"unauthorized")
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                return self._reply(413, b"too large")
            try:
                update = json.loads(self.rfile.read(length))
            except ValueError:
                return self._reply(400, b"bad json")
            self._reply(200)
            threading.Thread(target=app.handle_update, args=(update,), daemon=True).start()

        def log_message(self, fmt, *args):  # route http.server logs to logging, without update bodies
            log.debug("%s - %s", self.client_address[0], fmt % args)

    httpd = ThreadingHTTPServer((host, port), Handler)
    log.info("webhook server on http://%s:%d%s", host, port, WEBHOOK_PATH)
    httpd.serve_forever()


def poll(app, tg) -> None:
    """Long polling (no public URL needed). Remove any webhook first."""
    tg.delete_webhook()
    offset = 0
    log.info("long polling started")
    while True:
        updates = tg.get_updates(offset)
        if not updates:
            time.sleep(1)  # also covers network errors (get_updates returns [])
            continue
        for u in updates:
            offset = max(offset, u["update_id"] + 1)
            try:
                app.handle_update(u)
            except Exception:
                log.exception("update failed")
