import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ScrapeStartResponse(BaseModel):
    run_id: uuid.UUID
    status: str


class ScrapeRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    extraction_method: str | None
    contacts_found: int
    messages_found: int
    contacts_saved: int
    messages_saved: int
    messages_pruned: int
    invalid_records: int
    duration_ms: int | None
    error_message: str | None
