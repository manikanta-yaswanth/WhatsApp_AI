from datetime import UTC, datetime

from scraper.parser import parse_dom_payload, parse_pre_plain_text, parse_store_payload
from utils.phone import normalize_phone, phone_from_whatsapp_id


def _store_chat(**over):  # type: ignore[no-untyped-def]
    chat = {
        "id": "14155550123@c.us",
        "name": "John Smith",
        "phone": None,
        "isGroup": False,
        "unreadCount": 2,
        "t": 1791400000,
        "messages": [
            {"id": "m1", "fromMe": False, "type": "chat", "body": "Hi", "t": 1791399000},
            {"id": "m2", "fromMe": True, "type": "chat", "body": "Hello", "t": 1791399100},
            {"id": "m3", "fromMe": False, "type": "image", "caption": "pic", "t": 1791399200},
            {"id": "m4", "fromMe": False, "type": "chat", "body": "Meeting tomorrow?", "t": 1791399300},
            {"id": "m4", "fromMe": False, "type": "chat", "body": "Meeting tomorrow?", "t": 1791399300},
        ],
    }
    chat.update(over)
    return chat


def test_store_chat_parsed_newest_first_limited_and_deduped() -> None:
    result = parse_store_payload([_store_chat()], limit=3)
    assert result.invalid == 0
    conv = result.conversations[0]
    assert conv.contact.phone_number == "+14155550123"
    assert conv.contact.contact_name == "John Smith"
    assert [m.whatsapp_message_id for m in conv.messages] == ["m4", "m3", "m2"]
    assert conv.messages[1].message_type == "image" and conv.messages[1].message_text == "pic"
    assert conv.messages[2].sender_type == "me"
    assert conv.unread_count == 2


def test_lid_chat_uses_phone_field_and_invalid_rows_counted() -> None:
    lid = _store_chat(id="123456789@lid", phone="919876543210", messages=[])
    broken = {"name": "no id"}
    result = parse_store_payload([lid, broken], limit=3)
    assert result.invalid == 1
    assert result.conversations[0].contact.phone_number == "+919876543210"


def test_group_messages_marked_group_member() -> None:
    chat = _store_chat(
        id="123-456@g.us",
        isGroup=True,
        messages=[
            {"id": "g1", "fromMe": False, "type": "chat", "body": "x", "t": 1791399000, "author": "14155550123@c.us"}
        ],
    )
    conv = parse_store_payload([chat], 3).conversations[0]
    assert conv.contact.is_group and conv.contact.phone_number is None
    assert conv.messages[0].sender_type == "group_member"


def test_pre_plain_text_formats() -> None:
    ts, sender = parse_pre_plain_text("[10:21, 07/10/2026] John Smith: ")  # type: ignore[misc]
    assert ts == datetime(2026, 10, 7, 10, 21, tzinfo=UTC) and sender == "John Smith"
    ts2, _ = parse_pre_plain_text("[9:05 PM, 10/7/2026] Sarah: ")  # type: ignore[misc]
    assert ts2.hour == 21
    assert parse_pre_plain_text("garbage") is None


def test_dom_payload_generates_stable_ids() -> None:
    chat = {"name": "Sarah", "messages": [{"prePlainText": "[10:21, 07/10/2026] Sarah: ", "text": "hey"}]}
    a = parse_dom_payload([chat], 3).conversations[0]
    b = parse_dom_payload([chat], 3).conversations[0]
    assert a.messages[0].whatsapp_message_id == b.messages[0].whatsapp_message_id
    assert a.messages[0].whatsapp_message_id.startswith("dom_")


def test_phone_helpers() -> None:
    assert normalize_phone("+1 (415) 555-0123") == "+14155550123"
    assert normalize_phone("123") is None
    assert phone_from_whatsapp_id("14155550123@c.us") == "+14155550123"
    assert phone_from_whatsapp_id("123@lid") is None
