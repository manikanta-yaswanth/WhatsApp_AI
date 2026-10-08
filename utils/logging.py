import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

# Keys that may carry personal message content; dropped from every log event.
_SENSITIVE_KEYS = {"message_text", "text", "body", "content", "answer", "query", "messages"}


def _redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in _SENSITIVE_KEYS & event_dict.keys():
        event_dict[key] = "[redacted]"
    return event_dict


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level)
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        logger_factory=lambda *args: structlog.PrintLogger(file=sys.stderr),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
