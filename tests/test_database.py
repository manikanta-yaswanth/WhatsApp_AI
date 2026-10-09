import uuid
from pathlib import Path

import psycopg2
import pytest

from Models.schema import ContactIn
from tests.factories import BASE_TIME, conversation
from utils.database import DatabaseUtil
from utils.feed_db import persist_conversations
from utils.repositories import ContactRepository, MessageRepository
from utils.settings import Settings


def test_separate_dotenv_keys_ignore_os_user(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / ".env"
    env.write_text("database=demo_test\nhost=127.0.0.1\nport=55432\nuser=local_role\npassword=test-only\n")
    monkeypatch.setenv("USER", "wrong_os_user")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_USER", raising=False)
    settings = Settings(_env_file=env)
    assert settings.db_config == {
        "dbname": "demo_test",
        "host": "127.0.0.1",
        "port": 55432,
        "user": "local_role",
        "password": "test-only",
    }
    assert "test-only" not in repr(settings)
    monkeypatch.setenv("DB_USER", "env_role")
    assert Settings(_env_file=env).db_config["user"] == "env_role"


def test_url_override_accepts_legacy_driver_and_special_password() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://role:abc%40%3A%2F@localhost:55432/db_test",
        database="ignored",
        _env_file=None,
    )
    assert settings.db_config["dbname"] == "db_test"
    assert settings.db_config["password"] == "abc@:/"
    assert "abc%40" not in repr(settings)


def test_connection_commit_rollback_and_close(database: DatabaseUtil) -> None:
    with pytest.raises(RuntimeError):
        with database.transaction() as s:
            failed_connection = s.connection
            ContactRepository(s).upsert(ContactIn(whatsapp_id="rollback@lid"))
            raise RuntimeError("rollback")
    assert failed_connection.closed
    with database.transaction() as s:
        assert ContactRepository(s).count() == 0
        connection = s.connection
        ContactRepository(s).upsert(ContactIn(whatsapp_id="commit@lid"))
    assert connection.closed
    with database.transaction() as s:
        assert ContactRepository(s).count() == 1


def test_sql_error_rolls_back_all_rows(database: DatabaseUtil) -> None:
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        with database.transaction() as s:
            ContactRepository(s).upsert(ContactIn(whatsapp_id="atomic@lid"))
            s.execute(
                "INSERT INTO evaluations (id,question,agent_run_id) VALUES (%s,%s,%s)",
                (uuid.uuid4(), "test", uuid.uuid4()),
            )
    with database.transaction() as s:
        assert ContactRepository(s).count() == 0


def test_initializer_preserves_rows_ids_and_alembic_marker(database: DatabaseUtil) -> None:
    with database.transaction() as s:
        cid, _ = ContactRepository(s).upsert(ContactIn(whatsapp_id="existing@lid", contact_name="Existing"))
        s.execute("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
        s.execute("INSERT INTO alembic_version VALUES ('0001')")
    database.initialize()
    database.initialize()
    with database.transaction() as s:
        assert ContactRepository(s).get(cid).contact_name == "Existing"  # type: ignore[union-attr]
        assert s.scalar("SELECT version_num FROM alembic_version") == "0001"


def test_query_values_cannot_execute_sql(database: DatabaseUtil) -> None:
    attack = "'; DROP TABLE whatsapp_contacts; --"
    with database.transaction() as s:
        repo = ContactRepository(s)
        repo.upsert(ContactIn(whatsapp_id="safe@lid", contact_name=attack))
        assert repo.search(name=attack)[0] == 1
        assert repo.search(name="' OR 1=1 --")[0] == 0
        assert repo.count() == 1


def test_fresh_schema_has_one_scraped_data_table_and_views(database: DatabaseUtil) -> None:
    with database.transaction() as s:
        tables = s.all(
            """SELECT table_name, table_type FROM information_schema.tables WHERE table_schema=current_schema()"""
        )
    assert {r["table_name"] for r in tables if r["table_type"] == "BASE TABLE"} == {
        "whatsapp_contacts",
        "scrape_runs",
        "agent_runs",
        "evaluation_runs",
        "evaluations",
    }
    assert {r["table_name"] for r in tables if r["table_type"] == "VIEW"} == {
        "contact_records",
        "conversation_records",
        "message_records",
    }


def test_legacy_migration_preserves_messages_ids_categories_and_backups(database: DatabaseUtil) -> None:
    cid, vid = uuid.uuid4(), uuid.uuid4()
    message_ids = [uuid.uuid4() for _ in range(5)]
    with database.transaction() as s:
        s.execute("CREATE TABLE contacts AS SELECT * FROM contact_records WITH NO DATA")
        s.execute("CREATE TABLE conversations AS SELECT * FROM conversation_records WITH NO DATA")
        s.execute("CREATE TABLE messages AS SELECT * FROM message_records WITH NO DATA")
        s.execute(
            """INSERT INTO contacts (id,whatsapp_id,contact_name,phone_number,is_group,created_at,updated_at)
               VALUES (%s,%s,%s,%s,false,now(),now())""",
            (cid, "legacy@lid", "Original", None),
        )
        s.execute(
            """INSERT INTO conversations (id,contact_id,unread_count,last_message_at,last_scraped_at,
               category,action,category_confidence,category_reason,classified_at)
               VALUES (%s,%s,2,%s,now(),'personal','NO_ACTION',0.9,'friendly',now())""",
            (vid, cid, BASE_TIME),
        )
        for i, mid in enumerate(message_ids):
            s.execute(
                """INSERT INTO messages (id,contact_id,whatsapp_message_id,sender_type,sender_name,
                   message_type,message_text,message_timestamp,scraped_at)
                   VALUES (%s,%s,%s,'contact',NULL,'text',%s,%s,now())""",
                (mid, cid, f"legacy-{i}", f"old {i}", BASE_TIME),
            )
    database.initialize()
    database.initialize()
    with database.transaction() as s:
        contact = ContactRepository(s).get(cid)
        conv = ContactRepository(s).get_conversation(cid)
        assert contact is not None and contact.contact_name == "Original"
        assert conv is not None and conv.id == vid and conv.unread_count == 2 and conv.category == "personal"
        assert {m.id for m in MessageRepository(s).recent_for_contact(cid, 10)} == set(message_ids)
        assert s.scalar("SELECT count(*) FROM contacts") == 1
        assert s.scalar("SELECT count(*) FROM messages") == 5
        assert s.scalar("SELECT count(*) FROM conversations") == 1
        persist_conversations(s, [conversation("legacy@lid", "Updated", None, ["new"])], 3)
    database.initialize()
    with database.transaction() as s:
        assert ContactRepository(s).get(cid).contact_name == "Updated"  # type: ignore[union-attr]
        assert MessageRepository(s).count() == 3
        assert ContactRepository(s).get_conversation(cid).category is None  # type: ignore[union-attr]
        assert s.scalar("SELECT contact_name FROM contacts") == "Original"
        assert s.scalar("SELECT count(*) FROM messages") == 5
