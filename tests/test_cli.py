import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import main as cli
from scraper.parser import ParseResult
from scraper.whatsapp import ScrapeOutput
from tests.factories import conversation
from tests.test_agents import make_responder
from utils.database import DatabaseUtil
from utils.feed_db import ScrapeService
from utils.repositories import ContactRepository, MessageRepository, RunRepository


class FakeScraper:
    async def scrape(self) -> ScrapeOutput:
        result = ParseResult()
        result.conversations = [conversation("1@c.us", "John", None, ["a", "b", "c", "d"])]
        return ScrapeOutput(method="store", result=result, contacts_found=1, messages_found=4)


@pytest.fixture
def run_cli(database, settings, scripted, monkeypatch, capsys):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(DatabaseUtil, "from_settings", lambda _: database)
    monkeypatch.setattr(cli, "ScrapeService", lambda s, db: ScrapeService(s, db, FakeScraper()))
    model = scripted(make_responder("contact", [("count_contacts", {})], "1 contact.", [0.9]))
    monkeypatch.setattr(cli, "build_chat_model", lambda *args, **kwargs: model)

    def run(*args):  # type: ignore[no-untyped-def]
        assert cli.main(list(args)) == 0
        return json.loads(capsys.readouterr().out)

    return run


def test_cli_scrape_contacts_messages_query_metrics(run_cli) -> None:  # type: ignore[no-untyped-def]
    assert run_cli("health")["connected"] == 1
    run = run_cli("scrape")
    assert run["status"] == "completed" and run["messages_saved"] == 3 and run["contacts_saved"] == 1
    contacts = run_cli("contacts", "--name", "jo")
    assert contacts["total"] == 1 and contacts["contacts"][0]["message_count"] == 3
    cid = contacts["contacts"][0]["id"]
    assert [m["message_text"] for m in run_cli("messages", "--contact", cid)] == ["d", "c", "b"]
    assert len(run_cli("messages", "--query", "d")) == 1
    assert len(run_cli("messages")) == 1
    response = run_cli("query", "how many contacts?")
    assert response["answer"] == "1 contact." and response["judge_score"] == 0.9
    assert run_cli("data-quality")["contacts"] == 1
    metrics = run_cli("metrics")
    assert metrics["agents"]["runs"] == 1 and metrics["scraping"]["runs"] == 1


def test_cli_seed_is_idempotent_and_init_preserves_data(run_cli) -> None:  # type: ignore[no-untyped-def]
    assert run_cli("seed-demo")["contacts_saved"] == 4
    assert run_cli("seed-demo")["contacts_saved"] == 0
    assert run_cli("init-db")["status"] == "initialized"
    assert run_cli("contacts")["total"] == 4
    assert run_cli("contacts", "--missing-phone")["total"] == 2
    assert run_cli("contacts", "--phone-prefix", "+1")["total"] == 1


def test_cli_classify_summarize_evaluate(run_cli, scripted, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import tool_call

    run_cli("scrape")
    cid = run_cli("contacts")["contacts"][0]["id"]

    def respond(messages, tools):  # type: ignore[no-untyped-def]
        if tools == ["ConversationClassification"]:
            return tool_call(
                "ConversationClassification",
                {"category": "personal", "action": "NO_ACTION", "confidence": 0.8, "reason": "friendly"},
            )
        return tool_call("Summary", {"summary": "A friendly chat.", "action": "NO_ACTION"})

    model = scripted(respond)
    monkeypatch.setattr(cli, "build_chat_model", lambda *args, **kwargs: model)
    classified = run_cli("classify", "--contact", cid)
    assert classified[0]["category"] == "personal"
    assert run_cli("summarize", cid)["summary"] == "A friendly chat."
    dataset = tmp_path / "questions.json"
    dataset.write_text(json.dumps([{"question": "count?", "expected_tools": ["count_contacts"]}]))
    model = scripted(make_responder("contact", [("count_contacts", {})], "1 contact.", [1.0]))
    monkeypatch.setattr(cli, "build_chat_model", lambda *args, **kwargs: model)
    evaluation = run_cli("evaluate", "--dataset", str(dataset))
    assert evaluation["status"] == "completed" and evaluation["metrics"]["count"] == 1


def test_graph_export_requires_no_database_or_openai(monkeypatch, capsys, tmp_path: Path) -> None:
    from PIL import Image

    from utils.settings import Settings

    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None, openai_api_key=None))
    monkeypatch.setattr(DatabaseUtil, "transaction", lambda _: pytest.fail("No DB access during export"))
    assert cli.main(["graph", "--output-dir", str(tmp_path)]) == 0
    paths = json.loads(capsys.readouterr().out)
    assert len(paths) == 7
    for path in paths:
        with Image.open(path) as image:
            assert image.format == "PNG" and image.width > 0


def test_cli_missing_key_and_unknown_contact_fail_cleanly(database, settings, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(DatabaseUtil, "from_settings", lambda _: database)
    assert cli.main(["query", "how many?"]) == 1
    assert "OPENAI_API_KEY" in capsys.readouterr().err
    with pytest.raises(SystemExit) as error:
        cli.main(["messages", "--contact", "not-a-uuid"])
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        cli.main(["contacts", "--limit", "-1"])
    assert error.value.code == 2


def test_help_runs_from_another_directory_without_configuration(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "main.py"), "--help"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0 and "init-db" in result.stdout and "scrape" in result.stdout
    assert "webhook" not in result.stdout


async def test_scrape_failure_is_recorded(database, settings) -> None:  # type: ignore[no-untyped-def]
    class BrokenScraper:
        async def scrape(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("private message should never be logged")

    service = ScrapeService(settings, database, BrokenScraper())  # type: ignore[arg-type]
    run = service.create_run()
    result = await service.execute(run.id)
    assert result.status == "failed" and result.error_message == "RuntimeError"


def test_missing_contact_summary_returns_nonzero(run_cli, capsys) -> None:  # type: ignore[no-untyped-def]
    assert cli.main(["summarize", str(uuid.uuid4())]) == 1
    assert "Contact not found" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["webhook"], ["scrape", "--webhook-url", "http://127.0.0.1:8080/webhook"]])
def test_removed_ingestion_options_fail_before_configuration(args, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(cli, "get_settings", lambda: pytest.fail("Removed options must fail before setup"))
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2


def test_cli_direct_scrape_deduplicates_and_records_each_run(run_cli) -> None:  # type: ignore[no-untyped-def]
    first = run_cli("scrape")
    second = run_cli("scrape")
    assert first["status"] == second["status"] == "completed"
    assert first["id"] != second["id"]
    assert second["contacts_saved"] == second["messages_saved"] == 0
    contacts = run_cli("contacts")
    assert contacts["total"] == 1 and contacts["contacts"][0]["message_count"] == 3
    cid = contacts["contacts"][0]["id"]
    assert [m["message_text"] for m in run_cli("messages", "--contact", cid)] == ["d", "c", "b"]
    assert run_cli("metrics")["scraping"]["runs"] == 2


async def test_direct_scrape_rolls_back_all_conversations_on_storage_failure(database, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class TwoContactScraper:
        async def scrape(self) -> ScrapeOutput:
            output = await FakeScraper().scrape()
            output.result.conversations.append(conversation("2@c.us", "Second", None, ["a"]))
            output.contacts_found = 2
            output.messages_found = 5
            return output

    original = MessageRepository.insert_new
    calls = 0

    def insert(self, cid, messages):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 2:
            self.session.execute("SELECT * FROM nonexistent_test_table")
        return original(self, cid, messages)

    monkeypatch.setattr(MessageRepository, "insert_new", insert)
    service = ScrapeService(settings, database, TwoContactScraper())
    run = service.create_run()
    result = await service.execute(run.id)
    assert calls == 2 and result.status == "failed"
    with database.transaction() as session:
        assert ContactRepository(session).count() == MessageRepository(session).count() == 0
        assert session.scalar("SELECT COUNT(*) FROM conversations") == 0
        saved = RunRepository(session).get_scrape(run.id)
        assert saved is not None and saved.status == "failed"
