import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Contact, Conversation, Message
from app.schemas.contact import ContactIn
from app.utils.time import utcnow


class ContactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, contact: ContactIn) -> tuple[uuid.UUID, bool]:
        """Insert or update by whatsapp_id. Returns (id, inserted)."""
        values = contact.model_dump()
        stmt = insert(Contact).values(id=uuid.uuid4(), **values)
        update_cols = {
            "contact_name": func.coalesce(stmt.excluded.contact_name, Contact.contact_name),
            "phone_number": func.coalesce(stmt.excluded.phone_number, Contact.phone_number),
            "is_group": stmt.excluded.is_group,
            "updated_at": func.now(),
        }
        upsert = stmt.on_conflict_do_update(index_elements=[Contact.whatsapp_id], set_=update_cols)
        # xmax = 0 only for freshly inserted rows.
        returning: Any = upsert.returning(Contact.id, literal_column("(xmax = 0)").label("inserted"))
        row = (await self.session.execute(returning)).one()
        return row.id, bool(row.inserted)

    async def upsert_conversation(
        self, contact_id: uuid.UUID, unread_count: int, last_message_at: datetime | None
    ) -> None:
        now = utcnow()
        stmt = insert(Conversation).values(
            id=uuid.uuid4(),
            contact_id=contact_id,
            unread_count=unread_count,
            last_message_at=last_message_at,
            last_scraped_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Conversation.contact_id],
            set_={
                "unread_count": stmt.excluded.unread_count,
                "last_message_at": func.coalesce(stmt.excluded.last_message_at, Conversation.last_message_at),
                "last_scraped_at": now,
            },
        )
        await self.session.execute(stmt)

    async def get(self, contact_id: uuid.UUID) -> Contact | None:
        return await self.session.get(Contact, contact_id)

    def _summary_query(self) -> Select:
        msg_stats = (
            select(
                Message.contact_id,
                func.count(Message.id).label("message_count"),
                func.max(Message.message_timestamp).label("last_message_at"),
            )
            .group_by(Message.contact_id)
            .subquery()
        )
        return (
            select(
                Contact,
                func.coalesce(msg_stats.c.message_count, 0).label("message_count"),
                msg_stats.c.last_message_at,
                Conversation.category,
            )
            .outerjoin(msg_stats, msg_stats.c.contact_id == Contact.id)
            .outerjoin(Conversation, Conversation.contact_id == Contact.id)
        )

    async def search(
        self,
        name: str | None = None,
        phone: str | None = None,
        phone_prefix: str | None = None,
        has_phone: bool | None = None,
        is_group: bool | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[tuple[Contact, int, datetime | None, str | None]]]:
        q = self._summary_query()
        filters: list[ColumnElement[bool]] = []
        if name:
            filters.append(Contact.contact_name.ilike(f"%{name}%"))
        if phone:
            filters.append(Contact.phone_number.ilike(f"%{phone.lstrip('+')}%"))
        if phone_prefix:
            prefix = phone_prefix if phone_prefix.startswith("+") else f"+{phone_prefix}"
            filters.append(Contact.phone_number.startswith(prefix))
        if has_phone is True:
            filters.append(Contact.phone_number.is_not(None))
        elif has_phone is False:
            filters.append(Contact.phone_number.is_(None))
        if is_group is not None:
            filters.append(Contact.is_group.is_(is_group))
        if category:
            filters.append(Conversation.category == category)
        if filters:
            q = q.where(*filters)
        total = (await self.session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
        q = q.order_by(func.coalesce(q.selected_columns.last_message_at, Contact.created_at).desc().nulls_last())
        rows = (await self.session.execute(q.limit(limit).offset(offset))).all()
        return total, [(r[0], r.message_count, r.last_message_at, r.category) for r in rows]

    async def count(
        self,
        is_group: bool | None = None,
        has_phone: bool | None = None,
        phone_prefix: str | None = None,
    ) -> int:
        q = select(func.count(Contact.id))
        if is_group is not None:
            q = q.where(Contact.is_group.is_(is_group))
        if has_phone is True:
            q = q.where(Contact.phone_number.is_not(None))
        elif has_phone is False:
            q = q.where(Contact.phone_number.is_(None))
        if phone_prefix:
            prefix = phone_prefix if phone_prefix.startswith("+") else f"+{phone_prefix}"
            q = q.where(Contact.phone_number.startswith(prefix))
        return (await self.session.execute(q)).scalar_one()

    async def find_by_name_or_id(self, ref: str) -> Contact | None:
        try:
            return await self.get(uuid.UUID(ref))
        except ValueError:
            pass
        q = (
            select(Contact)
            .where(
                or_(
                    Contact.contact_name.ilike(f"%{ref}%"),
                    Contact.phone_number.ilike(f"%{ref}%"),
                    Contact.whatsapp_id == ref,
                )
            )
            .order_by(Contact.updated_at.desc())
            .limit(1)
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def get_conversation(self, contact_id: uuid.UUID) -> Conversation | None:
        q = select(Conversation).where(Conversation.contact_id == contact_id)
        return (await self.session.execute(q)).scalar_one_or_none()

    async def set_classification(
        self, contact_id: uuid.UUID, category: str, action: str, confidence: float, reason: str
    ) -> None:
        stmt = insert(Conversation).values(
            id=uuid.uuid4(),
            contact_id=contact_id,
            category=category,
            action=action,
            category_confidence=confidence,
            category_reason=reason,
            classified_at=utcnow(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Conversation.contact_id],
            set_={
                "category": category,
                "action": action,
                "category_confidence": confidence,
                "category_reason": reason,
                "classified_at": utcnow(),
            },
        )
        await self.session.execute(stmt)
