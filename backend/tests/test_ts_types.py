"""The TypeScript mirror of app.models must never drift (CLAUDE.md, Phase 2 task 8)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from app.models import Scan, ScanResult
from tests.api_support import make_settings, read_events, running_app

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "gen_ts_types.py"
TYPES_TS = REPO_ROOT / "frontend" / "src" / "lib" / "types.ts"


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_ts_types", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_types_match_the_models(generator: ModuleType) -> None:
    assert TYPES_TS.read_text(encoding="utf-8") == generator.render(), (
        "frontend/src/lib/types.ts is stale: run `make types`"
    )


def test_every_api_model_is_exported(generator: ModuleType) -> None:
    text = TYPES_TS.read_text(encoding="utf-8")
    for name in (
        "Scan",
        "ScanResult",
        "ScanRequest",
        "ScanCreated",
        "StepEvent",
        "DoneEvent",
        "ApiError",
        "ApiErrorEnvelope",
        "HistoryResponse",
        "DemoInfo",
        "ErrorCode",
        "ScanEventMap",
    ):
        assert f"export interface {name} " in text or f"export type {name} " in text, name


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string"}, "string"),
        ({"type": "integer"}, "number"),
        ({"anyOf": [{"$ref": "#/$defs/ApiError"}, {"type": "null"}]}, "ApiError | null"),
        ({"type": "array", "items": {"enum": ["a", "b"]}}, '("a" | "b")[]'),
        ({"const": True}, "true"),
        (
            {
                "type": "object",
                "additionalProperties": {"type": "integer"},
                "propertyNames": {"$ref": "#/$defs/Impact"},
            },
            "Partial<Record<Impact, number>>",
        ),
        ({"type": "object", "additionalProperties": {"type": "number"}}, "Record<string, number>"),
        ({}, "unknown"),
    ],
)
def test_schema_to_typescript(generator: ModuleType, schema: dict, expected: str) -> None:
    assert generator.ts_type(schema) == expected


async def test_api_json_carries_every_field_the_mirror_declares(tmp_path: Path) -> None:
    """The mirror makes every response field required; the API must honour that."""
    async with running_app(make_settings(tmp_path)) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": "http://demo.test/"})).json()[
            "scan_id"
        ]
        await read_events(client, scan_id)
        body = (await client.get(f"/api/scans/{scan_id}")).json()
    assert set(body) == set(Scan.model_fields)
    assert set(body["before"]) == set(ScanResult.model_fields)
