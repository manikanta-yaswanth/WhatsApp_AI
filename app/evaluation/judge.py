import uuid
from pathlib import Path

from sqlalchemy import select

from app.db.models import AgentRun, Evaluation, EvaluationRun
from app.evaluation.datasets import DEFAULT_DATASET, load_dataset
from app.evaluation.metrics import summarize_evaluations
from app.services.agent_service import AgentService
from app.utils.logging import get_logger
from app.utils.time import utcnow

log = get_logger(__name__)


async def create_evaluation_run(service: AgentService, dataset: Path | str = DEFAULT_DATASET) -> uuid.UUID:
    async with service.session_factory() as s, s.begin():
        run = EvaluationRun(id=uuid.uuid4(), status="running", dataset=str(Path(dataset).name))
        s.add(run)
    return run.id


async def run_evaluation(
    service: AgentService, run_id: uuid.UUID, dataset: Path | str = DEFAULT_DATASET
) -> EvaluationRun:
    """Question -> agent -> answer -> LLM judge (with expected reference) -> stored scores -> metrics."""
    try:
        questions = load_dataset(dataset)
        for q in questions:
            response, state = await _safe_run(service, q.question, q.reference)
            ev = (state or {}).get("evaluation") or {"schema_compliant": False}
            async with service.session_factory() as s, s.begin():
                agent_run = await s.get(AgentRun, response) if isinstance(response, uuid.UUID) else None
                if agent_run is None:
                    s.add(
                        Evaluation(
                            evaluation_run_id=run_id,
                            question=q.question,
                            expected=q.reference,
                            expected_tools=q.expected_tools or None,
                            schema_compliant=False,
                            reasoning="agent failed",
                        )
                    )
                    continue
                s.add(AgentService._evaluation_row(agent_run, ev, run_id, q.reference, q.expected_tools or None))
        status, error = "completed", None
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
    async with service.session_factory() as s, s.begin():
        run = await s.get(EvaluationRun, run_id)
        assert run is not None
        rows = list((await s.execute(select(Evaluation).where(Evaluation.evaluation_run_id == run_id))).scalars())
        run.total_questions = len(rows)
        run.metrics = summarize_evaluations(rows)
        run.status, run.error_message, run.completed_at = status, error, utcnow()
    log.info(
        "evaluation_finished",
        run_id=str(run_id),
        status=status,
        questions=run.total_questions,
        avg_final_score=run.metrics.get("avg_final_score"),
    )
    return run


async def _safe_run(
    service: AgentService, question: str, reference: str | None
) -> tuple[uuid.UUID | None, dict | None]:
    try:
        response, state = await service.run(question, judge=True, expected=reference, record_evaluation=False)
        return response.run_id, state
    except RuntimeError:
        async with service.session_factory() as s:
            last = (
                await s.execute(
                    select(AgentRun.id).where(AgentRun.query == question).order_by(AgentRun.created_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
        return last, None
