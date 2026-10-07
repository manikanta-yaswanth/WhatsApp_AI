import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Contact, Message
from app.schemas.message import MessageIn
from app.utils.time import utcnow


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert_new(self, contact_id: uuid.UUID, messages: list[MessageIn]) -> int:
        """Insert messages, skipping ones already stored (by whatsapp_message_id)."""
        if not messages:
            return 0
        rows = [{"id": uuid.uuid4(), "contact_id": contact_id, **m.model_dump()} for m in messages]
        stmt = (
            insert(Message)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[Message.whatsapp_message_id])
            .returning(Message.id)
        )
        return len((await self.session.execute(stmt)).all())

    async def prune(self, contact_id: uuid.UUID, keep: int) -> int:
        """Retain only the newest `keep` messages for a contact."""
        keep_ids = (
            select(Message.id)
            .where(Message.contact_id == contact_id)
            .order_by(Message.message_timestamp.desc(), Message.scraped_at.desc())
            .limit(keep)
        )
        stmt = delete(Message).where(Message.contact_id == contact_id, Message.id.not_in(keep_ids))
        result = await self.session.execute(stmt)
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def recent_for_contact(self, contact_id: uuid.UUID, limit: int = 3) -> list[Message]:
        q = (
            select(Message)
            .where(Message.contact_id == contact_id)
            .order_by(Message.message_timestamp.desc())
            .limit(limit)
        )
        return list((await self.session.execute(q)).scalars())

    async def recent_conversations(
        self, since: datetime | None = None, limit: int = 50, per_contact: int = 3
    ) -> list[tuple[Contact, list[Message]]]:
        """Contacts with the newest activity, each with their newest messages."""
        last = func.max(Message.message_timestamp).label("last_at")
        q = select(Message.contact_id, last).group_by(Message.contact_id)
        if since:
            q = q.having(func.max(Message.message_timestamp) >= since)
        q = q.order_by(last.desc()).limit(limit)
        contact_ids = [r.contact_id for r in (await self.session.execute(q)).all()]
        if not contact_ids:
            return []
        contacts = {
            c.id: c for c in (await self.session.execute(select(Contact).where(Contact.id.in_(contact_ids)))).scalars()
        }
        ranked = (
            select(
                Message,
                func.row_number()
                .over(partition_by=Message.contact_id, order_by=Message.message_timestamp.desc())
                .label("rn"),
            )
            .where(Message.contact_id.in_(contact_ids))
            .subquery()
        )
        from sqlalchemy.orm import aliased

        m = aliased(Message, ranked)
        msgs = (await self.session.execute(select(m).where(ranked.c.rn <= per_contact))).scalars()
        grouped: dict[uuid.UUID, list[Message]] = {cid: [] for cid in contact_ids}
        for msg in msgs:
            grouped[msg.contact_id].append(msg)
        for lst in grouped.values():
            lst.sort(key=lambda x: x.message_timestamp, reverse=True)
        return [(contacts[cid], grouped[cid]) for cid in contact_ids if cid in contacts]

    async def search_text(self, query: str, limit: int = 20, days: int | None = None) -> list[tuple[Message, Contact]]:
        terms = [t for t in query.split() if len(t) > 1][:8] or [query]
        q = (
            select(Message, Contact)
            .join(Contact, Contact.id == Message.contact_id)
            .where(or_(*[Message.message_text.ilike(f"%{t}%") for t in terms]))
        )
        if days:
            q = q.where(Message.message_timestamp >= utcnow() - timedelta(days=days))
        q = q.order_by(Message.message_timestamp.desc()).limit(limit)
        return [(r[0], r[1]) for r in (await self.session.execute(q)).all()]

    async def count(self, since: datetime | None = None) -> int:
        q = select(func.count(Message.id))
        if since:
            q = q.where(Message.message_timestamp >= since)
        return (await self.session.execute(q)).scalar_one()
