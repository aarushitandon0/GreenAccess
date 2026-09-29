"""Scan persistence (MASTERSPEC §4 ``persist``, §17 ``DATABASE_URL``).

Single responsibility: move :class:`~app.models.Scan` objects in and out of the
database. Callers only ever see pydantic models, never rows.

SQLModel's session API is synchronous, so every public method runs its query in
a worker thread (CLAUDE.md: "async for I/O"); the event loop keeps serving SSE
streams while SQLite does its work. Migrations are ``create_all``: the schema is
one table and has no history to migrate yet.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.db.tables import ScanRow
from app.models import ApiError, PatchInfo, Scan, ScanResult, ScanStatus

logger = logging.getLogger(__name__)

__all__ = ["ScanRepository"]

_T = TypeVar("_T")

#: Statuses a scan can be left in if the process stops mid-scan.
_UNFINISHED: tuple[str, ...] = (ScanStatus.QUEUED.value, ScanStatus.RUNNING.value)


def _utc(value: datetime) -> datetime:
    """Normalise to aware UTC; SQLite can hand back a naive value."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _to_scan(row: ScanRow) -> Scan:
    return Scan.model_validate(
        {
            "id": row.id,
            "url": row.url,
            "host": row.host,
            "status": row.status,
            "created_at": _utc(row.created_at),
            "before": row.before,
            "after": row.after,
            "patch": row.patch,
            "error": row.error,
        }
    )


class ScanRepository:
    """All database access for scans."""

    def __init__(self, database_url: str) -> None:
        url = make_url(database_url)
        connect_args: dict[str, object] = {}
        if url.get_backend_name() == "sqlite":
            # Queries run in worker threads, so the connection crosses threads.
            connect_args["check_same_thread"] = False
            if url.database and url.database != ":memory:":
                Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(database_url, connect_args=connect_args)

    # -- lifecycle -------------------------------------------------------- #

    def create_all(self) -> None:
        """Create missing tables. Idempotent."""
        SQLModel.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()

    async def _run(self, work: Callable[[Session], _T]) -> _T:
        def job() -> _T:
            with Session(self.engine, expire_on_commit=False) as session:
                return work(session)

        return await asyncio.to_thread(job)

    # -- writes ----------------------------------------------------------- #

    async def insert(self, scan: Scan) -> None:
        row = ScanRow(
            id=scan.id,
            url=scan.url,
            host=scan.host,
            status=scan.status.value,
            created_at=scan.created_at.astimezone(UTC),
        )

        def work(session: Session) -> None:
            session.add(row)
            session.commit()

        await self._run(work)

    async def _update(self, scan_id: str, **fields: object) -> bool:
        def work(session: Session) -> bool:
            row = session.get(ScanRow, scan_id)
            if row is None:
                return False
            for name, value in fields.items():
                setattr(row, name, value)
            session.add(row)
            session.commit()
            return True

        return await self._run(work)

    async def mark_running(self, scan_id: str) -> bool:
        return await self._update(scan_id, status=ScanStatus.RUNNING.value)

    async def save_before(self, scan_id: str, result: ScanResult) -> bool:
        """Store a finished scan's result and mark it done, in one commit."""
        return await self._update(
            scan_id,
            status=ScanStatus.DONE.value,
            before=result.model_dump(mode="json"),
            error=None,
        )

    async def save_patch(self, scan_id: str, patch: PatchInfo) -> bool:
        """Store the generated fixes / built patch (MASTERSPEC §5 ``PatchInfo``)."""
        return await self._update(scan_id, patch=patch.model_dump(mode="json"))

    async def save_after(self, scan_id: str, result: ScanResult, patch: PatchInfo) -> bool:
        """Store the re-scan of the patched copy and its patch, in one commit."""
        return await self._update(
            scan_id,
            after=result.model_dump(mode="json"),
            patch=patch.model_dump(mode="json"),
        )

    async def mark_error(self, scan_id: str, error: ApiError) -> bool:
        return await self._update(
            scan_id, status=ScanStatus.ERROR.value, error=error.model_dump(mode="json")
        )

    async def fail_unfinished(self, error: ApiError) -> int:
        """Mark scans left queued or running (by a restart) as failed.

        Their tasks died with the old process, so without this they would sit
        in ``running`` forever and an SSE subscriber would wait on nothing.
        """
        payload = error.model_dump(mode="json")

        def work(session: Session) -> int:
            rows = session.exec(select(ScanRow).where(col(ScanRow.status).in_(_UNFINISHED))).all()
            for row in rows:
                row.status = ScanStatus.ERROR.value
                row.error = payload
                session.add(row)
            session.commit()
            return len(rows)

        return await self._run(work)

    # -- reads ------------------------------------------------------------ #

    async def get(self, scan_id: str) -> Scan | None:
        def work(session: Session) -> Scan | None:
            row = session.get(ScanRow, scan_id)
            return _to_scan(row) if row is not None else None

        return await self._run(work)

    async def list_recent(self, *, host: str | None = None, limit: int = 50) -> list[Scan]:
        """Newest first, optionally for one host."""

        def work(session: Session) -> list[Scan]:
            query = select(ScanRow)
            if host:
                query = query.where(ScanRow.host == host)
            query = query.order_by(col(ScanRow.created_at).desc(), col(ScanRow.id)).limit(limit)
            return [_to_scan(row) for row in session.exec(query).all()]

        return await self._run(work)
