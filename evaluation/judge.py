import asyncio
import uuid
from pathlib import Path

from evaluation.datasets import DEFAULT_DATASET, load_dataset
from evaluation.metrics import summarize_evaluations
from Models.records import Evaluation, EvaluationRun
from utils.agent_runner import AgentRunError, AgentService
from utils.logging import get_logger
from utils.repositories import RunRepository

log = get_logger(__name__)


async def create_evaluation_run(service: AgentService, dataset: Path | str = DEFAULT_DATASET) -> uuid.UUID:
    def create() -> uuid.UUID:
        with service.database.transaction() as s:
            return RunRepository(s).create_evaluation_run(Path(dataset).name)

    return await asyncio.to_thread(create)


async def run_evaluation(
    service: AgentService,
    run_id: uuid.UUID,
    dataset: Path | str = DEFAULT_DATASET,
) -> EvaluationRun:
    error = None
    try:
        for question in load_dataset(dataset):
            failed_id = None
            try:
                response, state = await service.run(
                    question.question, judge=True, expected=question.reference, record_evaluation=False
                )
            except AgentRunError as exc:
                failed_id = exc.run_id
                response, state = None, {}

            def save(response, state, question, failed_id) -> None:  # type: ignore[no-untyped-def]
                with service.database.transaction() as s:
                    repo = RunRepository(s)
                    if response is None:
                        row = Evaluation(
                            evaluation_run_id=run_id,
                            agent_run_id=failed_id,
                            question=question.question,
                            expected=question.reference,
                            expected_tools=question.expected_tools or None,
                            schema_compliant=False,
                            reasoning="agent failed",
                        )
                    else:
                        run = repo.get_agent(response.run_id)
                        row = AgentService._evaluation_row(
                            run,
                            state.get("evaluation") or {},
                            run_id,
                            question.reference,
                            question.expected_tools or None,
                        )
                    repo.save_evaluation(row)

            await asyncio.to_thread(save, response, state, question, failed_id)
    except Exception as exc:  # noqa: BLE001 - persist evaluation failure
        error = type(exc).__name__

    def finish() -> EvaluationRun:
        with service.database.transaction() as s:
            repo = RunRepository(s)
            rows = repo.evaluations(run_id)
            return repo.finish_evaluation(run_id, summarize_evaluations(rows), len(rows), error)

    run = await asyncio.to_thread(finish)
    log.info("evaluation_finished", run_id=str(run.id), status=run.status, questions=run.total_questions)
    return run
