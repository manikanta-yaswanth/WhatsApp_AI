import json
from pathlib import Path

from sqlalchemy import select

from app.db.models import Evaluation
from app.evaluation.judge import create_evaluation_run, run_evaluation
from app.schemas.agent import EvaluationResult
from app.services.agent_service import AgentService
from tests.test_agents import make_responder


def test_final_score_weights() -> None:
    r = EvaluationResult(correctness=1, relevance=1, groundedness=1, completeness=1, hallucination=0, reasoning="")
    assert r.final_score == 1.0
    r = EvaluationResult(correctness=0.5, relevance=1, groundedness=0, completeness=1, hallucination=1, reasoning="")
    assert r.final_score == 0.5


async def test_evaluation_run_stores_metrics(session_factory, settings, scripted, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    dataset = tmp_path / "q.json"
    dataset.write_text(
        json.dumps(
            [
                {"question": "How many contacts are stored?", "expected": "0", "expected_tools": ["count_contacts"]},
                {"question": "Any data issues?", "expected_tools": ["data_quality_report"]},
            ]
        )
    )
    llm = scripted(make_responder("contact", [("count_contacts", {})], "0 contacts.", [0.9, 0.8]))
    service = AgentService(settings, session_factory, llm)
    run = await run_evaluation(service, await create_evaluation_run(service, dataset), dataset)
    assert run.status == "completed" and run.total_questions == 2
    assert run.metrics["tool_accuracy"] == 0.5
    assert run.metrics["schema_compliance"] == 1.0
    async with session_factory() as s:
        rows = list((await s.execute(select(Evaluation))).scalars())
    assert sorted(r.final_score for r in rows) == [0.8, 0.9]
