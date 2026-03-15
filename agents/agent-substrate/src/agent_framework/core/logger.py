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

import logging
import os
import sys
import io
from typing import Literal

from pythonjsonlogger import jsonlogger


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter for server / structured-log pipelines."""

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        if not log_record.get("timestamp"):
            from datetime import datetime
            log_record["timestamp"] = datetime.utcnow().isoformat()
        if log_record.get("level"):
            log_record["level"] = log_record["level"].upper()
        else:
            log_record["level"] = record.levelname


class PrettyFormatter(logging.Formatter):
    """Concise, coloured formatter for interactive use (CLI / notebooks).

    Only shows the message — no timestamp, no logger name — unless the level
    is WARNING or above, in which case a short tag is prepended.
    """

    LEVEL_TAGS = {
        logging.WARNING:  "\033[33m⚠\033[0m ",   # yellow
        logging.ERROR:    "\033[31m✖\033[0m ",     # red
        logging.CRITICAL: "\033[1;31m✖✖\033[0m ",  # bold red
    }

    def format(self, record: logging.LogRecord) -> str:
        prefix = self.LEVEL_TAGS.get(record.levelno, "")
        return f"{prefix}{record.getMessage()}"


# ---------------------------------------------------------------------------
# Global mode flag — allows Console to flip _before_ first import of agent code
# ---------------------------------------------------------------------------
_current_mode: Literal["json", "pretty"] = "json"


def _is_interactive() -> bool:
    """Heuristic: running inside Jupyter or an interactive terminal."""
    try:
        # IPython / Jupyter
        shell = get_ipython().__class__.__name__  # type: ignore[name-defined]
        return shell in ("ZMQInteractiveShell", "TerminalInteractiveShell")
    except NameError:
        pass
    return hasattr(sys, "ps1") or sys.stdout.isatty()


def setup_logging(
    level: int = logging.INFO,
    *,
    mode: Literal["json", "pretty", "auto"] = "auto",
    service_name: str = "agent-framework",
) -> None:
    """Configure the root logger.

    Parameters
    ----------
    level:
        Minimum log level.
    mode:
        ``"json"``   — structured JSON (server / production).
        ``"pretty"`` — concise coloured lines (CLI / notebook).
        ``"auto"``   — pick based on environment (Jupyter or tty → pretty).
    """
    global _current_mode

    if mode == "auto":
        mode = "pretty" if _is_interactive() else "json"
    _current_mode = mode

    root = logging.getLogger()
    # Remove existing handlers to avoid duplicates
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    # Build stream (handle Jupyter OutStream lacking .buffer)
    if hasattr(sys.stdout, "buffer"):
        stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    else:
        stream = sys.stdout

    handler = logging.StreamHandler(stream)

    if mode == "pretty":
        handler.setFormatter(PrettyFormatter())
        # In pretty mode, silence noisy third-party loggers entirely
        for noisy in ("httpx", "httpcore", "openai", "urllib3", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    else:
        handler.setFormatter(
            CustomJsonFormatter(
                "%(timestamp)s %(level)s %(name)s %(message)s",
                json_ensure_ascii=False,
            )
        )

    root.addHandler(handler)
    root.setLevel(level)


# Module-level convenience logger
logger = logging.getLogger("agent_framework")
