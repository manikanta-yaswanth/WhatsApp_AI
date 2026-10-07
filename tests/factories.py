from datetime import UTC, datetime, timedelta

from app.schemas.contact import ContactIn
from app.schemas.conversation import ScrapedConversation
from app.schemas.message import MessageIn

BASE_TIME = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


def conversation(
    wid: str,
    name: str | None,
    phone: str | None,
    texts: list[str],
    start: datetime = BASE_TIME,
    prefix: str | None = None,
) -> ScrapedConversation:
    msgs = [
        MessageIn(
            whatsapp_message_id=f"{prefix or wid}-{i}",
            sender_type="contact" if i % 2 == 0 else "me",
            message_text=t,
            message_timestamp=start + timedelta(minutes=i),
        )
        for i, t in enumerate(texts)
    ]
    return ScrapedConversation(contact=ContactIn(whatsapp_id=wid, contact_name=name, phone_number=phone), messages=msgs)
