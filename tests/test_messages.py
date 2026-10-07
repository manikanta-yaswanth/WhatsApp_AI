from datetime import timedelta

from app.db.repositories.message_repository import MessageRepository
from app.services.scrape_service import persist_conversations
from tests.factories import BASE_TIME, conversation


async def test_only_latest_three_messages_are_retained(session_factory) -> None:  # type: ignore[no-untyped-def]
    old = conversation("1@c.us", "A", None, ["m0", "m1", "m2"], prefix="old")
    new = conversation("1@c.us", "A", None, ["n0", "n1"], start=BASE_TIME + timedelta(hours=1), prefix="new")
    async with session_factory() as s:
        await persist_conversations(s, [old], keep=3)
        stats = await persist_conversations(s, [new], keep=3)
        await s.commit()
        cid = (await MessageRepository(s).recent_conversations(limit=1))[0][0].id
        msgs = await MessageRepository(s).recent_for_contact(cid, 10)
    assert stats["messages_saved"] == 2 and stats["messages_pruned"] == 2
    assert [m.message_text for m in msgs] == ["n1", "n0", "m2"]


async def test_search_and_recent_conversations(session_factory) -> None:  # type: ignore[no-untyped-def]
    convs = [
        conversation("1@c.us", "A", None, ["can we schedule a meeting", "sure"]),
        conversation("2@c.us", "B", None, ["job interview next week"], start=BASE_TIME + timedelta(hours=2)),
    ]
    async with session_factory() as s:
        await persist_conversations(s, convs, keep=3)
        await s.commit()
        repo = MessageRepository(s)
        hits = await repo.search_text("meeting")
        recent = await repo.recent_conversations(limit=10)
    assert [c.contact_name for _, c in hits] == ["A"]
    assert [c.contact_name for c, _ in recent] == ["B", "A"]
    assert [m.message_text for m in recent[1][1]] == ["sure", "can we schedule a meeting"]
