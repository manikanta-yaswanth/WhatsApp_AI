import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.phone import normalize_phone


class ContactIn(BaseModel):
    """Validated contact as produced by the scraper parser."""

    whatsapp_id: str = Field(min_length=3, max_length=255)
    contact_name: str | None = Field(default=None, max_length=255)
    phone_number: str | None = None
    is_group: bool = False

    @field_validator("contact_name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None

    @field_validator("phone_number")
    @classmethod
    def _normalize_phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    whatsapp_id: str
    contact_name: str | None
    phone_number: str | None
    is_group: bool
    created_at: datetime
    updated_at: datetime


class ContactSummary(ContactOut):
    message_count: int = 0
    last_message_at: datetime | None = None
    category: str | None = None


class ContactList(BaseModel):
    total: int
    items: list[ContactSummary]
