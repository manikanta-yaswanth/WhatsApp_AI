"""Turn raw extractor payloads (store JS objects or DOM text) into validated Pydantic models.

This is the only module that knows the shape of WhatsApp's raw data, so UI or
store changes are fixed here.
"""

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from Models.schema import ContactIn, MessageIn, ScrapedConversation
from utils.hashing import stable_message_id
from utils.phone import normalize_phone, phone_from_whatsapp_id
from utils.time import from_unix

_MEDIA_TYPES = {"image", "video", "audio", "ptt", "document", "sticker", "location", "vcard", "multi_vcard"}


class ParseResult:
    def __init__(self) -> None:
        self.conversations: list[ScrapedConversation] = []
        self.invalid: int = 0


def _sender_type(raw: dict[str, Any], is_group: bool) -> str:
    if raw.get("fromMe"):
        return "me"
    if raw.get("type") in {"e2e_notification", "notification_template", "gp2", "protocol"}:
        return "system"
    return "group_member" if is_group else "contact"


def parse_store_chat(raw: dict[str, Any], limit: int) -> ScrapedConversation:
    """Parse one chat dict produced by STORE_EXTRACT_JS."""
    wid: str = raw["id"]
    is_group = bool(raw.get("isGroup")) or wid.endswith("@g.us")
    phone = normalize_phone(raw.get("phone")) or phone_from_whatsapp_id(wid)
    contact = ContactIn(
        whatsapp_id=wid,
        contact_name=raw.get("name") or raw.get("pushname"),
        phone_number=phone,
        is_group=is_group,
    )
    messages: list[MessageIn] = []
    for m in raw.get("messages") or []:
        ts = from_unix(m.get("t"))
        if ts is None or not m.get("id"):
            continue
        mtype = m.get("type") or "chat"
        body = m.get("body") if mtype in {"chat", "text"} else (m.get("caption") or None)
        messages.append(
            MessageIn(
                whatsapp_message_id=str(m["id"])[:255],
                sender_type=_sender_type(m, is_group),  # type: ignore[arg-type]
                sender_name=m.get("author") if is_group else None,
                message_type="text" if mtype == "chat" else (mtype if mtype in _MEDIA_TYPES else "other"),
                message_text=body,
                message_timestamp=ts,
            )
        )
    conv = ScrapedConversation(
        contact=contact,
        unread_count=max(0, int(raw.get("unreadCount") or 0)),
        last_message_at=from_unix(raw.get("t")),
        messages=messages,
    )
    conv.messages = conv.newest(limit)
    return conv


def parse_store_payload(chats: list[dict[str, Any]], limit: int) -> ParseResult:
    result = ParseResult()
    for raw in chats:
        try:
            result.conversations.append(parse_store_chat(raw, limit))
        except (ValidationError, KeyError, TypeError, ValueError):
            result.invalid += 1
    return result


# data-pre-plain-text looks like "[10:21, 07/10/2026] John Smith: " (locale dependent).
_PRE_PLAIN = re.compile(r"^\[(?P<time>[^,\]]+),\s*(?P<date>[^\]]+)\]\s*(?P<sender>.*?):\s*$")
_DATE_FORMATS = ["%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%y", "%m/%d/%y"]
_TIME_FORMATS = ["%H:%M", "%I:%M %p", "%I:%M\u202f%p"]


def parse_pre_plain_text(value: str, tz: Any = UTC) -> tuple[datetime, str] | None:
    m = _PRE_PLAIN.match(value.strip() + (" " if not value.endswith(" ") else ""))
    if not m:
        return None
    date_s, time_s = m["date"].strip(), m["time"].strip().upper()
    for df in _DATE_FORMATS:
        for tf in _TIME_FORMATS:
            try:
                dt = datetime.strptime(f"{date_s} {time_s}", f"{df} {tf}")
            except ValueError:
                continue
            return dt.replace(tzinfo=tz), m["sender"].strip()
    return None


def parse_dom_chat(raw: dict[str, Any], limit: int) -> ScrapedConversation:
    """Parse one chat dict produced by the DOM fallback extractor."""
    name: str = (raw.get("name") or "").strip()
    wid = raw.get("id") or f"dom:{name}"
    contact = ContactIn(
        whatsapp_id=wid,
        contact_name=name or None,
        phone_number=normalize_phone(name) or phone_from_whatsapp_id(wid),
        is_group=bool(raw.get("isGroup")),
    )
    messages: list[MessageIn] = []
    for m in raw.get("messages") or []:
        parsed = parse_pre_plain_text(m.get("prePlainText") or "")
        if not parsed:
            continue
        ts, sender = parsed
        text = m.get("text")
        sender_type = "me" if m.get("fromMe") else ("group_member" if contact.is_group else "contact")
        messages.append(
            MessageIn(
                whatsapp_message_id=m.get("id") or stable_message_id(wid, sender, ts.isoformat(), text or ""),
                sender_type=sender_type,  # type: ignore[arg-type]
                sender_name=sender if contact.is_group else None,
                message_text=text,
                message_timestamp=ts,
            )
        )
    conv = ScrapedConversation(contact=contact, unread_count=int(raw.get("unreadCount") or 0), messages=messages)
    conv.messages = conv.newest(limit)
    return conv


def parse_dom_payload(chats: list[dict[str, Any]], limit: int) -> ParseResult:
    result = ParseResult()
    for raw in chats:
        try:
            result.conversations.append(parse_dom_chat(raw, limit))
        except (ValidationError, KeyError, TypeError, ValueError):
            result.invalid += 1
    return result
