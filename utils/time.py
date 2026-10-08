from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def from_unix(ts: int | float | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, UTC)
