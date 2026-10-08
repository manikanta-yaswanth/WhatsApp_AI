import asyncio
import time
import uuid
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from agents.whatsapp_agent import build_agent_graph
from Models.records import AgentRun, Evaluation
from Models.schema import (
    AgentResponse,
    ClassifiedContact,
    SummarizeResponse,
    Summary,
    ToolCallRecord,
)
from utils import prompts
from utils.database import DatabaseUtil
from utils.logging import get_logger
from utils.repositories import ContactRepository, MessageRepository, RunRepository
from utils.settings import Settings
from utils.structured import ainvoke_structured
from utils.tools import build_tools, classify_conversations, format_conversation

log = get_logger(__name__)


class NotFoundError(LookupError):
    pass


class AgentService:
    def __init__(
        self,
        settings: Settings,
        database: DatabaseUtil,
        llm: BaseChatModel,
        judge_llm: BaseChatModel | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.llm = llm
        self.judge_llm = judge_llm or llm
        self.tools = build_tools(database, settings, llm)
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
            error = type(exc).__name__
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

        def save_run() -> None:
            with self.database.transaction() as s:
                repo = RunRepository(s)
                repo.save_agent(run)
                if record_evaluation and evaluation and evaluation.get("final_score") is not None:
                    repo.save_evaluation(self._evaluation_row(run, evaluation, None, expected))

        await asyncio.to_thread(save_run)
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
        return await classify_conversations(
            self.database, self.settings, self.llm, contact_ids, limit, only_unclassified
        )

    async def summarize(self, contact_id: uuid.UUID) -> SummarizeResponse:
        def snapshot():  # type: ignore[no-untyped-def]
            with self.database.transaction() as s:
                c = ContactRepository(s).get(contact_id)
                if c is None:
                    raise NotFoundError("contact not found")
                msgs = MessageRepository(s).recent_for_contact(contact_id, self.settings.max_messages_per_contact)
                return c, msgs

        c, msgs = await asyncio.to_thread(snapshot)
        if not msgs:
            return SummarizeResponse(
                contact_id=c.id, contact_name=c.contact_name, summary="No stored messages.", action="NO_ACTION"
            )
        r = await ainvoke_structured(
            self.llm, Summary, [SystemMessage(prompts.SUMMARIZE), HumanMessage(format_conversation(c, msgs))]
        )
        return SummarizeResponse(contact_id=c.id, contact_name=c.contact_name, summary=r.summary, action=r.action)
