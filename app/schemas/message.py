import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SenderType = Literal["contact", "me", "group_member", "system"]


class MessageIn(BaseModel):
    whatsapp_message_id: str = Field(min_length=1, max_length=255)
    sender_type: SenderType
    sender_name: str | None = None
    message_type: str = "text"
    message_text: str | None = None
    message_timestamp: datetime

    @field_validator("message_timestamp")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("message_timestamp must be timezone-aware")
        return v


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    whatsapp_message_id: str
    sender_type: str
    sender_name: str | None
    message_type: str
    message_text: str | None
    message_timestamp: datetime
    scraped_at: datetime
