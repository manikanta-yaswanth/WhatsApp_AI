"""Run the evaluation dataset through the agent and the LLM judge.

Usage: uv run python scripts/evaluate.py [--dataset evaluation/questions.json]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings  # noqa: E402
from app.db.database import get_engine, get_session_factory  # noqa: E402
from app.evaluation.datasets import DEFAULT_DATASET  # noqa: E402
from app.evaluation.judge import create_evaluation_run, run_evaluation  # noqa: E402
from app.llm.client import build_chat_model  # noqa: E402
from app.services.agent_service import AgentService  # noqa: E402
from app.utils.logging import configure_logging  # noqa: E402


async def main(dataset: str) -> int:
    configure_logging()
    s = get_settings()
    service = AgentService(s, get_session_factory(), build_chat_model(s), build_chat_model(s, judge=True))
    run_id = await create_evaluation_run(service, dataset)
    run = await run_evaluation(service, run_id, dataset)
    print(
        json.dumps({"evaluation_id": str(run.id), "status": run.status, "metrics": run.metrics}, indent=2, default=str)
    )
    await get_engine().dispose()
    return 0 if run.status == "completed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    raise SystemExit(asyncio.run(main(parser.parse_args().dataset)))
