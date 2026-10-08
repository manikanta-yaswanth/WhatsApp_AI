import json
import threading
from http.client import HTTPMessage
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import SecretStr

from Models.schema import WebhookPayload
from tests.factories import conversation
from utils import webhook
from utils.repositories import ContactRepository, MessageRepository
from utils.webhook import MAX_BODY_BYTES, WebhookDeliveryError, batches, create_server, deliver, deliver_conversations


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


def test_batches_respect_count_and_encoded_byte_limits(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    convs = [conversation(f"{i}@lid", "Name", None, ["hello"]) for i in range(501)]
    count_batches = batches(convs)
    assert [len(p.conversations) for p in count_batches] == [500, 1]
    monkeypatch.setattr(webhook, "MAX_BODY_BYTES", 2000)
    convs = [conversation(f"{i}@lid", "Name", None, ["你" * 200]) for i in range(5)]
    byte_batches = batches(convs)
    assert len(byte_batches) > 1
    assert sum(len(p.conversations) for p in byte_batches) == 5
    assert all(len(p.model_dump_json().encode()) <= 2000 for p in byte_batches)


def test_oversized_conversation_fails_before_any_requests(settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(webhook, "MAX_BODY_BYTES", 1000)
    monkeypatch.setattr(webhook, "deliver", lambda *args: pytest.fail("Preflight must reject before delivery"))
    convs = [conversation("1@lid", "Name", None, ["small"]), conversation("2@lid", "Name", None, ["x" * 1200])]
    with pytest.raises(ValueError, match="conversation exceeds"):
        deliver_conversations("http://127.0.0.1:8080/webhook", convs, settings)


def test_partial_batch_delivery_reports_retryable_progress(settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = 0

    def send(*args):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 2:
            raise HTTPError("http://127.0.0.1/webhook", 503, "failed", HTTPMessage(), None)
        return {"status": "accepted", "contacts_saved": 500, "messages_saved": 500}

    monkeypatch.setattr(webhook, "deliver", send)
    convs = [conversation(f"{i}@lid", "Name", None, ["a"]) for i in range(501)]
    with pytest.raises(WebhookDeliveryError, match="after 1 of 2 batches"):
        deliver_conversations("http://127.0.0.1:8080/webhook", convs, settings)


def test_loopback_delivery_bypasses_environment_proxy(receiver, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    payload = WebhookPayload(conversations=[conversation("1@lid", "Name", None, ["hello"])])
    assert deliver(receiver[0], payload, receiver[1])["status"] == "accepted"
    with pytest.raises(ValueError, match="literal loopback"):
        deliver("http://localhost/webhook", payload, receiver[1])


@pytest.mark.parametrize("field,value", [("sender_name", "x" * 256), ("message_type", "x" * 31)])
def test_webhook_rejects_db_length_violations_as_validation_errors(receiver, field, value) -> None:  # type: ignore[no-untyped-def]
    url, settings = receiver
    payload = WebhookPayload(conversations=[conversation("1@lid", "Name", None, ["hello"])]).model_dump(mode="json")
    payload["conversations"][0]["messages"][0][field] = value
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + settings.webhook_token.get_secret_value(),
            "Content-Type": "application/json",
        },
    )
    with pytest.raises(HTTPError) as error:
        urlopen(request, timeout=3)
    assert error.value.code == 422
