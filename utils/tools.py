"""Approved parameterized queries; agents never generate or execute SQL."""

import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool

from Models.records import Contact, Message
from Models.schema import ClassifiedContact, ConversationClassification
from utils import prompts
from utils.database import DatabaseUtil
from utils.phone import region_of
from utils.repositories import ContactRepository, MessageRepository, QualityRepository, RunRepository
from utils.settings import Settings
from utils.structured import ainvoke_structured
from utils.time import utcnow


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


async def classify_conversations(
    database: DatabaseUtil,
    settings: Settings,
    llm: BaseChatModel,
    contact_ids: list[uuid.UUID] | None = None,
    limit: int = 50,
    only_unclassified: bool = False,
    days: int | None = None,
) -> list[ClassifiedContact]:
    def snapshot() -> list[tuple[Contact, list[Message], Any]]:
        with database.transaction() as s:
            contacts, messages = ContactRepository(s), MessageRepository(s)
            if contact_ids:
                pairs = [
                    (c, messages.recent_for_contact(c.id, settings.max_messages_per_contact))
                    for cid in contact_ids
                    if (c := contacts.get(cid)) is not None
                ]
            else:
                since = utcnow() - timedelta(days=days) if days is not None else None
                pairs = messages.recent_conversations(since, limit, settings.max_messages_per_contact)
            return [(c, msgs, contacts.get_conversation(c.id)) for c, msgs in pairs]

    out = []
    for c, msgs, conv in await asyncio.to_thread(snapshot):
        if not msgs:
            continue
        if conv and conv.category and conv.classified_at:
            if not only_unclassified:
                out.append(
                    ClassifiedContact(
                        contact_id=c.id,
                        contact_name=c.contact_name,
                        category=conv.category,
                        action=conv.action,
                        confidence=conv.category_confidence,
                        reason=conv.category_reason,
                    )
                )
            continue
        result = await classify_contact(llm, c, msgs)

        def save(
            contact: Contact = c, classification: ConversationClassification = result, snapshot: list[Message] = msgs
        ) -> bool:
            with database.transaction() as s:
                repo = ContactRepository(s)
                repo.lock(contact.id)
                current = MessageRepository(s).recent_for_contact(contact.id, settings.max_messages_per_contact)
                if [m.id for m in current] != [m.id for m in snapshot]:
                    return False
                repo.set_classification(
                    contact.id,
                    classification.category,
                    classification.action,
                    classification.confidence,
                    classification.reason,
                )
                return True

        if await asyncio.to_thread(save):
            out.append(ClassifiedContact(contact_id=c.id, contact_name=c.contact_name, **result.model_dump()))
    return out


def build_tools(database: DatabaseUtil, settings: Settings, llm: BaseChatModel | None = None) -> dict[str, BaseTool]:
    keep = settings.max_messages_per_contact

    @tool
    def search_contacts(
        name: str | None = None,
        phone_prefix: str | None = None,
        has_phone: bool | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> str:
        """Search contacts by name substring, phone/country prefix, missing phone or stored classification category."""
        with database.transaction() as s:
            total, rows = ContactRepository(s).search(
                name=name,
                phone_prefix=phone_prefix,
                has_phone=has_phone,
                category=category,
                limit=max(1, min(limit, 100)),
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
    def get_contact(contact: str) -> str:
        """Get one contact by UUID, name or phone, with its stored conversation classification."""
        with database.transaction() as s:
            repo = ContactRepository(s)
            c = repo.find_by_name_or_id(contact)
            if c is None:
                return _dump({"error": "contact not found"})
            conv = repo.get_conversation(c.id)
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
    def get_recent_messages(contact: str, limit: int = 3) -> str:
        """Get a contact's newest stored messages by UUID, name or phone."""
        with database.transaction() as s:
            c = ContactRepository(s).find_by_name_or_id(contact)
            if c is None:
                return _dump({"error": "contact not found"})
            msgs = MessageRepository(s).recent_for_contact(c.id, max(1, min(limit, keep)))
        return _dump({"contact": _contact_dict(c), "messages": [_message_dict(m) for m in msgs]})

    @tool
    def get_recent_conversations(days: int = 7, limit: int = 30) -> str:
        """List contacts active in the last days, newest first, with their recent stored messages."""
        with database.transaction() as s:
            pairs = MessageRepository(s).recent_conversations(
                utcnow() - timedelta(days=max(0, days)), max(1, min(limit, 100)), keep
            )
        return _dump(
            {
                "conversations": [
                    {"contact": _contact_dict(c), "messages": [_message_dict(m) for m in msgs]} for c, msgs in pairs
                ]
            }
        )

    @tool
    def search_messages(query: str, days: int | None = None, limit: int = 20) -> str:
        """Keyword search over stored text (any word matches), optionally restricted to the last days."""
        with database.transaction() as s:
            rows = MessageRepository(s).search_text(query, max(1, min(limit, 100)), days)
        return _dump({"matches": [{"contact": _contact_dict(c), "message": _message_dict(m)} for m, c in rows]})

    @tool
    def count_contacts(by_country: bool = False, has_phone: bool | None = None, phone_prefix: str | None = None) -> str:
        """Count total, individuals, groups, with_phone and without_phone; matching for a phone filter.
        by_country adds an ISO country breakdown."""
        with database.transaction() as s:
            repo = ContactRepository(s)
            result: dict[str, Any] = {
                "total": repo.count(),
                "individuals": repo.count(is_group=False),
                "groups": repo.count(is_group=True),
                "with_phone": repo.count(has_phone=True),
                "without_phone": repo.count(has_phone=False),
            }
            if has_phone is not None or phone_prefix:
                result["filter"] = {"has_phone": has_phone, "phone_prefix": phone_prefix}
                result["matching"] = repo.count(has_phone=has_phone, phone_prefix=phone_prefix)
            if by_country:
                breakdown: dict[str, int] = {}
                for row in s.all("SELECT phone_number FROM contacts WHERE phone_number IS NOT NULL"):
                    region = region_of(row["phone_number"]) or "unknown"
                    breakdown[region] = breakdown.get(region, 0) + 1
                result["by_country"] = dict(sorted(breakdown.items(), key=lambda item: -item[1]))
        return _dump(result)

    @tool
    def get_message_stats(days: int = 7) -> str:
        """Stored-message activity, most recent contacts, category distribution and latest scrape runs."""
        since = utcnow() - timedelta(days=max(0, days))
        with database.transaction() as s:
            msgs = MessageRepository(s)
            top = s.all(
                """SELECT c.contact_name AS name, count(*) AS messages, max(m.message_timestamp) AS last_message_at
                   FROM contacts c JOIN messages m ON m.contact_id=c.id WHERE m.message_timestamp >= %s
                   GROUP BY c.id ORDER BY max(m.message_timestamp) DESC LIMIT 10""",
                (since,),
            )
            cats = s.all("SELECT category, count(*) n FROM conversations WHERE category IS NOT NULL GROUP BY category")
            return _dump(
                {
                    "messages_stored": msgs.count(),
                    f"messages_last_{days}_days": msgs.count(since),
                    "most_recent_active_contacts": top,
                    "categories": {r["category"]: r["n"] for r in cats},
                    "recent_scrape_runs": [r.model_dump() for r in RunRepository(s).latest_scrapes(5)],
                }
            )

    @tool
    def data_quality_report() -> str:
        """Check missing names/phones, invalid phones, duplicates, timestamps and retention; return quality score."""
        with database.transaction() as s:
            return _dump(QualityRepository(s).report(keep))

    tools = {
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
        async def classify_recent_conversations(days: int = 7, limit: int = 20, only_unclassified: bool = False) -> str:
            """Classify recent conversations and return results, reusing current categories unless messages changed.
            only_unclassified excludes current cached results."""
            rows = await classify_conversations(
                database,
                settings,
                llm,
                limit=max(1, min(limit, 50)),
                only_unclassified=only_unclassified,
                days=max(0, days),
            )
            return _dump({"classified": [r.model_dump() for r in rows]})

        tools[classify_recent_conversations.name] = classify_recent_conversations
    return tools
