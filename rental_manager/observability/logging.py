from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime


_SENSITIVE_PATTERN = re.compile(
    r"(?i)((?:pin|token|secret|password|authorization|cookie|api[_-]?key)\s*[=:]\s*)([^\s,;]+)"
)


def redact_log_text(value: object) -> str:
    text = str(value or "")
    return _SENSITIVE_PATTERN.sub(r"\1[redacted]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "at": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact_log_text(record.getMessage()),
        }
        for key in ("area", "event", "duration_ms", "status"):
            value = getattr(record, key, None)
            if value not in {None, ""}:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    root = logging.getLogger()
    if any(getattr(handler, "_rental_manager_handler", False) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler._rental_manager_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
