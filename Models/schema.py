import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from utils.phone import normalize_phone


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


Intent = Literal["contact", "message", "classification", "search", "analytics", "data_quality"]

Category = Literal[
    "meeting_request",
    "job_opportunity",
    "personal",
    "follow_up",
    "urgent",
    "sales",
    "support",
    "spam",
    "unknown",
]

Action = Literal["ACTION_REQUIRED", "FOLLOW_UP", "NO_ACTION"]


class IntentDecision(BaseModel):
    """Router output: which specialist agent should handle the question."""

    intent: Intent
    reason: str = Field(description="One short sentence.")


class ConversationClassification(BaseModel):
    category: Category
    action: Action = Field(description="Whether the user needs to reply or act.")
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(description="One short sentence; do not quote the messages verbatim.")


class Summary(BaseModel):
    summary: str
    action: Action


class EvaluationResult(BaseModel):
    """LLM-as-Judge scores, each in [0, 1]. hallucination: 1 = fully fabricated."""

    correctness: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    groundedness: float = Field(ge=0, le=1)
    completeness: float = Field(ge=0, le=1)
    hallucination: float = Field(ge=0, le=1)
    reasoning: str

    @property
    def final_score(self) -> float:
        return round(
            0.30 * self.correctness
            + 0.20 * self.relevance
            + 0.25 * self.groundedness
            + 0.15 * self.completeness
            + 0.10 * (1 - self.hallucination),
            4,
        )


class AgentQuery(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    judge: bool = True


class ToolCallRecord(BaseModel):
    name: str
    args: dict


class AgentResponse(BaseModel):
    run_id: uuid.UUID
    intent: str | None
    answer: str
    tool_calls: list[ToolCallRecord]
    iterations: int
    retry_count: int
    judge_score: float | None
    evaluation: dict | None = None
    latency_ms: int


class ClassifyRequest(BaseModel):
    contact_ids: list[uuid.UUID] | None = None
    limit: int = Field(default=50, ge=1, le=500)
    only_unclassified: bool = False


class ClassifiedContact(BaseModel):
    contact_id: uuid.UUID
    contact_name: str | None
    category: Category
    action: Action
    confidence: float
    reason: str


class SummarizeRequest(BaseModel):
    contact_id: uuid.UUID


class SummarizeResponse(BaseModel):
    contact_id: uuid.UUID
    contact_name: str | None
    summary: str
    action: Action


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


class AgentState(TypedDict, total=False):
    user_query: str
    intent: str
    messages: Annotated[list[AnyMessage], add_messages]
    tool_calls: list[dict[str, Any]]
    iterations: int
    answer: str
    evaluation: dict[str, Any] | None
    retry_count: int
    judge_enabled: bool
    expected: str | None
    prompt_tokens: int
    completion_tokens: int


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversations: list[ScrapedConversation] = Field(min_length=1, max_length=500)
