from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extensions import connection
from psycopg2.extras import RealDictCursor, register_uuid

from utils.settings import PROJECT_ROOT, Settings

register_uuid()


class DatabaseUtil:
    """A short-lived connection per transaction, never shared between agent threads."""

    def __init__(self, db_config: Mapping[str, Any]) -> None:
        self._config = dict(db_config)

    @classmethod
    def from_settings(cls, settings: Settings) -> "DatabaseUtil":
        return cls(settings.db_config)

    @contextmanager
    def transaction(self) -> Iterator["SQLSession"]:
        conn = psycopg2.connect(**self._config, connect_timeout=10)
        try:
            with conn:
                yield SQLSession(conn)
        finally:
            conn.close()

    def initialize(self, schema: Path = PROJECT_ROOT / "data" / "schema.sql") -> None:
        with self.transaction() as session:
            session.execute(schema.read_text(encoding="utf-8"))

    def health(self) -> dict[str, Any]:
        with self.transaction() as session:
            return session.one("SELECT current_database() AS database, 1 AS connected")


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
