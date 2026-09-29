"""Structured answers the LLM must return, one pydantic model per prompt.

Single responsibility: define and validate every shape the model may answer
with (MASTERSPEC §8.2: "JSON schema enforced; validate with pydantic"). The
same models produce the JSON schema sent as the API's structured-output
format, so the request and the validation can never disagree.

Validation here is also the injection boundary. Page content reaches the
prompt, so an answer is treated as untrusted text: lengths are capped, control
characters stripped, language tags and colours checked against strict
patterns. Nothing an answer contains is ever inserted as markup; the patcher
writes it only as attribute values or text nodes, which lxml escapes.
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "AltTextAnswer",
    "LangAnswer",
    "NameAnswer",
    "NameBatchAnswer",
    "strict_json_schema",
]

_CONTROL: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]+")
_HEX: Final[re.Pattern[str]] = re.compile(r"^#[0-9a-fA-F]{6}$")
_LANG: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8}){0,2}$")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _CONTROL.sub(" ", text)).strip()


class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AltTextAnswer(_Answer):
    """One image, judged by the vision model (MASTERSPEC §8.1 kind 1).

    ``decorative`` images get ``alt=""`` (CLAUDE.md). Text drawn into the
    pixels is transcribed separately, for the text-in-image fix (kind 11).
    """

    decorative: bool
    alt: str = Field(max_length=250)
    #: Words visible in the image, in reading order; empty when there are none.
    visible_text: list[str] = Field(max_length=12)
    #: Dominant background and text colours, for a text replacement block.
    background_color: str
    text_color: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=300)

    @field_validator("alt", "reason")
    @classmethod
    def _plain(cls, value: str) -> str:
        return _clean(value)

    @field_validator("visible_text")
    @classmethod
    def _lines(cls, value: list[str]) -> list[str]:
        lines = [_clean(line)[:200] for line in value]
        return [line for line in lines if line]

    @field_validator("background_color", "text_color")
    @classmethod
    def _colour(cls, value: str) -> str:
        value = value.strip()
        if not _HEX.match(value):
            raise ValueError("colour must be #rrggbb")
        return value.lower()

    @model_validator(mode="after")
    def _decorative_means_empty(self) -> AltTextAnswer:
        if self.decorative and self.alt:
            raise ValueError('a decorative image must have alt ""')
        if not self.decorative and not self.alt:
            raise ValueError("an informative image needs non-empty alt text")
        return self


class NameAnswer(_Answer):
    """An accessible name for one control (kinds 2 and 4)."""

    id: str = Field(max_length=64)
    name: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("name")
    @classmethod
    def _plain(cls, value: str) -> str:
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("name must not be blank")
        return cleaned


class NameBatchAnswer(_Answer):
    """Names for every control in one batch, keyed by the ids we sent."""

    items: list[NameAnswer] = Field(max_length=100)


class LangAnswer(_Answer):
    """The document language (kind 3), as a BCP 47 tag."""

    lang: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("lang")
    @classmethod
    def _tag(cls, value: str) -> str:
        value = value.strip()
        if not _LANG.match(value):
            raise ValueError("lang must be a BCP 47 tag such as 'en' or 'en-GB'")
        return value


# Keywords the API's structured-output schemas do not accept. Pydantic still
# enforces every one of them when the answer is validated.
_UNSUPPORTED: Final[frozenset[str]] = frozenset(
    {"maxLength", "minLength", "maximum", "minimum", "maxItems", "minItems", "title", "default"}
)


def _strict(node: Any) -> Any:
    if isinstance(node, dict):
        out = {k: _strict(v) for k, v in node.items() if k not in _UNSUPPORTED}
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out["properties"])
        return out
    if isinstance(node, list):
        return [_strict(item) for item in node]
    return node


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """`model`'s JSON schema in the strict form structured outputs require."""
    schema: dict[str, Any] = _strict(model.model_json_schema())
    return schema


#: Which answer model each task uses; tasks name cache entries and usage rows.
Task = Literal["img_alt", "form_label", "link_name", "button_name", "html_lang"]
