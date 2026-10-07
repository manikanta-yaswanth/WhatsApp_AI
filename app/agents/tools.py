"""Approved, parameterized tools the agents may call. The LLM never writes SQL."""

import json
import uuid
from datetime import timedelta
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.db.models import Contact, Conversation, Message
from app.db.repositories.contact_repository import ContactRepository
from app.db.repositories.message_repository import MessageRepository
from app.db.repositories.quality_repository import QualityRepository
from app.db.repositories.scrape_repository import ScrapeRepository
from app.llm import prompts
from app.llm.structured import ainvoke_structured
from app.schemas.agent import ConversationClassification
from app.utils.phone import region_of
from app.utils.time import utcnow


def _contact_dict(c: Contact, **extra: Any) -> dict[str, Any]:
    return {"contact_id": str(c.id), "name": c.contact_name, "phone": c.phone_number, "is_group": c.is_group, **extra}


def _message_dict(m: Message) -> dict[str, Any]:
    return {
        "from": m.sender_type,
        "sender_name": m.sender_name,
        "type": m.message_type,
        "text": m.message_text,
        "at": m.message_timestamp.isoformat(),
    }


def _dump(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


def format_conversation(contact: Contact, messages: list[Message]) -> str:
    lines = [f"Contact: {contact.contact_name or 'unknown'}"]
    for m in messages:
        lines.append(
            f"[{m.message_timestamp:%Y-%m-%d %H:%M}] {m.sender_type}: {m.message_text or '<' + m.message_type + '>'}"
        )
    return "\n".join(lines)


async def classify_contact(llm: BaseChatModel, contact: Contact, messages: list[Message]) -> ConversationClassification:
    return await ainvoke_structured(
        llm,
        ConversationClassification,
        [SystemMessage(prompts.CLASSIFY), HumanMessage(format_conversation(contact, messages))],
    )


def build_tools(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, llm: BaseChatModel | None = None
) -> dict[str, BaseTool]:
    keep = settings.max_messages_per_contact

    @tool
    async def search_contacts(
        name: str | None = None,
        phone_prefix: str | None = None,
        has_phone: bool | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> str:
        """Search contacts. name: case-insensitive substring. phone_prefix: country/area prefix such as '+1' or '+91'.
        has_phone: true/false to filter contacts with/without a phone number. category: stored conversation
        category (meeting_request, job_opportunity, personal, follow_up, urgent, sales, support, spam, unknown)."""
        async with session_factory() as s:
            total, rows = await ContactRepository(s).search(
                name=name, phone_prefix=phone_prefix, has_phone=has_phone, category=category, limit=min(limit, 100)
            )
        return _dump(
            {
                "total_matches": total,
                "contacts": [
                    _contact_dict(c, message_count=n, last_message_at=last, category=cat) for c, n, last, cat in rows
                ],
            }
        )

    @tool
    async def get_contact(contact: str) -> str:
        """Get one contact by contact_id, name or phone, including its conversation classification."""
        async with session_factory() as s:
            repo = ContactRepository(s)
            c = await repo.find_by_name_or_id(contact)
            if c is None:
                return _dump({"error": "contact not found"})
            conv = await repo.get_conversation(c.id)
        return _dump(
            _contact_dict(
                c,
                region=region_of(c.phone_number),
                unread_count=conv.unread_count if conv else None,
                last_message_at=conv.last_message_at if conv else None,
                category=conv.category if conv else None,
                action=conv.action if conv else None,
            )
        )

    @tool
    async def get_recent_messages(contact: str, limit: int = 3) -> str:
        """Get the newest messages (max 3 stored) for a contact given its contact_id, name or phone."""
        async with session_factory() as s:
            c = await ContactRepository(s).find_by_name_or_id(contact)
            if c is None:
                return _dump({"error": "contact not found"})
            msgs = await MessageRepository(s).recent_for_contact(c.id, min(limit, keep))
        return _dump({"contact": _contact_dict(c), "messages": [_message_dict(m) for m in msgs]})

    @tool
    async def get_recent_conversations(days: int = 7, limit: int = 30) -> str:
        """List contacts with activity in the last `days` days, newest first, each with their recent messages."""
        async with session_factory() as s:
            convs = await MessageRepository(s).recent_conversations(
                since=utcnow() - timedelta(days=days), limit=min(limit, 100), per_contact=keep
            )
        return _dump(
            {
                "conversations": [
                    {"contact": _contact_dict(c), "messages": [_message_dict(m) for m in msgs]} for c, msgs in convs
                ]
            }
        )

    @tool
    async def search_messages(query: str, days: int | None = None, limit: int = 20) -> str:
        """Keyword search over stored message text (any word matches). Optionally restrict to the last `days` days."""
        async with session_factory() as s:
            rows = await MessageRepository(s).search_text(query, limit=min(limit, 100), days=days)
        return _dump({"matches": [{"contact": _contact_dict(c), "message": _message_dict(m)} for m, c in rows]})

    @tool
    async def count_contacts(by_country: bool = False) -> str:
        """Count stored contacts (individuals vs groups). by_country=true adds a breakdown by phone country code."""
        async with session_factory() as s:
            repo = ContactRepository(s)
            result: dict[str, Any] = {
                "total": await repo.count(),
                "individuals": await repo.count(is_group=False),
                "groups": await repo.count(is_group=True),
            }
            if by_country:
                phones = (
                    await s.execute(select(Contact.phone_number).where(Contact.phone_number.is_not(None)))
                ).scalars()
                breakdown: dict[str, int] = {}
                for p in phones:
                    region = region_of(p) or "unknown"
                    breakdown[region] = breakdown.get(region, 0) + 1
                result["by_country"] = dict(sorted(breakdown.items(), key=lambda kv: -kv[1]))
        return _dump(result)

    @tool
    async def get_message_stats(days: int = 7) -> str:
        """Activity statistics: stored messages, messages in the last `days` days, most active contacts,
        category distribution and the latest scrape runs."""
        since = utcnow() - timedelta(days=days)
        async with session_factory() as s:
            msgs = MessageRepository(s)
            top = (
                await s.execute(
                    select(
                        Contact.contact_name,
                        func.count(Message.id).label("n"),
                        func.max(Message.message_timestamp).label("last"),
                    )
                    .join(Message, Message.contact_id == Contact.id)
                    .where(Message.message_timestamp >= since)
                    .group_by(Contact.id)
                    .order_by(func.max(Message.message_timestamp).desc())
                    .limit(10)
                )
            ).all()
            cats = (
                await s.execute(
                    select(Conversation.category, func.count())
                    .where(Conversation.category.is_not(None))
                    .group_by(Conversation.category)
                )
            ).all()
            runs = await ScrapeRepository(s).latest(5)
            return _dump(
                {
                    "messages_stored": await msgs.count(),
                    f"messages_last_{days}_days": await msgs.count(since=since),
                    "most_recent_active_contacts": [
                        {"name": n, "messages": c, "last_message_at": last} for n, c, last in top
                    ],
                    "categories": {c: n for c, n in cats},
                    "recent_scrape_runs": [
                        {
                            "status": r.status,
                            "completed_at": r.completed_at,
                            "contacts_found": r.contacts_found,
                            "messages_saved": r.messages_saved,
                        }
                        for r in runs
                    ],
                }
            )

    @tool
    async def data_quality_report() -> str:
        """Run deterministic data quality checks (missing names/phones, invalid phones, duplicates,
        invalid timestamps, contacts over the message limit) and return a quality score."""
        async with session_factory() as s:
            return _dump(await QualityRepository(s).report(keep))

    tools: dict[str, BaseTool] = {
        t.name: t
        for t in [
            search_contacts,
            get_contact,
            get_recent_messages,
            get_recent_conversations,
            search_messages,
            count_contacts,
            get_message_stats,
            data_quality_report,
        ]
    }

    if llm is not None:

        @tool
        async def classify_recent_conversations(days: int = 7, limit: int = 20, only_unclassified: bool = True) -> str:
            """Classify recent conversations into categories (meeting_request, job_opportunity, follow_up, ...)
            with an action flag, store the result, and return it."""
            async with session_factory() as s:
                convs = await MessageRepository(s).recent_conversations(
                    since=utcnow() - timedelta(days=days), limit=min(limit, 50), per_contact=keep
                )
                repo = ContactRepository(s)
                out = []
                for c, msgs in convs:
                    conv = await repo.get_conversation(c.id)
                    if (
                        only_unclassified
                        and conv
                        and conv.classified_at
                        and conv.last_message_at
                        and conv.classified_at >= conv.last_message_at
                        and conv.category
                    ):
                        out.append(
                            {
                                "contact": _contact_dict(c),
                                "category": conv.category,
                                "action": conv.action,
                                "confidence": conv.category_confidence,
                                "reason": conv.category_reason,
                            }
                        )
                        continue
                    r = await classify_contact(llm, c, msgs)
                    await repo.set_classification(c.id, r.category, r.action, r.confidence, r.reason)
                    out.append({"contact": _contact_dict(c), **r.model_dump()})
                await s.commit()
            return _dump({"classified": out})

        tools[classify_recent_conversations.name] = classify_recent_conversations

    return tools


AGENT_TOOLSETS: dict[str, list[str]] = {
    "contact": ["search_contacts", "get_contact", "count_contacts", "get_recent_messages"],
    "message": ["search_contacts", "get_contact", "get_recent_messages", "get_recent_conversations", "search_messages"],
    "classification": [
        "classify_recent_conversations",
        "search_contacts",
        "get_recent_conversations",
        "get_recent_messages",
    ],
    "search": ["search_messages", "search_contacts", "get_recent_messages", "get_recent_conversations"],
    "analytics": ["get_message_stats", "count_contacts", "search_contacts", "data_quality_report"],
    "data_quality": ["data_quality_report", "count_contacts", "search_contacts"],
}


def parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
