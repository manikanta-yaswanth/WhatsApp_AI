import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import SecretStr

from Models.schema import WebhookPayload
from tests.factories import conversation
from utils.repositories import ContactRepository, MessageRepository
from utils.webhook import MAX_BODY_BYTES, create_server, deliver


@pytest.fixture
def receiver(database, settings):  # type: ignore[no-untyped-def]
    configured = settings.model_copy(update={"webhook_token": SecretStr("test-token-not-a-real-secret-" + "x" * 32)})
    server = create_server("127.0.0.1", 0, configured, database)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/webhook", configured
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_webhook_delivers_validated_data_and_deduplicates(receiver, database) -> None:  # type: ignore[no-untyped-def]
    url, settings = receiver
    payload = WebhookPayload(conversations=[conversation("1@c.us", "John", None, ["a", "b", "c", "d"])])
    first = deliver(url, payload, settings)
    assert first["status"] == "accepted" and first["contacts_saved"] == 1 and first["messages_saved"] == 3
    second = deliver(url, payload, settings)
    assert second["contacts_saved"] == 0 and second["messages_saved"] == 0
    with database.transaction() as s:
        assert ContactRepository(s).count() == 1
        cid = ContactRepository(s).search()[1][0][0].id
        assert [m.message_text for m in MessageRepository(s).recent_for_contact(cid)] == ["d", "c", "b"]


@pytest.mark.parametrize(
    "kind,status", [("no-auth", 401), ("wrong-path", 404), ("content-type", 415), ("invalid", 422), ("too-large", 413)]
)
def test_webhook_rejects_invalid_requests_without_writes(receiver, database, kind, status) -> None:  # type: ignore[no-untyped-def]
    url, settings = receiver
    headers = {
        "Authorization": "Bearer " + settings.webhook_token.get_secret_value(),
        "Content-Type": "application/json",
    }
    body = b'{"conversations":[]}'
    if kind == "no-auth":
        headers.pop("Authorization")
    elif kind == "wrong-path":
        url += "/wrong"
    elif kind == "content-type":
        headers["Content-Type"] = "text/plain"
    elif kind == "too-large":
        headers["Content-Length"] = str(MAX_BODY_BYTES + 1)
    request = Request(url, data=body, headers=headers, method="POST")
    with pytest.raises(HTTPError) as error:
        urlopen(request, timeout=3)
    assert error.value.code == status
    assert "error" in json.load(error.value)
    with database.transaction() as s:
        assert ContactRepository(s).count() == 0


def test_webhook_rolls_back_whole_batch_on_storage_failure(receiver, database, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    url, settings = receiver
    original = MessageRepository.insert_new
    calls = 0

    def insert(self, cid, messages):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 2:
            self.session.execute("SELECT * FROM nonexistent_test_table")
        return original(self, cid, messages)

    monkeypatch.setattr(MessageRepository, "insert_new", insert)
    payload = WebhookPayload(
        conversations=[conversation("1@c.us", "A", None, ["a"]), conversation("2@c.us", "B", None, ["b"])]
    )
    with pytest.raises(HTTPError) as error:
        deliver(url, payload, settings)
    assert error.value.code == 503
    with database.transaction() as s:
        assert ContactRepository(s).count() == 0 and MessageRepository(s).count() == 0


def test_webhook_requires_strong_token(database, settings) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="WEBHOOK_TOKEN"):
        create_server("127.0.0.1", 0, settings, database)


def test_delivery_refuses_plain_http_nonlocal_and_credentials(settings) -> None:  # type: ignore[no-untyped-def]
    payload = WebhookPayload(conversations=[conversation("1@c.us", "A", None, ["a"])])
    with pytest.raises(ValueError, match="HTTPS"):
        deliver("http://example.com/webhook", payload, settings)
    with pytest.raises(ValueError, match="embedded credentials"):
        deliver("https://user:password@example.com/webhook", payload, settings)


def test_delivery_does_not_forward_auth_on_redirect(receiver) -> None:  # type: ignore[no-untyped-def]
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(302)
            self.send_header("Location", receiver[0])
            self.end_headers()

        def log_message(self, format: str, *args) -> None:  # type: ignore[no-untyped-def]
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Redirect) as redirect:
        thread = threading.Thread(target=redirect.serve_forever, daemon=True)
        thread.start()
        try:
            payload = WebhookPayload(conversations=[conversation("1@c.us", "A", None, ["a"])])
            with pytest.raises(HTTPError) as error:
                deliver(f"http://127.0.0.1:{redirect.server_port}/webhook", payload, receiver[1])
            assert error.value.code == 302
        finally:
            redirect.shutdown()
            thread.join(timeout=5)
