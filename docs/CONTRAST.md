# Contrast: how the §13 palette is enforced

`scripts/contrast_check.py` parses `frontend/src/styles/tokens.css` and asserts every
foreground/background pair the UI paints against WCAG 2.1 AA. It runs in `make lint`
and fails the build on any regression.

Thresholds used:

| Rule | Requirement |
|---|---|
| WCAG 1.4.3 Contrast (Minimum), normal text | 4.5:1 |
| WCAG 1.4.3 Contrast (Minimum), large text (≥18.66px bold / ≥24px regular) | 3:1 |
| WCAG 1.4.11 Non-text Contrast (meaningful UI boundaries) | 3:1 |

## Two findings against MASTERSPEC §13

§13 states "Check every text/background pair for ≥ 4.5:1 (≥ 3:1 for large text and UI
borders)". Measured against the palette §13 itself lists, two pairs do not reach that bar.
**No spec token value was changed.** In both cases a companion token was added.

### 1. `--amber-text` on `--amber-fill` is 4.02:1

`#7A4E00` on `#F2B84B` measures **4.02:1** — enough for large text (3:1), short of the
4.5:1 needed for normal-size text.

- `--amber-text: #7A4E00` is kept exactly as specified and is registered as
  **large-text-only**.
- `--amber-text-strong: #6B4400` was added for normal-size text on amber, measuring
  **4.79:1**. It is the same hue, darkened.

### 2. `--border` on `--bg` is 1.34:1

`#DDD8CC` on `#FAF8F3` measures **1.34:1**. This is fine — and intended — for a decorative
hairline: WCAG 1.4.11 applies only to boundaries that convey information or identify a
control, and explicitly exempts purely decorative ones.

- `--border: #DDD8CC` is kept as specified, for decorative separators only. The checker
  holds it in a `DECORATIVE_ONLY` set and **fails** if it is ever registered as a
  foreground in a checked pair.
- `--border-strong: #8C8571` was added for boundaries that do carry meaning — input
  outlines, control edges, chart axes — measuring **3.47:1** on `--bg`.

## Dark theme

§13 fixes only `--bg: #121212` and `--ink: #E8E8E8`. The remaining dark values were
derived here and verified to the same thresholds. The checker additionally asserts that
the `@media (prefers-color-scheme: dark)` block and the explicit `[data-theme='dark']`
block declare identical values, so the two routes into dark mode cannot drift apart.

## Current result

44 pairs checked across 2 themes — all pass. Reproduce with:

```
python scripts/contrast_check.py
```
