import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = _uuid()
    whatsapp_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(32))
    contact_name: Mapped[str | None] = mapped_column(String(255))
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan", passive_deletes=True
    )
    conversation: Mapped["Conversation | None"] = relationship(
        back_populates="contact", cascade="all, delete-orphan", passive_deletes=True
    )


class Conversation(Base):
    """Chat-level metadata plus the latest AI classification of the recent messages."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid()
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    unread_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    category: Mapped[str | None] = mapped_column(String(40))
    category_confidence: Mapped[float | None] = mapped_column(Float)
    category_reason: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(String(20))
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    contact: Mapped[Contact] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index(
            "idx_messages_contact_time", "contact_id", "message_timestamp", postgresql_ops={"message_timestamp": "DESC"}
        ),
    )

    id: Mapped[uuid.UUID] = _uuid()
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    whatsapp_message_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    sender_type: Mapped[str] = mapped_column(String(20), nullable=False)
    sender_name: Mapped[str | None] = mapped_column(String(255))
    message_type: Mapped[str] = mapped_column(String(30), default="text", server_default="text")
    message_text: Mapped[str | None] = mapped_column(Text)
    message_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scraped_at: Mapped[datetime] = _created()

    contact: Mapped[Contact] = relationship(back_populates="messages")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[uuid.UUID] = _uuid()
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_method: Mapped[str | None] = mapped_column(String(20))
    contacts_found: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    messages_found: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    contacts_saved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    messages_saved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    messages_pruned: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    invalid_records: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = _uuid()
    query: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(40))
    answer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    iterations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    judge_score: Mapped[float | None] = mapped_column(Float)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = _uuid()
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    dataset: Mapped[str | None] = mapped_column(String(255))
    total_questions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    evaluations: Mapped[list["Evaluation"]] = relationship(
        back_populates="evaluation_run", cascade="all, delete-orphan", passive_deletes=True
    )


class Evaluation(Base):
    """One LLM-as-Judge verdict for one agent answer."""

    __tablename__ = "evaluations"

    id: Mapped[uuid.UUID] = _uuid()
    evaluation_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"))
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[str | None] = mapped_column(Text)
    expected_tools: Mapped[list[str] | None] = mapped_column(JSONB)
    tools_used: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    answer: Mapped[str | None] = mapped_column(Text)
    correctness: Mapped[float | None] = mapped_column(Float)
    relevance: Mapped[float | None] = mapped_column(Float)
    groundedness: Mapped[float | None] = mapped_column(Float)
    completeness: Mapped[float | None] = mapped_column(Float)
    hallucination: Mapped[float | None] = mapped_column(Float)
    tool_accuracy: Mapped[float | None] = mapped_column(Float)
    schema_compliant: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    final_score: Mapped[float | None] = mapped_column(Float)
    reasoning: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()

    evaluation_run: Mapped[EvaluationRun | None] = relationship(back_populates="evaluations")
