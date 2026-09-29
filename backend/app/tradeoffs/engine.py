"""Trade-off engine (MASTERSPEC §10).

Single responsibility: run every rule in ``rules.json`` against a
:class:`~app.models.ScanResult` and return the
:class:`~app.models.TradeoffFinding` objects that fired.

The engine is rules-driven and fully deterministic. It performs no page loads,
no network calls and **no LLM calls**: MASTERSPEC §10 allows an LLM to rewrite
a filled-in explanation later, when ``LLM_OFFLINE`` is off, but that is a
separate, optional pass over the finished findings. Nothing here depends on it,
and with no API key at all the engine produces the same findings.

Carbon deltas
-------------
``carbon_delta_bytes`` is the transfer-byte change from applying the rule's
recommended fix: positive saves, negative costs, zero is byte-neutral.
``carbon_delta_grams`` is that same figure run through the *same* Sustainable
Web Design function the carbon score uses (:func:`app.carbon.swd.per_visit`),
with the page's real green-hosting verdict, so a card and a gauge can never
disagree about what a byte is worth. Both are estimates (MASTERSPEC §7).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.carbon import swd
from app.models import ScanResult, TradeoffFinding, TradeoffType
from app.tradeoffs.detectors import REGISTRY, DetectorHit, human_bytes

logger = logging.getLogger(__name__)

__all__ = [
    "ENGINE_FACTS",
    "CarbonEffect",
    "Rule",
    "evaluate",
    "load_rules",
]

RULES_PATH: Final[Path] = Path(__file__).parent / "rules.json"

#: Fill-ins the engine adds to every detector's facts, so templates can rely on
#: them without each detector repeating the work. ``plural`` is derived from a
#: detector's ``count`` when the detector did not supply its own.
ENGINE_FACTS: Final[frozenset[str]] = frozenset(
    {"bytes_abs_human", "bytes_phrase", "grams_abs", "direction", "evidence_count", "plural"}
)

#: Byte-provenance tokens ``carbon_effect.bytes_fn`` may use. The token records
#: where a finding's byte estimate came from; it is validated so a typo in the
#: rules file fails at load rather than showing an unexplained number.
_BYTES_FN_PREFIXES: Final[tuple[str, ...]] = ("detection:", "constant:")
_BYTES_FN_LITERALS: Final[frozenset[str]] = frozenset({"zero"})


class CarbonEffect(BaseModel):
    """The ``carbon_effect`` block of a rule (MASTERSPEC §10)."""

    model_config = ConfigDict(extra="forbid")

    #: Provenance token for the byte figure. See :data:`_BYTES_FN_PREFIXES`.
    bytes_fn: str
    #: Nuance about the byte figure, appended to the finding's explanation.
    note: str


class Rule(BaseModel):
    """One entry in ``rules.json`` (MASTERSPEC §10)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: TradeoffType
    title: str
    detector: str
    params: dict[str, object] = Field(default_factory=dict)
    a11y_effect: str
    carbon_effect: CarbonEffect
    fix_id: str | None = None
    explanation_template: str


class _StrictFacts(dict):
    """Dict that names the missing key when a template asks for one."""

    def __missing__(self, key: str) -> str:
        raise KeyError(key)


def _validate(rules: list[Rule]) -> None:
    """Fail loudly on a rules file that cannot produce honest findings."""
    seen: set[str] = set()
    for rule in rules:
        if rule.id in seen:
            raise ValueError(f"duplicate rule id in rules.json: {rule.id}")
        seen.add(rule.id)

        if rule.detector not in REGISTRY:
            raise ValueError(
                f"rule {rule.id!r} names detector {rule.detector!r}, which is not in the registry"
            )

        token = rule.carbon_effect.bytes_fn
        if token not in _BYTES_FN_LITERALS and not token.startswith(_BYTES_FN_PREFIXES):
            raise ValueError(f"rule {rule.id!r} has an unrecognised bytes_fn: {token!r}")


@lru_cache(maxsize=1)
def load_rules() -> tuple[Rule, ...]:
    """Parse and validate ``rules.json``. Cached: the file never changes at runtime."""
    raw = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    rules = [Rule.model_validate(entry) for entry in raw]
    _validate(rules)
    return tuple(rules)


def _format_grams(grams: float) -> str:
    """Grams with enough precision to be meaningful at page scale."""
    magnitude = abs(grams)
    precision = 4 if magnitude < 0.01 else 3
    return f"{grams:.{precision}f} g CO2e"


def _engine_facts(hit: DetectorHit, grams: float) -> dict[str, str]:
    if hit.bytes_delta > 0:
        direction = "saves"
    elif hit.bytes_delta < 0:
        direction = "costs"
    else:
        direction = "changes"

    abs_human = human_bytes(abs(hit.bytes_delta))
    grams_abs = _format_grams(abs(grams))

    # A single ready-made clause, so a template never has to stitch a verb to a
    # number that might be zero and end up saying "changes about 0 B".
    if hit.bytes_delta == 0:
        bytes_phrase = "changes no transfer bytes"
    else:
        bytes_phrase = f"{direction} about {abs_human} ({grams_abs} per view, estimated)"

    facts = {
        "bytes_abs_human": abs_human,
        "bytes_phrase": bytes_phrase,
        "grams_abs": grams_abs,
        "direction": direction,
        "evidence_count": str(len(hit.evidence)),
    }

    # Derive `plural` from `count` unless the detector supplied its own.
    if "plural" not in hit.facts:
        count = hit.facts.get("count")
        facts["plural"] = "" if count == "1" else "s"
    return facts


def _fill(rule: Rule, facts: dict[str, str]) -> str:
    try:
        return rule.explanation_template.format_map(_StrictFacts(facts))
    except KeyError as exc:  # pragma: no cover - guarded by test_tradeoffs
        raise ValueError(
            f"rule {rule.id!r} explanation_template needs fact {exc.args[0]!r}, "
            f"which detector {rule.detector!r} does not provide"
        ) from exc


def _finding(rule: Rule, hit: DetectorHit, *, green: bool) -> TradeoffFinding:
    # Same model, same constants, same green flag as the carbon score. per_visit
    # is linear in bytes, so running the magnitude and re-applying the sign is
    # exact, and avoids asking the model for a negative byte count.
    magnitude = swd.per_visit(abs(hit.bytes_delta), green=green)
    grams = magnitude if hit.bytes_delta >= 0 else -magnitude

    facts = {**hit.facts, **_engine_facts(hit, grams)}
    explanation = f"{_fill(rule, facts)} {rule.carbon_effect.note}".strip()

    return TradeoffFinding(
        rule_id=rule.id,
        type=rule.type,
        title=rule.title,
        a11y_impact=rule.a11y_effect,
        carbon_delta_bytes=hit.bytes_delta,
        carbon_delta_grams=grams,
        evidence=hit.evidence,
        recommended_fix_id=rule.fix_id,
        explanation=explanation,
    )


def evaluate(result: ScanResult) -> list[TradeoffFinding]:
    """Run every rule against `result`.

    Order is deterministic: synergies first, each group sorted by the size of
    its byte effect, then by rule id. A detector that raises is logged and
    skipped rather than failing the scan, because one bad rule must not cost
    the user a finished audit.
    """
    findings: list[TradeoffFinding] = []

    for rule in load_rules():
        detector = REGISTRY[rule.detector]
        try:
            hit = detector(result)
        except (KeyError, ValueError, TypeError, AttributeError, ZeroDivisionError):
            logger.exception(
                "trade-off detector %s failed; skipping rule %s", rule.detector, rule.id
            )
            continue
        if hit is None:
            continue
        findings.append(_finding(rule, hit, green=result.green.green))

    findings.sort(
        key=lambda finding: (
            finding.type is not TradeoffType.SYNERGY,
            -abs(finding.carbon_delta_bytes),
            finding.rule_id,
        )
    )
    return findings
