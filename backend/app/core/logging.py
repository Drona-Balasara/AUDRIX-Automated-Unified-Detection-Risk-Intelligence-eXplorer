"""Centralized logging configuration.

A single ``configure_logging`` call sets up consistent, development-friendly
formatting for the application and the Uvicorn servers. Logging uses the
standard library only. No credentials, tokens, authorization headers, or
request bodies are logged anywhere in the application; keep it that way when
adding new log statements.
"""

from __future__ import annotations

import logging
from logging.config import dictConfig

_CONFIGURED = False

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str = "INFO") -> None:
    """Configure root and Uvicorn loggers once per process.

    Safe to call multiple times; subsequent calls are no-ops so repeated
    application startups (e.g. in tests) do not stack duplicate handlers.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {"format": LOG_FORMAT, "datefmt": DATE_FORMAT},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {"handlers": ["console"], "level": level.upper()},
            "loggers": {
                # Align Uvicorn's loggers with ours and prevent duplicate output.
                "uvicorn": {"handlers": ["console"], "level": level.upper(), "propagate": False},
                "uvicorn.error": {"handlers": ["console"], "level": level.upper(), "propagate": False},
                "uvicorn.access": {"handlers": ["console"], "level": level.upper(), "propagate": False},
            },
        }
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger for a module (use ``__name__`` at call sites)."""
    return logging.getLogger(name)
