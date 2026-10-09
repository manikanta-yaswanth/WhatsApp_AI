"""CLI entrypoint, following AI_DATA_AGENTS' root-main convention."""

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

import psycopg2
from pydantic import BaseModel, ValidationError

from evaluation.datasets import DEFAULT_DATASET
from evaluation.judge import create_evaluation_run, run_evaluation
from evaluation.metrics import platform_metrics
from utils.agent_runner import AgentService, NotFoundError
from utils.database import DatabaseUtil
from utils.feed_db import ScrapeService
from utils.llm_pick import LLMNotConfiguredError, build_chat_model
from utils.logging import configure_logging
from utils.repositories import ContactRepository, MessageRepository, QualityRepository
from utils.settings import Settings, get_settings


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1 or number > 500:
        raise argparse.ArgumentTypeError("Choose a value between 1 and 500")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="WhatsApp Conversation Intelligence — PostgreSQL/Supabase + LangGraph")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("init-db", "health", "seed-demo", "data-quality", "metrics"):
        commands.add_parser(name)
    commands.add_parser("scrape", help="Scrape WhatsApp Web and save directly to PostgreSQL")
    login = commands.add_parser("login", help="Open a visible browser for manual WhatsApp QR login")
    login.add_argument("--timeout", type=positive_int, default=300)
    contacts = commands.add_parser("contacts")
    contacts.add_argument("--name")
    contacts.add_argument("--phone-prefix")
    contacts.add_argument("--missing-phone", action="store_true")
    contacts.add_argument("--limit", type=positive_int, default=50)
    messages = commands.add_parser("messages")
    messages.add_argument("--contact", type=uuid.UUID)
    messages.add_argument("--query")
    messages.add_argument("--limit", type=positive_int, default=20)
    query = commands.add_parser("query")
    query.add_argument("question")
    query.add_argument("--no-judge", action="store_true")
    classify = commands.add_parser("classify")
    classify.add_argument("--contact", type=uuid.UUID, action="append")
    classify.add_argument("--limit", type=positive_int, default=50)
    classify.add_argument("--only-unclassified", action="store_true")
    commands.add_parser("summarize").add_argument("contact_id", type=uuid.UUID)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--dataset", default=str(DEFAULT_DATASET))
    graph = commands.add_parser("graph", help="Export router and specialist graphs as PNGs without OpenAI calls")
    graph.add_argument("--output-dir", default="data/graphs")
    return root


def emit(value: Any) -> None:
    def encode(item: Any) -> Any:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        return str(item)

    print(json.dumps(value, default=encode, ensure_ascii=False, indent=2))


async def dispatch(args: argparse.Namespace, settings: Settings) -> int:
    if args.command == "login":
        from scripts.login import main as login

        return await login(args.timeout)
    if args.command == "graph":
        from utils.graph_export import export_graphs

        emit(export_graphs(args.output_dir, settings))
        return 0
    database = DatabaseUtil.from_settings(settings)
    if args.command == "init-db":
        await asyncio.to_thread(database.initialize)
        emit({"status": "initialized", **await asyncio.to_thread(database.health)})
        return 0
    if args.command == "health":
        emit(await asyncio.to_thread(database.health))
        return 0
    if args.command == "seed-demo":
        from scripts.seed_demo import seed

        emit(await asyncio.to_thread(seed, database))
        return 0
    if args.command == "scrape":
        scrape_service = ScrapeService(settings, database)
        run = await asyncio.to_thread(scrape_service.create_run)
        run = await scrape_service.execute(run.id)
        emit(run)
        return 0 if run.status == "completed" else 1
    if args.command in {"contacts", "messages", "data-quality", "metrics"}:

        def read() -> Any:
            with database.transaction() as s:
                if args.command == "contacts":
                    total, rows = ContactRepository(s).search(
                        name=args.name,
                        phone_prefix=args.phone_prefix,
                        has_phone=False if args.missing_phone else None,
                        limit=args.limit,
                    )
                    return {
                        "total": total,
                        "contacts": [
                            {**c.model_dump(), "message_count": n, "last_message_at": last, "category": cat}
                            for c, n, last, cat in rows
                        ],
                    }
                if args.command == "messages":
                    repo = MessageRepository(s)
                    if args.contact:
                        return repo.recent_for_contact(args.contact, args.limit)
                    if args.query:
                        return [{"message": m, "contact": c} for m, c in repo.search_text(args.query, args.limit)]
                    return [
                        {"contact": c, "messages": msgs}
                        for c, msgs in repo.recent_conversations(
                            limit=args.limit, per_contact=settings.max_messages_per_contact
                        )
                    ]
                if args.command == "data-quality":
                    return QualityRepository(s).report(settings.max_messages_per_contact)
                return platform_metrics(s)

        emit(await asyncio.to_thread(read))
        return 0
    llm = build_chat_model(settings)
    service = AgentService(settings, database, llm, build_chat_model(settings, judge=True))
    if args.command == "query":
        from Models.schema import AgentQuery

        request = AgentQuery(query=args.question, judge=not args.no_judge)
        response, _ = await service.run(request.query, judge=request.judge)
        emit(response)
    elif args.command == "classify":
        emit(await service.classify(args.contact, args.limit, args.only_unclassified))
    elif args.command == "summarize":
        emit(await service.summarize(args.contact_id))
    elif args.command == "evaluate":
        rid = await create_evaluation_run(service, args.dataset)
        evaluation_run = await run_evaluation(service, rid, args.dataset)
        emit(evaluation_run)
        return 0 if evaluation_run.status == "completed" else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    configure_logging()
    try:
        return asyncio.run(dispatch(args, get_settings()))
    except psycopg2.Error:
        print("PostgreSQL operation failed. Check your .env settings and run init-db first.", file=sys.stderr)
    except LLMNotConfiguredError:
        print("Set OPENAI_API_KEY in your local .env for agent/evaluation commands.", file=sys.stderr)
    except ValidationError:
        print("Invalid configuration or input. Check .env and command arguments.", file=sys.stderr)
    except NotFoundError:
        print("Contact not found.", file=sys.stderr)
    except (OSError, RuntimeError, ValueError):
        print("Command failed. Check the database schema, browser profile or input dataset.", file=sys.stderr)
    except KeyboardInterrupt:
        return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
