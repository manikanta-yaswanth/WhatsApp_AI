from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas.contact import ContactIn
from app.schemas.message import MessageIn


class ScrapedConversation(BaseModel):
    """Scraper output contract: one chat with its newest messages (newest first)."""

    contact: ContactIn
    unread_count: int = 0
    last_message_at: datetime | None = None
    messages: list[MessageIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _dedupe_and_sort(self) -> "ScrapedConversation":
        seen: dict[str, MessageIn] = {}
        for m in self.messages:
            seen.setdefault(m.whatsapp_message_id, m)
        self.messages = sorted(seen.values(), key=lambda m: m.message_timestamp, reverse=True)
        if self.messages and self.last_message_at is None:
            self.last_message_at = self.messages[0].message_timestamp
        return self

    def newest(self, limit: int) -> list[MessageIn]:
        return self.messages[:limit]
