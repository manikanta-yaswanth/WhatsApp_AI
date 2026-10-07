import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.db.repositories.contact_repository import ContactRepository
from app.db.repositories.message_repository import MessageRepository
from app.schemas.message import MessageOut

router = APIRouter(tags=["messages"])


@router.get("/contacts/{contact_id}/messages", response_model=list[MessageOut])
async def contact_messages(
    contact_id: uuid.UUID, limit: int = Query(3, ge=1, le=50), session: AsyncSession = Depends(get_session)
) -> list:
    if await ContactRepository(session).get(contact_id) is None:
        raise HTTPException(404, "contact not found")
    return await MessageRepository(session).recent_for_contact(contact_id, limit)


@router.get("/messages", response_model=list[MessageOut])
async def search_messages(
    q: str = Query(min_length=1),
    days: int | None = None,
    limit: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list:
    return [m for m, _ in await MessageRepository(session).search_text(q, limit=limit, days=days)]
