import uuid
from pathlib import Path

import psycopg2
import pytest

from Models.schema import ContactIn
from utils.database import DatabaseUtil
from utils.repositories import ContactRepository
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
            s.execute("INSERT INTO conversations (id,contact_id) VALUES (%s,%s)", (uuid.uuid4(), uuid.uuid4()))
    with database.transaction() as s:
        assert ContactRepository(s).count() == 0


def test_initializer_preserves_old_tables_ids_and_alembic_marker(database: DatabaseUtil) -> None:
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
    attack = "'; DROP TABLE contacts; --"
    with database.transaction() as s:
        repo = ContactRepository(s)
        repo.upsert(ContactIn(whatsapp_id="safe@lid", contact_name=attack))
        assert repo.search(name=attack)[0] == 1
        assert repo.search(name="' OR 1=1 --")[0] == 0
        assert repo.count() == 1
