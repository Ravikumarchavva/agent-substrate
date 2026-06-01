"""Logging configuration — supports JSON (server) and human-friendly (CLI/notebook) modes.

Usage:
    # JSON mode (default — for servers)
    setup_logging()

    # Human-friendly mode (for notebooks, scripts, CLI chat)
    setup_logging(mode="pretty")

    # Quiet mode — only warnings and above
    setup_logging(mode="pretty", level=logging.WARNING)
"""

from __future__ import annotations

import inspect
import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

from ravi.config import settings

from pythonjsonlogger.msgspec import MsgspecFormatter

_LOGGER_NAMESPACE = "ravi"
_CONFIG_LOCK = threading.Lock()
_CONFIGURED = False


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class JsonFormatter(MsgspecFormatter):
    """JSON formatter for server / structured-log pipelines."""

    def add_fields(self, log_record, record, message_dict):  # type: ignore[override]
        super().add_fields(log_record, record, message_dict)
        if not log_record.get("timestamp"):
            from datetime import datetime, timezone

            log_record["timestamp"] = datetime.now(timezone.utc).isoformat()
        if log_record.get("level"):
            log_record["level"] = log_record["level"].upper()
        else:
            log_record["level"] = record.levelname


class TextFormatter(logging.Formatter):
    """Concise, coloured formatter for interactive use (CLI / notebooks).

    Only shows the message — no timestamp, no logger name — unless the level
    is WARNING or above, in which case a short tag is prepended.
    """

    LEVEL_TAGS = {
        logging.WARNING: "\033[33m⚠\033[0m ",  # yellow
        logging.ERROR: "\033[31m✖\033[0m ",  # red
        logging.CRITICAL: "\033[1;31m✖✖\033[0m ",  # bold red
    }

    def format(self, record: logging.LogRecord) -> str:
        prefix = self.LEVEL_TAGS.get(record.levelno, "")
        msg = f"{prefix}{record.getMessage()}"
        if record.exc_info:
            msg = msg + "\n" + self.formatException(record.exc_info)
        return msg


# ---------------------------------------------------------------------------
# Global mode flag — allows Console to flip _before_ first import of agent code
# ---------------------------------------------------------------------------


def _resolve_logger_name(name: str | None) -> str:
    """Resolve logger name for module-level usage.

    When ``name`` is omitted, infer the caller module name so modules can use:

        logger = setup_logging()
    """
    if name:
        return name
    frame = inspect.currentframe()
    if frame is None or frame.f_back is None or frame.f_back.f_back is None:
        return _LOGGER_NAMESPACE
    caller_globals = frame.f_back.f_back.f_globals
    caller_name = caller_globals.get("__name__")
    if isinstance(caller_name, str) and caller_name:
        return caller_name
    return _LOGGER_NAMESPACE


def _build_handler(
    *,
    sink: Literal["rotating_file", "file", "console"],
    mode: Literal["json", "pretty"],
    service_name: str,
    max_bytes: int,
    backup_count: int,
) -> logging.Handler:
    if sink in ("file", "rotating_file"):
        path = Path(settings.ROOT_DIR) / "logs" / f"{service_name}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if sink == "rotating_file":
            handler: logging.Handler = RotatingFileHandler(
                filename=path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
        else:
            handler = logging.FileHandler(filename=path, encoding="utf-8")
    else:
        handler = logging.StreamHandler()

    if mode == "pretty":
        handler.setFormatter(TextFormatter())
    else:
        handler.setFormatter(
            JsonFormatter("%(timestamp)s %(level)s %(name)s %(message)s")
        )
    setattr(handler, "_ravi_managed", True)
    return handler


def setup_logging(
    name: str | None = None,
    level: int = logging.INFO,
    *,
    mode: Literal["json", "pretty"] = "json",
    sink: Literal["rotating_file", "file", "console"] = "rotating_file",
    service_name: str = "agent-framework",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure ``ravi`` logger namespace and return a module logger.

    Safe to call from every module at import time::

        from ravi.logger import setup_logging
        logger = setup_logging()

    The first call configures the ``ravi`` namespace. Subsequent calls are
    constant-time and return a logger bound to the caller module.

    Parameters
    ----------
    name:
        Explicit logger name. If omitted, inferred from caller module.
    level:
        Minimum log level for the ``ravi`` namespace.
    mode:
        ``"json"``   — structured JSON (server / production).
        ``"pretty"`` — concise coloured lines (CLI / notebook).
    sink:
        ``"rotating_file"`` (default), ``"file"``, or ``"console"``.
    service_name:
        Log filename stem under ``<ROOT_DIR>/logs/``.
    max_bytes:
        Max file size before rotation (rotating sink only).
    backup_count:
        Number of rotated files to retain (rotating sink only).
    """
    global _CONFIGURED

    with _CONFIG_LOCK:
        namespace_logger = logging.getLogger(_LOGGER_NAMESPACE)
        if not _CONFIGURED:
            # Replace only handlers we own; keep foreign handlers untouched.
            namespace_logger.handlers = [
                h
                for h in namespace_logger.handlers
                if not getattr(h, "_ravi_managed", False)
            ]
            namespace_logger.addHandler(
                _build_handler(
                    sink=sink,
                    mode=mode,
                    service_name=service_name,
                    max_bytes=max_bytes,
                    backup_count=backup_count,
                )
            )
            namespace_logger.setLevel(level)
            namespace_logger.propagate = False

            if mode == "pretty":
                for noisy in ("httpx", "httpcore", "openai", "urllib3", "asyncio"):
                    logging.getLogger(noisy).setLevel(logging.WARNING)

            _CONFIGURED = True
        else:
            namespace_logger.setLevel(level)

    return logging.getLogger(_resolve_logger_name(name))
