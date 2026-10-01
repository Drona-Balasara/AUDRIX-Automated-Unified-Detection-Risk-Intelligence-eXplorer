"""Declarative base for all ORM models.

Models in :mod:`app.models` inherit from :class:`Base`. Keeping the base in its
own module avoids circular imports between the session configuration and the
models that register themselves against ``Base.metadata``.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy 2.x declarative base shared by every model."""
