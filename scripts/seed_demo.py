"""Load a few fake contacts/messages so the API and agents can be tried without a WhatsApp login.

Usage: uv run python scripts/seed_demo.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import UTC, datetime, timedelta

from app.db.database import get_session_factory
from app.schemas.contact import ContactIn
from app.schemas.conversation import ScrapedConversation
from app.schemas.message import MessageIn
from app.services.scrape_service import persist_conversations

now = datetime.now(UTC)


def conv(wid, name, phone, msgs):
    return ScrapedConversation(
        contact=ContactIn(whatsapp_id=wid, contact_name=name, phone_number=phone),
        messages=[
            MessageIn(
                whatsapp_message_id=f"{wid}-{i}",
                sender_type=s,
                message_text=t,
                message_timestamp=now - timedelta(hours=h),
            )
            for i, (s, t, h) in enumerate(msgs)
        ],
    )


data = [
    conv(
        "14155550101@c.us",
        "Alice Recruiter",
        "+14155550101",
        [
            ("contact", "Hi! Are you free for an interview Thursday at 3pm?", 2),
            ("me", "Sure, sounds good", 1),
            ("contact", "Great, sending the invite now", 0.5),
        ],
    ),
    conv(
        "919876543210@c.us", "Ravi", "+919876543210", [("contact", "Can we meet tomorrow to discuss the project?", 5)]
    ),
    conv("84213@lid", None, None, [("contact", "hello", 30)]),
    conv(
        "447700900123@c.us",
        "Mom",
        "+447700900123",
        [("contact", "Call me when you get a chance", 3), ("contact", "Dinner on Sunday?", 2.5)],
    ),
]


async def main():
    async with get_session_factory()() as s:
        print(await persist_conversations(s, data, keep=3))
        await s.commit()


asyncio.run(main())
