"""LLM client: structured output, retry, cache, offline mode, accounting.

The Anthropic SDK is replaced by a scripted fake, so these tests make no
network calls and need no key. Every reply below is test data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.config import Settings
from app.llm.cache import LlmCache
from app.llm.client import (
    LlmClient,
    LlmFailed,
    LlmRequest,
    LlmUnavailable,
    PreparedImage,
)
from app.llm.schemas import AltTextAnswer, LangAnswer, NameBatchAnswer, strict_json_schema

GOOD_LANG = '{"lang": "en", "confidence": 0.9}'


def reply(text: str, *, stop: str = "end_turn", tokens: tuple[int, int] = (100, 20)) -> Any:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


@dataclass
class FakeMessages:
    script: list[Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@dataclass
class FakeSdk:
    messages: FakeMessages


def make_client(tmp_path: Path, script: list[Any], **overrides: Any) -> tuple[LlmClient, FakeSdk]:
    sdk = FakeSdk(FakeMessages(list(script)))
    values: dict[str, Any] = {
        "model": "test-model",
        "cache": LlmCache(tmp_path / "cache", (tmp_path / "fixtures",)),
        "api_key": "sk-test",
        "offline": False,
        "sdk": sdk,
    }
    values.update(overrides)
    return LlmClient(**values), sdk


def lang_request(prompt: str = "<page_text>Hello world</page_text>", **kw: Any) -> LlmRequest:
    return LlmRequest(task="html_lang", system="sys", prompt=prompt, prompt_version="v1", **kw)


async def test_live_call_is_validated_cached_and_counted(tmp_path: Path) -> None:
    client, sdk = make_client(tmp_path, [reply(GOOD_LANG, tokens=(120, 30))])
    result = await client.structured(lang_request(), LangAnswer)

    assert result.value.lang == "en" and result.cached is False
    usage = client.meter.usage()
    assert (usage.live_calls, usage.cached_calls, usage.failed_calls) == (1, 0, 0)
    assert (usage.input_tokens, usage.output_tokens) == (120, 30)
    call = sdk.messages.calls[0]
    assert call["model"] == "test-model"
    assert call["timeout"] == client.timeout_s
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["format"]["schema"] == strict_json_schema(LangAnswer)
    assert "tool_choice" not in call  # forced tool use is rejected by current models

    files = list((tmp_path / "cache").rglob("*.json"))
    assert len(files) == 1
    stored = json.loads(files[0].read_text())
    assert stored["cached"] is True and "review before use" in stored["label"]
    assert stored["model"] == "test-model"
    assert stored["usage"] == {"input_tokens": 120, "output_tokens": 30}


async def test_second_identical_request_is_served_from_cache(tmp_path: Path) -> None:
    client, sdk = make_client(tmp_path, [reply(GOOD_LANG, tokens=(120, 30))])
    await client.structured(lang_request(), LangAnswer)
    again = await client.structured(lang_request(), LangAnswer)

    assert again.cached is True
    assert len(sdk.messages.calls) == 1
    usage = client.meter.usage()
    assert (usage.live_calls, usage.cached_calls) == (1, 1)
    assert (usage.cached_input_tokens, usage.cached_output_tokens) == (120, 30)
    # Billed tokens are only the live call's.
    assert usage.input_tokens == 120


async def test_invalid_json_is_retried_once_with_the_error(tmp_path: Path) -> None:
    client, sdk = make_client(tmp_path, [reply("not json"), reply(GOOD_LANG)])
    result = await client.structured(lang_request(), LangAnswer)

    assert result.value.lang == "en"
    assert len(sdk.messages.calls) == 2
    retry = sdk.messages.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert retry[1]["content"] == "not json"
    assert "not valid JSON" in retry[2]["content"]
    assert client.meter.usage().live_calls == 2


async def test_schema_violation_is_retried_then_fails(tmp_path: Path) -> None:
    bad = '{"lang": "english please", "confidence": 0.9}'
    client, _ = make_client(tmp_path, [reply(bad), reply(bad)])
    with pytest.raises(LlmFailed, match="after one retry"):
        await client.structured(lang_request(), LangAnswer)
    usage = client.meter.usage()
    assert (usage.live_calls, usage.failed_calls) == (2, 1)
    assert not list((tmp_path / "cache").rglob("*.json"))  # never cache a bad answer


async def test_truncated_reply_is_explained_on_retry(tmp_path: Path) -> None:
    client, sdk = make_client(tmp_path, [reply('{"lang": "e', stop="max_tokens"), reply(GOOD_LANG)])
    await client.structured(lang_request(), LangAnswer)
    assert "max_tokens" in sdk.messages.calls[1]["messages"][2]["content"]


async def test_offline_miss_raises_llm_unavailable(tmp_path: Path) -> None:
    client, sdk = make_client(tmp_path, [], offline=True)
    with pytest.raises(LlmUnavailable, match="LLM_OFFLINE") as caught:
        await client.structured(lang_request(), LangAnswer)
    assert caught.value.code == "LLM_UNAVAILABLE"
    assert sdk.messages.calls == []
    assert client.meter.usage().unavailable_reason is not None


async def test_offline_hit_from_committed_fixtures(tmp_path: Path) -> None:
    online, _ = make_client(tmp_path, [reply(GOOD_LANG)])
    await online.structured(lang_request(), LangAnswer)
    # Move the entry into the read-only fixture location, as the demo does.
    (tmp_path / "cache").rename(tmp_path / "fixtures")

    offline, sdk = make_client(tmp_path, [], offline=True)
    result = await offline.structured(lang_request(), LangAnswer)
    assert result.cached is True and result.value.lang == "en"
    assert sdk.messages.calls == []
    usage = offline.meter.usage()
    assert usage.offline is True and usage.live_calls == 0 and usage.cached_calls == 1


async def test_missing_key_raises_llm_unavailable(tmp_path: Path) -> None:
    client = LlmClient(model="m", cache=LlmCache(tmp_path), api_key=None, offline=False)
    with pytest.raises(LlmUnavailable, match="ANTHROPIC_API_KEY"):
        await client.structured(lang_request(), LangAnswer)


async def test_cache_key_covers_images_and_model(tmp_path: Path) -> None:
    image_a = PreparedImage("image/webp", b"a", "a" * 64, 1, 1)
    image_b = PreparedImage("image/webp", b"b", "b" * 64, 1, 1)
    client, sdk = make_client(tmp_path, [reply(GOOD_LANG), reply(GOOD_LANG), reply(GOOD_LANG)])
    await client.structured(lang_request(images=(image_a,)), LangAnswer)
    await client.structured(lang_request(images=(image_b,)), LangAnswer)
    assert len(sdk.messages.calls) == 2
    assert client.meter.usage().vision_calls == 2
    other_model, other_sdk = make_client(tmp_path, [reply(GOOD_LANG)], model="another-model")
    await other_model.structured(lang_request(images=(image_a,)), LangAnswer)
    assert len(other_sdk.messages.calls) == 1


async def test_image_is_sent_base64_before_the_text(tmp_path: Path) -> None:
    image = PreparedImage("image/webp", b"\x00\x01", "c" * 64, 2, 2)
    client, sdk = make_client(tmp_path, [reply(GOOD_LANG)])
    await client.structured(lang_request(images=(image,)), LangAnswer)
    content = sdk.messages.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"] == {"type": "base64", "media_type": "image/webp", "data": "AAE="}
    assert content[1]["type"] == "text"


def _request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


async def test_auth_error_stops_further_live_calls(tmp_path: Path) -> None:
    error = anthropic.AuthenticationError(
        "bad key", response=httpx2.Response(401, request=_request()), body=None
    )
    client, sdk = make_client(tmp_path, [error])
    with pytest.raises(LlmUnavailable):
        await client.structured(lang_request(), LangAnswer)
    with pytest.raises(LlmUnavailable, match="rejected"):
        await client.structured(lang_request("<page_text>other</page_text>"), LangAnswer)
    assert len(sdk.messages.calls) == 1


async def test_timeout_fails_only_that_request(tmp_path: Path) -> None:
    client, _ = make_client(
        tmp_path, [anthropic.APITimeoutError(request=_request()), reply(GOOD_LANG)]
    )
    with pytest.raises(LlmFailed, match="timed out"):
        await client.structured(lang_request(), LangAnswer)
    result = await client.structured(lang_request(), LangAnswer)
    assert result.value.lang == "en"
    assert client.meter.usage().failed_calls == 1


async def test_refusal_fails_the_request(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, [reply("", stop="refusal")])
    with pytest.raises(LlmFailed, match="declined"):
        await client.structured(lang_request(), LangAnswer)


def test_from_settings_reads_model_offline_and_cache_dirs(tmp_path: Path) -> None:
    settings = Settings(
        anthropic_model="claude-from-env",
        llm_offline=True,
        llm_cache_dir=tmp_path / "c",
        llm_fixture_cache_dir=tmp_path / "f",
    )
    client = LlmClient.from_settings(settings)
    assert client.model == "claude-from-env"
    assert client.offline is True
    assert client.cache.write_dir == tmp_path / "c"
    assert client.cache.read_only_dirs == (tmp_path / "f",)


# --------------------------------------------------------------------------- #
# Answer schemas
# --------------------------------------------------------------------------- #

ALT = {
    "decorative": False,
    "alt": "Harbour at dusk",
    "visible_text": [],
    "background_color": "#102030",
    "text_color": "#FFFFFF",
    "confidence": 0.8,
    "reason": "Photo illustrates the story.",
}


def test_alt_answer_normalises_and_accepts() -> None:
    answer = AltTextAnswer.model_validate(ALT)
    assert answer.text_color == "#ffffff"


@pytest.mark.parametrize(
    "patch",
    [
        {"decorative": True},  # decorative must have alt ""
        {"alt": ""},  # informative needs text
        {"background_color": "red"},
        {"confidence": 1.5},
        {"extra": 1},
    ],
)
def test_alt_answer_rejects(patch: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        AltTextAnswer.model_validate({**ALT, **patch})


def test_decorative_answer_is_empty_alt() -> None:
    answer = AltTextAnswer.model_validate({**ALT, "decorative": True, "alt": ""})
    assert answer.alt == ""


def test_answers_strip_control_characters() -> None:
    answer = AltTextAnswer.model_validate({**ALT, "alt": "Line one\n\x07Line two"})
    assert answer.alt == "Line one Line two"
    batch = NameBatchAnswer.model_validate(
        {"items": [{"id": "a", "name": "  Close\tchat ", "confidence": 1}]}
    )
    assert batch.items[0].name == "Close chat"


def test_strict_schema_shape() -> None:
    schema = strict_json_schema(AltTextAnswer)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "maxLength" not in json.dumps(schema)
    nested = strict_json_schema(NameBatchAnswer)
    item = nested["$defs"]["NameAnswer"]
    assert item["additionalProperties"] is False
