from statistics import mean
from typing import Any

from Models.records import Evaluation
from utils.database import SQLSession


def _avg(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(mean(vals), 4) if vals else None


def summarize_evaluations(rows: list[Evaluation], latencies_ms: list[int | None] | None = None) -> dict[str, Any]:
    """Aggregate judge scores into dashboard metrics (all fractions in [0, 1])."""
    lat = [v for v in (latencies_ms or [r.latency_ms for r in rows]) if v is not None]
    return {
        "count": len(rows),
        "agent_accuracy": _avg([r.correctness for r in rows]),
        "relevance": _avg([r.relevance for r in rows]),
        "groundedness": _avg([r.groundedness for r in rows]),
        "completeness": _avg([r.completeness for r in rows]),
        "hallucination_rate": _avg([r.hallucination for r in rows]),
        "tool_accuracy": _avg([r.tool_accuracy for r in rows]),
        "schema_compliance": round(sum(r.schema_compliant for r in rows) / len(rows), 4) if rows else None,
        "avg_final_score": _avg([r.final_score for r in rows]),
        "avg_latency_s": round(mean(lat) / 1000, 3) if lat else None,
    }


def platform_metrics(session: SQLSession) -> dict[str, Any]:
    evals = [
        Evaluation.model_validate(r)
        for r in session.all("SELECT * FROM evaluations ORDER BY created_at DESC LIMIT 1000")
    ]
    agent = session.one(
        """SELECT count(*) runs, avg(latency_ms) latency, avg(iterations) iterations,
           coalesce(sum(prompt_tokens),0) prompt, coalesce(sum(completion_tokens),0) completion,
           count(*) FILTER (WHERE status='failed') failed FROM agent_runs"""
    )
    scrape = session.one(
        """SELECT count(*) runs, count(*) FILTER (WHERE status='failed') failed,
           avg(duration_ms) duration, max(completed_at) last_completed FROM scrape_runs"""
    )
    return {
        "evaluation": summarize_evaluations(evals),
        "agents": {
            "runs": agent["runs"],
            "failed": agent["failed"],
            "avg_latency_s": round(float(agent["latency"]) / 1000, 3) if agent["latency"] is not None else None,
            "avg_iterations": round(float(agent["iterations"]), 2) if agent["iterations"] is not None else None,
            "prompt_tokens": int(agent["prompt"]),
            "completion_tokens": int(agent["completion"]),
        },
        "scraping": {
            "runs": scrape["runs"],
            "failed": scrape["failed"],
            "avg_duration_s": round(float(scrape["duration"]) / 1000, 2) if scrape["duration"] is not None else None,
            "last_completed_at": scrape["last_completed"],
        },
    }
