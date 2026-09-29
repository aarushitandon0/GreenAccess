"""Disk cache of LLM answers (MASTERSPEC §8.2, §16).

Single responsibility: store and find validated answers by a key derived from
everything that determines them: the model, the prompts, the hashes of any
images, the answer schema and the prompt version. Change any of those and the
key changes, so a stale answer is never served for a different question.

Two locations are read, in order:

1. the writable cache (``backend/.llm_cache/``, gitignored), and
2. the committed demo cache (``backend/app/fixtures/llm_cache/``), which is
   read-only and exists so ``LLM_OFFLINE=1`` can replay the demo run.

Each entry records when and with which model it was produced, and is labelled
``"cached": true``; callers report cached answers separately from live ones.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["CacheEntry", "LlmCache", "cache_key"]


def cache_key(
    *,
    model: str,
    system: str,
    prompt: str,
    image_hashes: list[str],
    schema_name: str,
    prompt_version: str,
) -> str:
    """sha256 over the request's identity (MASTERSPEC §8.2: prompt + image + model)."""
    identity = json.dumps(
        {
            "model": model,
            "system": system,
            "prompt": prompt,
            "images": image_hashes,
            "schema": schema_name,
            "prompt_version": prompt_version,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheEntry:
    key: str
    task: str
    model: str
    created_at: str
    response: dict[str, Any]
    input_tokens: int
    output_tokens: int


class LlmCache:
    """Answers on disk: one JSON file per key, sharded by the key's prefix."""

    def __init__(self, write_dir: Path, read_only_dirs: tuple[Path, ...] = ()) -> None:
        self.write_dir = write_dir
        self.read_only_dirs = read_only_dirs

    @staticmethod
    def _path(root: Path, key: str) -> Path:
        return root / key[:2] / f"{key}.json"

    def get(self, key: str) -> CacheEntry | None:
        for root in (self.write_dir, *self.read_only_dirs):
            path = self._path(root, key)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("ignoring unreadable cache entry %s: %s", path, exc)
                continue
            try:
                return CacheEntry(
                    key=str(raw["key"]),
                    task=str(raw.get("task", "")),
                    model=str(raw["model"]),
                    created_at=str(raw.get("created_at", "")),
                    response=dict(raw["response"]),
                    input_tokens=int(raw.get("usage", {}).get("input_tokens", 0)),
                    output_tokens=int(raw.get("usage", {}).get("output_tokens", 0)),
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("ignoring malformed cache entry %s: %s", path, exc)
        return None

    def put(
        self,
        key: str,
        *,
        task: str,
        model: str,
        response: dict[str, Any],
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Write atomically, so a crash never leaves half an answer behind."""
        path = self._path(self.write_dir, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": key,
            "task": task,
            "model": model,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "cached": True,
            "label": "Cached LLM answer. AI-generated, review before use.",
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            "response": response,
        }
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
