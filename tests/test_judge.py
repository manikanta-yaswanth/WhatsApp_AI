import json
from pathlib import Path

from evaluation.judge import create_evaluation_run, run_evaluation
from Models.schema import EvaluationResult
from tests.test_agents import make_responder
from utils.agent_runner import AgentService
from utils.repositories import RunRepository


def test_final_score_weights() -> None:
    r = EvaluationResult(correctness=1, relevance=1, groundedness=1, completeness=1, hallucination=0, reasoning="")
    assert r.final_score == 1.0
    r = EvaluationResult(correctness=0.5, relevance=1, groundedness=0, completeness=1, hallucination=1, reasoning="")
    assert r.final_score == 0.5


async def test_evaluation_run_stores_metrics(database, settings, scripted, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
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
    service = AgentService(settings, database, llm)
    run = await run_evaluation(service, await create_evaluation_run(service, dataset), dataset)
    assert run.status == "completed" and run.total_questions == 2
    assert run.metrics["tool_accuracy"] == 0.5
    assert run.metrics["schema_compliance"] == 1.0
    with database.transaction() as s:
        rows = RunRepository(s).evaluations()
    assert sorted(r.final_score for r in rows if r.final_score is not None) == [0.8, 0.9]


async def test_failed_questions_keep_distinct_agent_run_ids(
    database, settings, scripted, tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    async def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ValueError("private content must not be logged")

    llm = scripted(make_responder("contact", [], "", []))
    service = AgentService(settings, database, llm)
    monkeypatch.setattr(service.graph, "ainvoke", fail)
    dataset = tmp_path / "fail.json"
    dataset.write_text(json.dumps([{"question": "Same question"}] * 2))
    run = await run_evaluation(service, await create_evaluation_run(service, dataset), dataset)
    with database.transaction() as s:
        repo = RunRepository(s)
        rows = repo.evaluations(run.id)
        assert len(rows) == 2 and all(r.agent_run_id is not None for r in rows)
        assert rows[0].agent_run_id != rows[1].agent_run_id
        for row in rows:
            assert row.agent_run_id is not None
            agent = repo.get_agent(row.agent_run_id)
            assert agent.status == "failed" and not row.schema_compliant
