from app.db.repositories.contact_repository import ContactRepository
from app.db.repositories.quality_repository import QualityRepository
from app.schemas.contact import ContactIn
from app.services.scrape_service import persist_conversations
from tests.factories import conversation


async def test_upsert_is_idempotent_and_updates_name(session_factory) -> None:  # type: ignore[no-untyped-def]
    conv = conversation("14155550123@c.us", "John", "+14155550123", ["hi"])
    async with session_factory() as s:
        first = await persist_conversations(s, [conv], keep=3)
        conv.contact.contact_name = "John Smith"
        second = await persist_conversations(s, [conv], keep=3)
        await s.commit()
        total, rows = await ContactRepository(s).search(name="smith")
    assert first["contacts_saved"] == 1 and second["contacts_saved"] == 0
    assert first["messages_saved"] == 1 and second["messages_saved"] == 0
    assert total == 1 and rows[0][0].contact_name == "John Smith" and rows[0][1] == 1


async def test_search_filters(session_factory) -> None:  # type: ignore[no-untyped-def]
    convs = [
        conversation("14155550123@c.us", "John", "+14155550123", ["a"]),
        conversation("919876543210@c.us", "Priya", "+919876543210", ["b"]),
        conversation("555@lid", "Anon", None, ["c"]),
    ]
    async with session_factory() as s:
        await persist_conversations(s, convs, keep=3)
        await s.commit()
        repo = ContactRepository(s)
        assert (await repo.search(phone_prefix="+1"))[0] == 1
        assert (await repo.search(has_phone=False))[0] == 1
        assert (await repo.find_by_name_or_id("priya")).contact_name == "Priya"  # type: ignore[union-attr]
        report = await QualityRepository(s).report(3)
    assert report["contacts"] == 3 and report["missing_phone_numbers"] == 1
    assert report["quality_score"] == 1.0


async def test_count_filters_phone(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        repo = ContactRepository(session)
        await repo.upsert(ContactIn(whatsapp_id="14155550101@c.us", contact_name="A", phone_number="+14155550101"))
        await repo.upsert(ContactIn(whatsapp_id="919876543210@c.us", contact_name="B", phone_number="+919876543210"))
        await repo.upsert(ContactIn(whatsapp_id="84213@lid", contact_name=None, phone_number=None))
        await session.commit()
        assert await repo.count() == 3
        assert await repo.count(has_phone=False) == 1
        assert await repo.count(has_phone=True) == 2
        assert await repo.count(phone_prefix="1") == 1
        assert await repo.count(phone_prefix="+91") == 1
