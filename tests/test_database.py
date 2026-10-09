import uuid
from pathlib import Path

import psycopg2
import pytest
from psycopg2 import sql
from pydantic import ValidationError

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


def test_supabase_url_retains_pooler_username_ssl_and_private_schema() -> None:
    settings = Settings(
        database_url="postgresql://postgres.example_ref:test%40password@aws-0-example.pooler.supabase.com:5432/postgres?sslmode=require",
        db_schema="whatsapp_demo",
        _env_file=None,
    )
    assert settings.db_config["user"] == "postgres.example_ref"
    assert settings.db_config["port"] == "5432"
    assert settings.db_config["sslmode"] == "require"
    assert settings.db_schema == "whatsapp_demo"
    assert "test%40password" not in repr(settings)


@pytest.mark.parametrize("schema", ["public; DROP TABLE contacts", "a.b", "../data", "A", "x" * 64])
def test_invalid_schema_is_rejected(schema: str) -> None:
    with pytest.raises(ValidationError):
        Settings(db_schema=schema, _env_file=None)


def test_blank_schema_keeps_legacy_search_path() -> None:
    assert Settings(db_schema="", _env_file=None).db_schema is None


def test_private_schema_isolated_idempotent_and_rls_enabled(database: DatabaseUtil) -> None:
    name = "wa_private_" + uuid.uuid4().hex
    private = DatabaseUtil(database._config, schema=name)
    try:
        private.initialize()
        with private.transaction() as s:
            s.execute("GRANT SELECT ON message_records TO PUBLIC")
        private.initialize()
        health = private.health()
        assert health["connected"] == 1 and health["schema"] == name
        assert isinstance(health["ssl"], bool)
        with private.transaction() as s:
            persist_conversations(s, [conversation("private@lid", "Private", None, ["secret"])], 3)
            cid = s.scalar("SELECT id FROM whatsapp_contacts")
            ContactRepository(s).set_classification(cid, "personal", "NO_ACTION", 0.9, "friendly")
            assert MessageRepository(s).count() == 1
            assert ContactRepository(s).get_conversation(cid).category == "personal"  # type: ignore[union-attr]
            rows = s.all(
                "SELECT relrowsecurity FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r'", (name,)
            )
            assert len(rows) == 5 and all(r["relrowsecurity"] for r in rows)
            assert (
                s.scalar(
                    """SELECT count(*) FROM pg_namespace n,
                   LATERAL aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a
                   WHERE n.nspname=%s AND a.grantee=0 AND a.privilege_type='USAGE'""",
                    (name,),
                )
                == 0
            )
        with database.transaction() as s:
            assert ContactRepository(s).count() == 0
            assert (
                s.scalar(
                    """SELECT count(*) FROM pg_class c,
                   LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
                   WHERE c.relnamespace=%s::regnamespace AND c.relkind IN ('r','v') AND a.grantee=0""",
                    (name,),
                )
                == 0
            )
    finally:
        with database.transaction() as s:
            s.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


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
        assert all(
            r["relrowsecurity"]
            for r in s.all(
                "SELECT relrowsecurity FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relkind='r'"
            )
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
