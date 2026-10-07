import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_agent_service, get_scrape_service
from app.config.settings import get_settings
from app.db.database import get_session
from app.main import app
from app.scraper.parser import ParseResult
from app.scraper.whatsapp import ScrapeOutput
from app.services.agent_service import AgentService
from app.services.scrape_service import ScrapeService
from tests.factories import conversation
from tests.test_agents import make_responder


class FakeScraper:
    async def scrape(self) -> ScrapeOutput:
        r = ParseResult()
        r.conversations = [conversation("14155550123@c.us", "John", "+14155550123", ["a", "b", "c", "d"])]
        return ScrapeOutput(method="store", result=r, contacts_found=1, messages_found=4)


@pytest.fixture
async def client(session_factory, settings, scripted):  # type: ignore[no-untyped-def]
    async def _session():  # type: ignore[no-untyped-def]
        async with session_factory() as s:
            yield s

    scrape = ScrapeService(settings, session_factory, scraper=FakeScraper())  # type: ignore[arg-type]
    llm = scripted(make_responder("contact", [("count_contacts", {})], "1 contact.", [0.9]))
    agent = AgentService(settings, session_factory, llm)
    app.dependency_overrides.update(
        {
            get_session: _session,
            get_settings: lambda: settings,
            get_scrape_service: lambda: scrape,
            get_agent_service: lambda: agent,
        }
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t/api/v1") as c:
        c.scrape_service = scrape  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


async def test_scrape_contacts_messages_agent_flow(client) -> None:  # type: ignore[no-untyped-def]
    assert (await client.get("/health")).json()["database"] == "ok"
    started = (await client.post("/scrape/start")).json()

    for _ in range(50):
        status = (await client.get(f"/scrape/{started['run_id']}")).json()
        if status["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.05)
    assert status["status"] == "completed", status
    assert status["messages_saved"] == 3 and status["contacts_saved"] == 1

    contacts = (await client.get("/contacts", params={"name": "jo"})).json()
    assert contacts["total"] == 1 and contacts["items"][0]["message_count"] == 3
    cid = contacts["items"][0]["id"]
    msgs = (await client.get(f"/contacts/{cid}/messages")).json()
    assert [m["message_text"] for m in msgs] == ["d", "c", "b"]

    answer = (await client.post("/agents/query", json={"query": "how many contacts?"})).json()
    assert answer["answer"] == "1 contact." and answer["judge_score"] == 0.9

    metrics = (await client.get("/metrics")).json()
    assert metrics["agents"]["runs"] == 1 and metrics["scraping"]["runs"] == 1
    assert (await client.get("/agents/data-quality")).json()["contacts"] == 1


async def test_api_key_enforced(client, settings) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    secured = settings.model_copy(update={"api_key": SecretStr("k")})
    app.dependency_overrides[get_settings] = lambda: secured
    assert (await client.get("/contacts")).status_code == 401
    assert (await client.get("/contacts", headers={"X-API-Key": "k"})).status_code == 200
    assert (await client.get("/health")).status_code == 200
