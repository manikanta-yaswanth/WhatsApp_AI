import time
import uuid
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.graph import build_agent_graph
from app.agents.tools import build_tools, classify_contact, format_conversation
from app.config.settings import Settings
from app.db.models import AgentRun, Evaluation
from app.db.repositories.contact_repository import ContactRepository
from app.db.repositories.message_repository import MessageRepository
from app.llm import prompts
from app.llm.structured import ainvoke_structured
from app.schemas.agent import (
    AgentResponse,
    ClassifiedContact,
    SummarizeResponse,
    Summary,
    ToolCallRecord,
)
from app.utils.logging import get_logger

log = get_logger(__name__)


class NotFoundError(LookupError):
    pass


class AgentService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        llm: BaseChatModel,
        judge_llm: BaseChatModel | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.llm = llm
        self.judge_llm = judge_llm or llm
        self.tools = build_tools(session_factory, settings, llm)
        self.graph = build_agent_graph(llm, self.tools, settings, self.judge_llm)

    async def run(
        self, query: str, judge: bool = True, expected: str | None = None, record_evaluation: bool = True
    ) -> tuple[AgentResponse, dict[str, Any]]:
        t0 = time.perf_counter()
        state: dict[str, Any] = {}
        error: str | None = None
        try:
            state = await self.graph.ainvoke(
                {"user_query": query, "judge_enabled": judge, "expected": expected, "retry_count": 0},
                {
                    "recursion_limit": 4
                    * (self.settings.agent_max_iterations + 2)
                    * (self.settings.judge_max_retries + 1)
                },
            )
        except Exception as exc:  # noqa: BLE001 - persisted on the agent run
            error = f"{type(exc).__name__}: {exc}"[:2000]
            log.error("agent_failed", error=type(exc).__name__)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        evaluation = state.get("evaluation")
        score = evaluation.get("final_score") if evaluation else None
        run = AgentRun(
            id=uuid.uuid4(),
            query=query,
            intent=state.get("intent"),
            answer=state.get("answer"),
            status="failed" if error else "completed",
            tool_calls=state.get("tool_calls", []),
            iterations=state.get("iterations", 0),
            retry_count=state.get("retry_count", 0),
            prompt_tokens=state.get("prompt_tokens", 0),
            completion_tokens=state.get("completion_tokens", 0),
            latency_ms=latency_ms,
            judge_score=score,
            error_message=error,
        )
        async with self.session_factory() as s, s.begin():
            s.add(run)
            await s.flush()
            if record_evaluation and evaluation and evaluation.get("final_score") is not None:
                s.add(self._evaluation_row(run, evaluation, None, None))
        log.info(
            "agent_run",
            run_id=str(run.id),
            intent=run.intent,
            iterations=run.iterations,
            tool_calls=len(run.tool_calls),
            latency_ms=latency_ms,
            judge_score=score,
            prompt_tokens=run.prompt_tokens,
            completion_tokens=run.completion_tokens,
        )
        if error:
            raise RuntimeError(error)
        response = AgentResponse(
            run_id=run.id,
            intent=run.intent,
            answer=run.answer or "",
            tool_calls=[ToolCallRecord(**tc) for tc in run.tool_calls],
            iterations=run.iterations,
            retry_count=run.retry_count,
            judge_score=score,
            evaluation=evaluation,
            latency_ms=latency_ms,
        )
        return response, state

    @staticmethod
    def _evaluation_row(
        run: AgentRun,
        ev: dict[str, Any],
        evaluation_run_id: uuid.UUID | None,
        expected: str | None,
        expected_tools: list[str] | None = None,
    ) -> Evaluation:
        used = sorted({tc["name"] for tc in run.tool_calls})
        tool_accuracy = None
        if expected_tools:
            tool_accuracy = len(set(expected_tools) & set(used)) / len(set(expected_tools))
        return Evaluation(
            evaluation_run_id=evaluation_run_id,
            agent_run_id=run.id,
            question=run.query,
            expected=expected,
            expected_tools=expected_tools,
            tools_used=used,
            answer=run.answer,
            correctness=ev.get("correctness"),
            relevance=ev.get("relevance"),
            groundedness=ev.get("groundedness"),
            completeness=ev.get("completeness"),
            hallucination=ev.get("hallucination"),
            tool_accuracy=tool_accuracy,
            schema_compliant=bool(ev.get("schema_compliant", False)),
            final_score=ev.get("final_score"),
            reasoning=ev.get("reasoning"),
            latency_ms=run.latency_ms,
        )

    async def classify(
        self, contact_ids: list[uuid.UUID] | None, limit: int, only_unclassified: bool
    ) -> list[ClassifiedContact]:
        keep = self.settings.max_messages_per_contact
        out: list[ClassifiedContact] = []
        async with self.session_factory() as s:
            contacts = ContactRepository(s)
            messages = MessageRepository(s)
            if contact_ids:
                pairs = []
                for cid in contact_ids:
                    c = await contacts.get(cid)
                    if c is not None:
                        pairs.append((c, await messages.recent_for_contact(cid, keep)))
            else:
                pairs = await messages.recent_conversations(limit=limit, per_contact=keep)
            for c, msgs in pairs:
                if not msgs:
                    continue
                conv = await contacts.get_conversation(c.id)
                if only_unclassified and conv and conv.category:
                    continue
                r = await classify_contact(self.llm, c, msgs)
                await contacts.set_classification(c.id, r.category, r.action, r.confidence, r.reason)
                out.append(ClassifiedContact(contact_id=c.id, contact_name=c.contact_name, **r.model_dump()))
            await s.commit()
        return out

    async def summarize(self, contact_id: uuid.UUID) -> SummarizeResponse:
        async with self.session_factory() as s:
            c = await ContactRepository(s).get(contact_id)
            if c is None:
                raise NotFoundError("contact not found")
            msgs = await MessageRepository(s).recent_for_contact(contact_id, self.settings.max_messages_per_contact)
        if not msgs:
            return SummarizeResponse(
                contact_id=c.id, contact_name=c.contact_name, summary="No stored messages.", action="NO_ACTION"
            )
        r = await ainvoke_structured(
            self.llm, Summary, [SystemMessage(prompts.SUMMARIZE), HumanMessage(format_conversation(c, msgs))]
        )
        return SummarizeResponse(contact_id=c.id, contact_name=c.contact_name, summary=r.summary, action=r.action)
