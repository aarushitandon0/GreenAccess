"""Anthropic API wrapper for structured fix generation (MASTERSPEC §8.2).

Single responsibility: turn one :class:`LlmRequest` into one validated pydantic
answer, as cheaply and honestly as possible, and account for what it cost.

For every request, in order:

1. **Cache.** The key is a hash of model + prompts + image hashes + schema +
   prompt version (:mod:`app.llm.cache`). A hit is validated again, then served
   and counted as cached, never as live.
2. **Offline.** With ``LLM_OFFLINE=1`` nothing leaves the machine: a miss
   raises :class:`LlmUnavailable` (API code ``LLM_UNAVAILABLE``). So does a
   missing ``ANTHROPIC_API_KEY``.
3. **Live call.** ``messages.create`` with the answer model's JSON schema as
   the structured-output format (``output_config.format``), a per-request
   timeout and one SDK-level retry for network and 5xx failures.
4. **Validate.** The reply is parsed and validated with pydantic. If it is not
   valid JSON or fails validation, the request is retried **once** with the
   error shown to the model; a second failure raises :class:`LlmFailed`.
5. **Store.** Only validated answers are written to the cache.

Every request sent (retries included) is counted with its token usage in a
:class:`UsageMeter`, which becomes the ``AiUsage`` the fixes response reports.
Prompts and answers are never logged: they contain page content.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.llm.cache import LlmCache, cache_key
from app.llm.schemas import strict_json_schema
from app.models import AiUsage

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "LlmClient",
    "LlmFailed",
    "LlmRequest",
    "LlmResult",
    "LlmUnavailable",
    "PreparedImage",
    "UsageMeter",
]

#: Per-request wall clock, before the SDK's single retry.
DEFAULT_TIMEOUT_S: Final[float] = 60.0
#: Network/5xx retries the SDK performs itself. Invalid JSON is retried here.
SDK_MAX_RETRIES: Final[int] = 1
#: Live requests in flight at once.
MAX_CONCURRENT_CALLS: Final[int] = 3
#: Room for adaptive thinking plus a small JSON answer.
DEFAULT_MAX_TOKENS: Final[int] = 4096

T = TypeVar("T", bound=BaseModel)


class LlmUnavailable(Exception):
    """No answer can come from the LLM at all (MASTERSPEC §12 ``LLM_UNAVAILABLE``)."""

    code: Final[str] = "LLM_UNAVAILABLE"


class LlmFailed(Exception):
    """This one request failed; others in the same run may still succeed."""


@dataclass(frozen=True)
class PreparedImage:
    """An image ready to send: already downscaled (see :mod:`app.llm.vision`)."""

    media_type: str
    data: bytes
    sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class LlmRequest:
    task: str
    system: str
    prompt: str
    prompt_version: str
    images: tuple[PreparedImage, ...] = ()
    max_tokens: int = DEFAULT_MAX_TOKENS


@dataclass(frozen=True)
class LlmResult[T: BaseModel]:
    value: T
    cached: bool


class _Messages(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class Sdk(Protocol):
    """The slice of ``anthropic.AsyncAnthropic`` used here; tests supply a fake."""

    @property
    def messages(self) -> _Messages: ...


@dataclass
class UsageMeter:
    """Counts calls and tokens for one fix-generation run."""

    model: str
    offline: bool
    live_calls: int = 0
    cached_calls: int = 0
    failed_calls: int = 0
    vision_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cached_output_tokens: int = 0
    unavailable_reason: str | None = None

    def usage(self) -> AiUsage:
        return AiUsage(
            model=self.model,
            live_calls=self.live_calls,
            cached_calls=self.cached_calls,
            failed_calls=self.failed_calls,
            vision_calls=self.vision_calls,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_input_tokens,
            cached_output_tokens=self.cached_output_tokens,
            offline=self.offline,
            unavailable_reason=self.unavailable_reason,
        )


@dataclass
class LlmClient:
    """Structured, cached, accounted calls to the model named by ``ANTHROPIC_MODEL``."""

    model: str
    cache: LlmCache
    api_key: str | None = None
    offline: bool = True
    timeout_s: float = DEFAULT_TIMEOUT_S
    sdk: Sdk | None = None
    meter: UsageMeter = field(init=False)
    #: Set after an error no retry can fix (bad key, unknown model); from then
    #: on only the cache is consulted.
    disabled_reason: str | None = field(default=None, init=False)
    _gate: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.meter = UsageMeter(model=self.model, offline=self.offline)
        self._gate = asyncio.Semaphore(MAX_CONCURRENT_CALLS)

    @classmethod
    def from_settings(cls, settings: Settings, *, sdk: Sdk | None = None) -> LlmClient:
        return cls(
            model=settings.anthropic_model,
            cache=LlmCache(settings.llm_cache_dir, (settings.llm_fixture_cache_dir,)),
            api_key=settings.anthropic_api_key,
            offline=settings.llm_offline,
            sdk=sdk,
        )

    # -- availability ----------------------------------------------------- #

    def live_unavailable_reason(self) -> str | None:
        """Why a cache miss cannot be answered live, or None if it can."""
        if self.offline:
            return "LLM_OFFLINE is set, so only cached answers can be used"
        if not self.api_key and self.sdk is None:
            return "ANTHROPIC_API_KEY is not set"
        return self.disabled_reason

    def _sdk(self) -> Sdk:
        if self.sdk is None:
            self.sdk = anthropic.AsyncAnthropic(
                api_key=self.api_key, timeout=self.timeout_s, max_retries=SDK_MAX_RETRIES
            )
        return self.sdk

    # -- the one public call ---------------------------------------------- #

    async def structured(self, request: LlmRequest, schema: type[T]) -> LlmResult[T]:
        """A validated `schema` answer to `request`, from cache or the API."""
        key = cache_key(
            model=self.model,
            system=request.system,
            prompt=request.prompt,
            image_hashes=[image.sha256 for image in request.images],
            schema_name=schema.__name__,
            prompt_version=request.prompt_version,
        )

        entry = self.cache.get(key)
        if entry is not None:
            try:
                value = schema.model_validate(entry.response)
            except ValidationError:
                logger.warning("cache entry %s no longer validates; ignoring it", key[:12])
            else:
                self.meter.cached_calls += 1
                self.meter.vision_calls += bool(request.images)
                self.meter.cached_input_tokens += entry.input_tokens
                self.meter.cached_output_tokens += entry.output_tokens
                return LlmResult(value=value, cached=True)

        reason = self.live_unavailable_reason()
        if reason is not None:
            self.meter.unavailable_reason = reason
            raise LlmUnavailable(reason)

        async with self._gate:
            value, input_tokens, output_tokens = await self._call_with_retry(request, schema)
        self.meter.vision_calls += bool(request.images)
        self.cache.put(
            key,
            task=request.task,
            model=self.model,
            response=value.model_dump(mode="json"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return LlmResult(value=value, cached=False)

    # -- live path -------------------------------------------------------- #

    @staticmethod
    def _user_content(request: LlmRequest) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.media_type,
                    "data": base64.standard_b64encode(image.data).decode("ascii"),
                },
            }
            for image in request.images
        ]
        content.append({"type": "text", "text": request.prompt})
        return content

    async def _send(
        self, request: LlmRequest, schema: type[T], messages: list[dict[str, Any]]
    ) -> Any:
        self.meter.live_calls += 1
        try:
            response = await self._sdk().messages.create(
                model=self.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=messages,
                output_config={
                    "format": {"type": "json_schema", "schema": strict_json_schema(schema)}
                },
                timeout=self.timeout_s,
            )
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.NotFoundError,
        ) as exc:
            # No retry fixes a bad key or an unknown model: stop calling.
            self.meter.failed_calls += 1
            self.disabled_reason = f"the API rejected the request ({type(exc).__name__})"
            self.meter.unavailable_reason = self.disabled_reason
            raise LlmUnavailable(self.disabled_reason) from exc
        except anthropic.APITimeoutError as exc:
            self.meter.failed_calls += 1
            raise LlmFailed(f"timed out after {self.timeout_s:.0f} s") from exc
        except anthropic.APIStatusError as exc:
            self.meter.failed_calls += 1
            raise LlmFailed(f"API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            self.meter.failed_calls += 1
            raise LlmFailed("could not reach the API") from exc

        usage = getattr(response, "usage", None)
        self.meter.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        self.meter.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        return response

    @staticmethod
    def _text_of(response: Any) -> str:
        return "".join(
            getattr(block, "text", "")
            for block in getattr(response, "content", None) or []
            if getattr(block, "type", "") == "text"
        )

    async def _call_with_retry(self, request: LlmRequest, schema: type[T]) -> tuple[T, int, int]:
        messages: list[dict[str, Any]] = [{"role": "user", "content": self._user_content(request)}]
        input_tokens = output_tokens = 0
        problem = ""
        for attempt in (1, 2):
            response = await self._send(request, schema, messages)
            usage = getattr(response, "usage", None)
            input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens += int(getattr(usage, "output_tokens", 0) or 0)

            stop = getattr(response, "stop_reason", None)
            if stop == "refusal":
                self.meter.failed_calls += 1
                raise LlmFailed("the model declined this request")
            text = self._text_of(response)
            try:
                value = schema.model_validate(json.loads(text))
            except json.JSONDecodeError as exc:
                problem = f"not valid JSON ({exc.msg})"
            except ValidationError as exc:
                problem = f"did not match the schema: {exc.errors(include_url=False)[:3]}"
            else:
                return value, input_tokens, output_tokens

            if stop == "max_tokens":
                problem = "cut off by max_tokens before the JSON was complete"
            logger.info("%s answer invalid on attempt %d: %s", request.task, attempt, problem)
            if attempt == 1:
                messages = [
                    *messages,
                    {"role": "assistant", "content": text or "(empty reply)"},
                    {
                        "role": "user",
                        "content": (
                            f"That reply was {problem[:500]}. Reply again with only the "
                            "JSON object the schema describes, nothing else."
                        ),
                    },
                ]
        self.meter.failed_calls += 1
        raise LlmFailed(f"invalid answer after one retry: {problem[:200]}")
