"""SQLModel table definitions (MASTERSPEC §4 ``persist`` step, §5 ``Scan``).

Single responsibility: describe how a :class:`~app.models.Scan` is laid out in
SQLite. Nothing else lives here; reading and writing is
:mod:`app.db.repository`'s job.

The nested results are stored as JSON documents rather than normalised tables:
they are always read and written whole, they are already validated by the
pydantic models on the way in and out, and a schema per nested model would
have to change every time a detector adds a field.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

__all__ = ["ScanRow"]


class ScanRow(SQLModel, table=True):
    """One scan. Columns follow MASTERSPEC §5 ``Scan`` field for field."""

    __tablename__ = "scans"

    id: str = Field(primary_key=True, max_length=64)
    url: str
    host: str = Field(index=True)
    status: str = Field(index=True)
    #: Always UTC. SQLModel refuses naive datetimes on write.
    created_at: datetime = Field(index=True)
    before: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    after: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    patch: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    #: ``{code, message}`` when the scan failed. Approved addition to §5.
    error: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
