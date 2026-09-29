"""The fix plan: generated fixes plus what the patcher needs to apply them.

Single responsibility: carry fixes from ``POST /fixes`` to ``POST /patch``.

The API's :class:`~app.models.Fix` describes a fix for people (description,
diff, confidence). Applying it also needs machine parameters (which element,
which colour, which alt text). Those live here, beside the page source the
fixes were generated against, in the scan's private work directory, so the
public data model stays as MASTERSPEC §5 defines it and the patch is applied
to exactly the HTML the fixes were written for.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.models import AiUsage, Fix

__all__ = ["FixPlan", "PlannedFix", "fix_id", "load_plan", "save_plan"]

PLAN_FILE: Final[str] = "plan.json"
SOURCE_FILE: Final[str] = "source.html"


class PlannedFix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fix: Fix
    #: Child-index path of the target element in the saved source, if any.
    path: list[int] | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class FixPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_id: str
    #: The page URL after redirects: the base every relative URL resolves on.
    page_url: str
    source_sha256: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    prompt_version: str = ""
    fixes: list[PlannedFix] = Field(default_factory=list)
    ai_usage: AiUsage = Field(default_factory=AiUsage)

    def by_id(self) -> dict[str, PlannedFix]:
        return {planned.fix.id: planned for planned in self.fixes}


def fix_id(kind: str, target_key: str) -> str:
    """Stable id: the same finding gets the same id on every regeneration."""
    digest = hashlib.sha256(f"{kind}\x00{target_key}".encode()).hexdigest()[:10]
    return f"{kind}-{digest}"


def save_plan(work_dir: Path, plan: FixPlan, source_html: str) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / SOURCE_FILE).write_text(source_html, encoding="utf-8")
    (work_dir / PLAN_FILE).write_text(plan.model_dump_json(indent=2), encoding="utf-8")


def load_plan(work_dir: Path) -> tuple[FixPlan, str] | None:
    try:
        raw = json.loads((work_dir / PLAN_FILE).read_text(encoding="utf-8"))
        source = (work_dir / SOURCE_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    plan = FixPlan.model_validate(raw)
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != plan.source_sha256:
        raise ValueError("the saved page source does not match its fix plan")
    return plan, source
