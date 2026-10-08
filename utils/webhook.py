"""Optional local webhook ingress; no FastAPI or arbitrary SQL."""

import hmac
import ipaddress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import psycopg2
from pydantic import ValidationError

from Models.schema import ScrapedConversation, WebhookPayload
from utils.database import DatabaseUtil
from utils.feed_db import persist_conversations
from utils.settings import Settings

MAX_BODY_BYTES = 2 * 1024 * 1024


class WebhookDeliveryError(RuntimeError):
    pass


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
    try:
        loopback = ipaddress.ip_address(target.hostname).is_loopback
    except ValueError:
        loopback = False
    if target.scheme == "http" and not loopback:
        raise ValueError("Use HTTPS except for a literal loopback IP")
    body = payload.model_dump_json().encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("Webhook batch exceeds 2 MiB; reduce MAX_CHATS_PER_SCRAPE")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token_value(settings)}", "Content-Type": "application/json"},
    )
    handlers = [NoRedirect(), ProxyHandler({})] if loopback else [NoRedirect()]
    with build_opener(*handlers).open(request, timeout=30) as response:
        return json.load(response)


def batches(conversations: list[ScrapedConversation]) -> list[WebhookPayload]:
    result = []
    pending: list[ScrapedConversation] = []
    size = len(b'{"conversations":[]}')
    for conv in conversations:
        encoded_size = len(conv.model_dump_json().encode("utf-8"))
        if encoded_size + len(b'{"conversations":[]}') > MAX_BODY_BYTES:
            raise ValueError("A conversation exceeds the webhook body limit; reduce retained messages or message size")
        if pending and (len(pending) == 500 or size + encoded_size + 1 > MAX_BODY_BYTES):
            result.append(WebhookPayload(conversations=pending))
            pending, size = [], len(b'{"conversations":[]}')
        size += encoded_size + int(bool(pending))
        pending.append(conv)
    if pending:
        result.append(WebhookPayload(conversations=pending))
    return result


def deliver_conversations(url: str, conversations: list[ScrapedConversation], settings: Settings) -> dict:
    payloads = batches(conversations)
    stats = {"batches_delivered": 0, "contacts_saved": 0, "messages_saved": 0, "messages_pruned": 0}
    for index, payload in enumerate(payloads):
        try:
            response = deliver(url, payload, settings)
            if response.get("status") != "accepted":
                raise WebhookDeliveryError("Receiver did not acknowledge delivery")
        except (URLError, OSError, json.JSONDecodeError, WebhookDeliveryError) as exc:
            raise WebhookDeliveryError(
                f"Webhook failed after {index} of {len(payloads)} batches; retry the scrape safely (IDs deduplicate)"
            ) from exc
        stats["batches_delivered"] += 1
        for key in ("contacts_saved", "messages_saved", "messages_pruned"):
            stats[key] += response.get(key, 0)
    return {"status": "accepted", **stats}
