from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import connection
from psycopg2.extras import RealDictCursor, register_uuid

from utils.settings import PROJECT_ROOT, Settings

register_uuid()


class DatabaseUtil:
    """A short-lived connection per transaction, never shared between agent threads."""

    def __init__(self, db_config: Mapping[str, Any], *, schema: str | None = None) -> None:
        self._config = dict(db_config)
        self._schema = schema

    @classmethod
    def from_settings(cls, settings: Settings) -> "DatabaseUtil":
        return cls(settings.db_config, schema=settings.db_schema)

    @contextmanager
    def transaction(self) -> Iterator["SQLSession"]:
        conn = psycopg2.connect(**self._config, connect_timeout=10)
        try:
            with conn:
                session = SQLSession(conn)
                if self._schema:
                    session.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self._schema)))
                yield session
        finally:
            conn.close()

    def initialize(self, schema: Path = PROJECT_ROOT / "data" / "schema.sql") -> None:
        with self.transaction() as session:
            if self._schema:
                session.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self._schema)))
            session.execute(schema.read_text(encoding="utf-8"))
            namespace = (self._schema,) if self._schema else ()
            tables = ("whatsapp_contacts", "scrape_runs", "agent_runs", "evaluation_runs", "evaluations")
            legacy_tables = [
                row["relname"]
                for row in session.all(
                    """SELECT relname FROM pg_class
                       WHERE relnamespace=current_schema()::regnamespace
                         AND relname=ANY(%s) AND relkind IN ('r','p')""",
                    (["contacts", "conversations", "messages"],),
                )
            ]
            for table in (*tables, *legacy_tables):
                session.execute(
                    sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(sql.Identifier(*namespace, table))
                )
            roles = [sql.SQL("PUBLIC")] + [
                sql.Identifier(r["rolname"])
                for r in session.all(
                    "SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)",
                    (["anon", "authenticated", "service_role"],),
                )
            ]
            # Owner-backed views and preserved legacy snapshots must not expose chats to API roles.
            for name in (*tables, *legacy_tables, "contact_records", "conversation_records", "message_records"):
                session.execute(
                    sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                        sql.Identifier(*namespace, name), sql.SQL(",").join(roles)
                    )
                )

    def health(self) -> dict[str, Any]:
        with self.transaction() as session:
            result = session.one("SELECT current_database() AS database, 1 AS connected")
            if self._schema:
                result["schema"] = session.scalar("SELECT current_schema()")
                result["ssl"] = session.connection.info.ssl_in_use
            return result


class SQLSession:
    def __init__(self, conn: connection) -> None:
        self.connection = conn

    def execute(self, query: Any, params: Sequence[Any] | None = None) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.rowcount

    def all(self, query: Any, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def one(self, query: Any, params: Sequence[Any] | None = None) -> dict[str, Any]:
        rows = self.all(query, params)
        if len(rows) != 1:
            raise LookupError("Expected one database row")
        return rows[0]

    def scalar(self, query: Any, params: Sequence[Any] | None = None) -> Any:
        return next(iter(self.one(query, params).values()))
