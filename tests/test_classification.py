from datetime import timedelta

from Models.schema import ConversationClassification
from tests.factories import BASE_TIME, conversation
from utils import tools
from utils.feed_db import persist_conversations
from utils.repositories import ContactRepository


def result() -> ConversationClassification:
    return ConversationClassification(category="personal", action="NO_ACTION", confidence=0.9, reason="Friendly")


async def test_only_unclassified_skips_current_and_reclassifies_backfills(database, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = 0

    async def classify(*args):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return result()

    monkeypatch.setattr(tools, "classify_contact", classify)
    conv = conversation("1@c.us", "John", None, ["a"], start=BASE_TIME + timedelta(days=2))
    with database.transaction() as s:
        persist_conversations(s, [conv], 3)
    assert len(await tools.classify_conversations(database, settings, None)) == 1  # type: ignore[arg-type]
    with database.transaction() as s:
        persist_conversations(s, [conv], 3)
    assert await tools.classify_conversations(database, settings, None, only_unclassified=True) == []  # type: ignore[arg-type]
    assert len(await tools.classify_conversations(database, settings, None)) == 1  # type: ignore[arg-type]
    assert calls == 1
    older = conversation("1@c.us", "John", None, ["backfilled"], prefix="backfill", start=BASE_TIME)
    with database.transaction() as s:
        persist_conversations(s, [older], 3)
    assert len(await tools.classify_conversations(database, settings, None, only_unclassified=True)) == 1  # type: ignore[arg-type]
    assert calls == 2


async def test_concurrent_scrape_does_not_save_old_classification(database, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    old = conversation("1@c.us", "John", None, ["old"])
    new = conversation("1@c.us", "John", None, ["new"], prefix="new", start=BASE_TIME + timedelta(hours=1))
    with database.transaction() as s:
        persist_conversations(s, [old], 3)
        cid = ContactRepository(s).search()[1][0][0].id

    async def classify(*args):  # type: ignore[no-untyped-def]
        with database.transaction() as s:
            persist_conversations(s, [new], 3)
        return result()

    monkeypatch.setattr(tools, "classify_contact", classify)
    assert await tools.classify_conversations(database, settings, None) == []  # type: ignore[arg-type]
    with database.transaction() as s:
        conv = ContactRepository(s).get_conversation(cid)
        assert conv is not None and conv.classified_at is None and conv.category is None

    async def unchanged(*args):  # type: ignore[no-untyped-def]
        return result()

    monkeypatch.setattr(tools, "classify_contact", unchanged)
    assert len(await tools.classify_conversations(database, settings, None, only_unclassified=True)) == 1  # type: ignore[arg-type]
