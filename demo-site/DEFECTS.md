# Daily Herald — planted defects

This file is the **source of truth** for the demo-site integration tests
(MASTERSPEC §15). Every defect below is deliberate. If you change the demo site,
change this table in the same commit, or the tests will disagree with reality.

Measured on the generated assets at 2,320,779 bytes first load, viewport
1366×768 (MASTERSPEC §6.1). That weight yields 0.671 g CO2e per view under the
Sustainable Web Design v3 model, which is carbon grade **E** — inside the E/F
band MASTERSPEC §11 targets.

Byte costs are **estimates** produced by the formulas in MASTERSPEC §7.3, not
measurements of a real saving. Tests assert ranges, never exact bytes.

## Accessibility defects

| ID | Where | Expected to be caught by | Est. byte cost |
|---|---|---|---|
| `HTML-LANG-01` | `index.html` — `<html>` has no `lang` | axe `html-has-lang` | 0 |
| `LANDMARK-01` | `index.html` — `div.masthead` / `div.wrap` / `div.footer` instead of `header`/`main`/`footer`; no skip link | axe `region`, `landmark-one-main` | 0 |
| `HEADING-ORDER-01` | `.hero__overlay` — `h1` followed directly by `h4`; all card titles are `h4` | axe `heading-order` | 0 |
| `IMG-ALT-01` | 6 of 8 `.card__image` elements plus `banner-subscribe.png` have no `alt` (7 nodes) | axe `image-alt` | 0 |
| `LINK-NAME-01` | `.nav__icon` — icon-only link containing an empty `span` | axe `link-name` | 0 |
| `CHAT-CLOSE-01` | `.chat__close` — `×` button with no accessible name | axe `button-name` | 0 |
| `FORM-LABEL-01` | `.newsletter__form` — `name`, `email`, `edition` and `terms` controls have no label of any kind (4 nodes) | axe `label`, `select-name` | 0 |
| `CONTRAST-01` | `.fine-print` (#B8B8B8 on #FFF ≈ 1.9:1), `.masthead__date` (#B0B0B0 ≈ 2.1:1), `.card__meta` (#B4B4B4 ≈ 2.0:1) | axe `color-contrast` | 0 |
| `DIV-BUTTON-01` | `.pager__btn` ×3 and `.tag` ×4 — clickable `div`s with `onclick`, no `tabindex`, no keyboard handler | trade-off detector `div_soup_widgets`; one has `role="button"` so axe may also flag it | 0 |
| `FOCUS-01` | `.nav a { outline: none }` — focus indicator removed with no replacement | keyboard crawl `focus_visible_missing_count` | 0 |
| `TRAP-01` | `#promo` — `js/main.js` cancels every Tab press once focus is inside, cycling 3 controls forever; no Escape handler | keyboard crawl `trap_detected` | 0 |

### Notes on the keyboard trap

The page has roughly 28 focusable elements before `#promo-yes`, and the trap
cycle is 3 elements long. A 60-press Tab crawl (MASTERSPEC §6.4) reaches the trap
and then observes about 10 full cycles, comfortably over the "≥ 3 full cycles"
threshold. `trap_container` should resolve to `#promo`.

## Carbon defects

| ID | Where | Expected detector (§7.3) | Est. byte cost |
|---|---|---|---|
| `VIDEO-AUTOPLAY-01` | `.hero__video` — `autoplay loop muted preload="auto"`, no poster | `autoplay_media` | ~1,001,000 (full media bytes) |
| `IMG-OVERSIZE-01` | 8 `.card__image` — natural 3000×2000, rendered 400×260 | `oversized_image` | ~648,000 total (~81,000 each) |
| `IMG-EAGER-01` | every image below the first viewport, all `loading="eager"` | `eager_below_fold` | ~1,077,000 (deferred, not removed) |
| `IMG-DIM-01` | all 10 `img` elements — no `width`/`height` attributes | `no_dimensions` | 0 (layout-shift note) |
| `IMG-TEXT-01` | `banner-sale.jpg` — 1600×400, text baked in, 175-char `alt` duplicating it | `text_in_image_suspected` | ~184,000 (bytes − ~2 KB of text) |
| `IMG-TEXT-02` | `banner-subscribe.png` — 1600×400, text baked in, no `alt` | `text_in_image_suspected` | ~192,000 |
| `THIRD-PARTY-01` | 4 tracker scripts from `localhost:8082` | `third_party_scripts` | ~9,200 |
| `FONT-BLOAT-01` | 4 woff2 files (203,528 bytes); the two `latin-ext` subsets add no coverage this page renders | `font_bloat` (>3 files **and** >150 KB) | ~144,000 (fonts − 60 KB) |
| `UNCOMPRESSED-01` | `index.html`, `css/main.css`, `js/main.js` and the 4 trackers served with no `Content-Encoding` | `uncompressed_text` | ~28,000 (~70% of ~39,500 decoded) |
| `MOTION-01` | `css/main.css` — 5 `@keyframes` animations plus `.tag` transitions, and no `prefers-reduced-motion` rule anywhere | `no_reduced_motion` | 0 (CPU note) |

### Which images are oversized

All eight `article-0N.jpg` are 3000×2000 natural and render at 400×260 CSS
pixels. Applying the §7.3 formula with the ×2 DPR allowance:

```
saving = bytes × (1 − (400×2 / 3000)²) = bytes × 0.929
```

The two banners are **not** expected to trip `oversized_image`: they are
1600 px natural and render up to 820 px, so `(820×2/1600)² > 1` and the formula
floors at 0. They are `text_in_image_suspected` cases instead.

## First-load byte budget

Measured with `python scripts/demo_page_weight.py` against a running demo:

| Kind | Bytes |
|---|---|
| img | 1,076,971 |
| media | 1,000,785 |
| font | 203,528 |
| js | 15,302 |
| html | 13,259 |
| css | 10,934 |
| **total** | **2,320,779** (2.32 MB, 22 requests) |

At that weight the Sustainable Web Design v3 model gives **0.671 g CO2e per
view**, i.e. carbon grade **E**. The D/E boundary sits at 0.656 g, which is
2,268,119 bytes — so the demo is deliberately kept above that line as well as
inside the 2.0–2.4 MB window.

MASTERSPEC §11 requires 2.0–2.4 MB. Regenerate with
`python scripts/make_demo_assets.py --force`; the generator binary-searches each
encoder setting to hit its byte target, so the total is reproducible.

## Deliberately *not* planted

Kept out so the demo stays realistic rather than a checklist:

- No `aria-hidden` on focusable content.
- No duplicate `id` values.
- No `tabindex` greater than 0.
- No missing document `<title>` (the page has one).

## Scoring expectations

MASTERSPEC §11 targets a **before** state of roughly 30–45 accessibility and a
carbon grade of E or F.

The carbon side already holds: 2,320,779 bytes gives 0.671 g/view, grade **E**.
The accessibility score is checked once the scoring module exists; this phase
asserts detection only.
