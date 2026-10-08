import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from Models.schema import ContactOut as Contact
from Models.schema import MessageOut as Message
from Models.schema import ScrapeRunOut as ScrapeRun

__all__ = ["Contact", "Message", "ScrapeRun", "Conversation", "AgentRun", "Evaluation", "EvaluationRun"]


class Conversation(BaseModel):
    id: uuid.UUID
    contact_id: uuid.UUID
    unread_count: int = 0
    last_message_at: datetime | None = None
    classified_at: datetime | None = None
    category: str | None = None
    action: str | None = None
    category_confidence: float | None = None
    category_reason: str | None = None


class AgentRun(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    query: str
    intent: str | None = None
    answer: str | None = None
    status: str = "completed"
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    iterations: int = 0
    retry_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int | None = None
    judge_score: float | None = None
    error_message: str | None = None


class Evaluation(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    evaluation_run_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    question: str
    expected: str | None = None
    expected_tools: list[str] | None = None
    tools_used: list[str] = Field(default_factory=list)
    answer: str | None = None
    correctness: float | None = None
    relevance: float | None = None
    groundedness: float | None = None
    completeness: float | None = None
    hallucination: float | None = None
    tool_accuracy: float | None = None
    schema_compliant: bool = True
    final_score: float | None = None
    reasoning: str | None = None
    latency_ms: int | None = None


class EvaluationRun(BaseModel):
    id: uuid.UUID
    status: str
    dataset: str | None
    total_questions: int
    metrics: dict[str, Any]
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
