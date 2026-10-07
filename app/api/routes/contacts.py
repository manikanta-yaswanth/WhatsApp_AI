import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.db.repositories.contact_repository import ContactRepository
from app.schemas.contact import ContactList, ContactOut, ContactSummary

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("", response_model=ContactList)
async def list_contacts(
    name: str | None = None,
    phone: str | None = None,
    has_phone: bool | None = None,
    is_group: bool | None = None,
    category: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> ContactList:
    total, rows = await ContactRepository(session).search(
        name=name, phone=phone, has_phone=has_phone, is_group=is_group, category=category, limit=limit, offset=offset
    )
    items = [
        ContactSummary(**ContactOut.model_validate(c).model_dump(), message_count=n, last_message_at=last, category=cat)
        for c, n, last, cat in rows
    ]
    return ContactList(total=total, items=items)


@router.get("/{contact_id}", response_model=ContactOut)
async def get_contact(contact_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> object:
    contact = await ContactRepository(session).get(contact_id)
    if contact is None:
        raise HTTPException(404, "contact not found")
    return contact
