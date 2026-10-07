import uuid
from typing import Literal

from pydantic import BaseModel, Field

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
