from datetime import timedelta

from tests.factories import BASE_TIME, conversation
from utils.feed_db import persist_conversations
from utils.repositories import MessageRepository


async def test_only_latest_three_messages_are_retained(database) -> None:  # type: ignore[no-untyped-def]
    old = conversation("1@c.us", "A", None, ["m0", "m1", "m2"], prefix="old")
    new = conversation("1@c.us", "A", None, ["n0", "n1"], start=BASE_TIME + timedelta(hours=1), prefix="new")
    with database.transaction() as s:
        persist_conversations(s, [old], keep=3)
        stats = persist_conversations(s, [new], keep=3)
        cid = (MessageRepository(s).recent_conversations(limit=1))[0][0].id
        msgs = MessageRepository(s).recent_for_contact(cid, 10)
    assert stats["messages_saved"] == 2 and stats["messages_pruned"] == 2
    assert [m.message_text for m in msgs] == ["n1", "n0", "m2"]


async def test_search_and_recent_conversations(database) -> None:  # type: ignore[no-untyped-def]
    convs = [
        conversation("1@c.us", "A", None, ["can we schedule a meeting", "sure"]),
        conversation("2@c.us", "B", None, ["job interview next week"], start=BASE_TIME + timedelta(hours=2)),
    ]
    with database.transaction() as s:
        persist_conversations(s, convs, keep=3)
        repo = MessageRepository(s)
        hits = repo.search_text("meeting")
        recent = repo.recent_conversations(limit=10)
    assert [c.contact_name for _, c in hits] == ["A"]
    assert [c.contact_name for c, _ in recent] == ["B", "A"]
    assert [m.message_text for m in recent[1][1]] == ["sure", "can we schedule a meeting"]
