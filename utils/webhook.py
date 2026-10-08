"""Optional local webhook ingress; no FastAPI or arbitrary SQL."""

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import psycopg2
from pydantic import ValidationError

from Models.schema import WebhookPayload
from utils.database import DatabaseUtil
from utils.feed_db import persist_conversations
from utils.settings import Settings

MAX_BODY_BYTES = 2 * 1024 * 1024


def token_value(settings: Settings) -> str:
    token = settings.webhook_token.get_secret_value() if settings.webhook_token else ""
    if len(token) < 32:
        raise ValueError("Set WEBHOOK_TOKEN to a random secret of at least 32 characters")
    return token


def create_server(host: str, port: int, settings: Settings, database: DatabaseUtil) -> ThreadingHTTPServer:
    token = token_value(settings)

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def respond(self, status: int, body: dict) -> None:
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self) -> None:
            if self.path != "/webhook":
                self.respond(404, {"error": "not found"})
                return
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
                self.respond(401, {"error": "unauthorized"})
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self.respond(415, {"error": "use application/json"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = 0
            if size < 1 or size > MAX_BODY_BYTES or self.headers.get("Transfer-Encoding"):
                self.respond(413, {"error": "body must be 1 byte to 2 MiB, with Content-Length"})
                return
            try:
                payload = WebhookPayload.model_validate_json(self.rfile.read(size))
            except ValidationError:
                self.respond(422, {"error": "invalid conversation payload"})
                return
            try:
                with database.transaction() as session:
                    stats = persist_conversations(session, payload.conversations, settings.max_messages_per_contact)
            except psycopg2.Error:
                self.respond(503, {"error": "database unavailable; retry later"})
                return
            self.respond(200, {"status": "accepted", **stats})

    return ThreadingHTTPServer((host, port), Handler)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def deliver(url: str, payload: WebhookPayload, settings: Settings) -> dict:
    target = urlsplit(url)
    if target.scheme not in {"http", "https"} or not target.hostname or target.username or target.password:
        raise ValueError("Use an HTTP(S) webhook URL without embedded credentials")
    if target.scheme == "http" and target.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use HTTPS for non-local webhook delivery")
    body = payload.model_dump_json().encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("Webhook batch exceeds 2 MiB; reduce MAX_CHATS_PER_SCRAPE")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token_value(settings)}", "Content-Type": "application/json"},
    )
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        return json.load(response)
