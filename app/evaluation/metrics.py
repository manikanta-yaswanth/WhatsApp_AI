from statistics import mean
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentRun, Evaluation, ScrapeRun


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


async def platform_metrics(session: AsyncSession) -> dict[str, Any]:
    evals = list(
        (await session.execute(select(Evaluation).order_by(Evaluation.created_at.desc()).limit(1000))).scalars()
    )
    agent = (
        await session.execute(
            select(
                func.count(AgentRun.id),
                func.avg(AgentRun.latency_ms),
                func.avg(AgentRun.iterations),
                func.coalesce(func.sum(AgentRun.prompt_tokens), 0),
                func.coalesce(func.sum(AgentRun.completion_tokens), 0),
                func.count(AgentRun.id).filter(AgentRun.status == "failed"),
            )
        )
    ).one()
    scrape = (
        await session.execute(
            select(
                func.count(ScrapeRun.id),
                func.count(ScrapeRun.id).filter(ScrapeRun.status == "failed"),
                func.avg(ScrapeRun.duration_ms),
                func.max(ScrapeRun.completed_at),
            )
        )
    ).one()
    return {
        "evaluation": summarize_evaluations(evals),
        "agents": {
            "runs": agent[0],
            "failed": agent[5],
            "avg_latency_s": round(float(agent[1]) / 1000, 3) if agent[1] is not None else None,
            "avg_iterations": round(float(agent[2]), 2) if agent[2] is not None else None,
            "prompt_tokens": int(agent[3]),
            "completion_tokens": int(agent[4]),
        },
        "scraping": {
            "runs": scrape[0],
            "failed": scrape[1],
            "avg_duration_s": round(float(scrape[2]) / 1000, 2) if scrape[2] is not None else None,
            "last_completed_at": scrape[3],
        },
    }
