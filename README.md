# GreenAccess

Paste a URL. Get one score for how inclusive and how green a website is, then a
one-click patch that fixes both, with the trade-offs explained and the result
proved by a second real scan.

Accessibility and carbon are normally audited by different tools, by different
people, at different times. They are not independent variables. Removing a
900 KB autoplay video helps both at once. Replacing a banner image that has text
baked into it helps both at once. Adding captions to a video helps accessibility
and costs a few kilobytes. Shipping a dark theme can cut energy on OLED screens
and simultaneously destroy text contrast. GreenAccess measures both dimensions in
a single pass and is explicit about which fixes are wins on both axes and which
are genuine conflicts.

The distinguishing claim is the last step. Most audit tools stop at advice.
GreenAccess generates the fix, applies it to a real copy of the page, serves that
copy, and runs the entire scan pipeline again against it. Every "after" number in
this repository is measured, not predicted.

---

## Table of contents

1. [Verified results](#1-verified-results)
2. [System architecture](#2-system-architecture)
3. [Repository layout](#3-repository-layout)
4. [Quick start](#4-quick-start)
5. [Command reference](#5-command-reference)
6. [The scan pipeline](#6-the-scan-pipeline)
7. [Scoring](#7-scoring)
8. [The carbon model](#8-the-carbon-model)
9. [Carbon and accessibility detectors](#9-carbon-and-accessibility-detectors)
10. [The trade-off engine](#10-the-trade-off-engine)
11. [Fix generation and patching](#11-fix-generation-and-patching)
12. [Security model](#12-security-model)
13. [HTTP API](#13-http-api)
14. [Data model](#14-data-model)
15. [The demo site](#15-the-demo-site)
16. [Testing and verification](#16-testing-and-verification)
17. [Configuration](#17-configuration)
18. [Project status and known gaps](#18-project-status-and-known-gaps)
19. [Accuracy commitments](#19-accuracy-commitments)

---

## 1. Verified results

The figures below were produced on 2026-09-30 by running the real pipeline
against the bundled demo site, twice: once before any fix and once against the
patched copy the tool itself generated. No number here is hardcoded, cached or
estimated by hand. Reproduce them with `make dev` plus the API calls in
[section 13](#13-http-api), or with `make demo-replay`.

| Measure | Before | After | Change |
|---|---|---|---|
| Accessibility score | 0 | 66 | +66 |
| Carbon score | 38 (grade E) | 90 (grade A) | +52 |
| Combined score | 19 | 78 | +59 |
| Transfer size | 2,327,022 bytes | 455,305 bytes | -80.4 percent |
| Estimated grams CO2e per view | 0.6730 g | 0.1317 g | -80.4 percent |
| Distinct axe rules violated | 9 | 2 | -7 |
| Failing elements | 43 | 23 | -20 |
| Fixes applied | n/a | 36 of 36 accepted | 0 skipped |

Engine versions behind those numbers: Playwright 1.63.0, axe-core 4.13.0,
Sustainable Web Design model v3, `@tgwf/co2` 0.19.0 as the parity reference.

The accessibility score of 0 is the scoring floor reached honestly. The demo
site's planted defects sum to slightly more than 100 penalty points under the
published formula in [section 7](#7-scoring), and the formula clamps at zero. It
is not a scanner failure and not a placeholder.

---

## 2. System architecture

```
                        ┌──────────────────────────────────────┐
                        │  Browser (the operator)              │
                        │  React 18 + TypeScript + Vite :5173  │
                        │                                      │
                        │  One scrolling narrative, not tabs:  │
                        │  Opening / Scan / Progress / Verdict  │
                        │  Access / Carbon / Trade-offs /      │
                        │  Fixes / After                       │
                        └───────────┬──────────────────────────┘
                                    │
                    HTTP (JSON)     │   Server-Sent Events
                    POST /api/scans │   GET /api/scans/{id}/events
                                    ▼
    ┌───────────────────────────────────────────────────────────────────────┐
    │  FastAPI backend, Uvicorn :8000                                       │
    │                                                                       │
    │  api/       routing, SSE fan-out, rate limit (10/min/IP),             │
    │             concurrency cap (2 scans), error envelope                 │
    │                                                                       │
    │  security/  ssrf.py         every URL validated before any I/O        │
    │             redirects.py    redirect chain pre-flight, address-pinned  │
    │             egress_proxy.py per-scan proxy; sees EVERY connection      │
    │             fetch.py        capped, pinned asset downloads             │
    │                                                                       │
    │  scanner/   browser.py   Chromium, fresh context, 1366x768            │
    │             network.py   CDP Network.* transfer sizes                  │
    │             a11y.py      vendored axe-core injection                   │
    │             keyboard.py  own 60-press Tab crawl, trap detection        │
    │             aria.py      accessibility tree snapshot                   │
    │             pipeline.py  orchestrates the 10 steps, emits events       │
    │                                                                       │
    │  carbon/    swd.py + constants.py  Sustainable Web Design v3          │
    │             detectors.py           11 byte-savings detectors           │
    │             green.py               Green Web Foundation lookup         │
    │                                                                       │
    │  scoring/   pure functions: a11y.py, carbon.py, combined.py           │
    │  tradeoffs/ rules.json + detector registry, synergy vs tension        │
    │  llm/       Anthropic client, prompts, disk cache, vision             │
    │  patcher/   lxml DOM transforms, contrast solver, image optimiser,    │
    │             zip builder, and the re-scan that produces "after"        │
    │  db/        SQLModel over SQLite, scan history per host               │
    └────┬─────────────────────┬──────────────────┬───────────────────┬─────┘
         │                     │                  │                   │
         │ every browser       │ vision + fix     │ host lookup       │ static
         │ connection          │ generation       │                   │
         ▼                     ▼                  ▼                   ▼
  ┌─────────────────┐  ┌───────────────┐  ┌────────────────┐  ┌──────────────┐
  │ Egress proxy    │  │ Anthropic API │  │ Green Web      │  │ /patched/... │
  │ 127.0.0.1:eph.  │  │ (optional;    │  │ Foundation API │  │ restrictive  │
  │ enforces SSRF   │  │  offline by   │  │ 5s timeout     │  │ CSP, never   │
  │ on every hop    │  │  default)     │  │                │  │ executed     │
  └────────┬────────┘  └───────────────┘  └────────────────┘  └──────┬───────┘
           │                                                          │
           ▼                                                          │
  ┌────────────────────────────────────────────┐                      │
  │ Target site, or the bundled demo:          │                      │
  │   demo-site        :8081  Daily Herald     │◄─────────────────────┘
  │   demo-third-party :8082  fake trackers    │    the re-scan visits the
  └────────────────────────────────────────────┘    patched copy as a site
```

### The two pipelines

```
SCAN PIPELINE (one async job per scan; each step emits an SSE event)

  validate ─► load ─► a11y ─► keyboard ─► aria ─► carbon ─► green ─► score
                                                                       │
                                                        tradeoffs ◄────┘
                                                            │
                                                         persist


FIX PIPELINE (user-triggered, separate, reuses the scan pipeline at the end)

  generate fixes ─► operator selects ─► patch ─► serve patched copy
                                                        │
                                                        ▼
                                              re-scan (the SAME
                                              10-step pipeline)
                                                        │
                                                        ▼
                                                  "after" result
```

The re-scan is the architectural point worth noticing. There is no separate
"predicted improvement" code path that could drift from reality. The after
numbers come from the identical pipeline, pointed at a real URL serving the real
patched bytes, so a fix that does not actually work shows up as a score that does
not actually move.

---

## 3. Repository layout

```
backend/
  app/
    api/          routing, SSE, rate limiting, job runner, error envelope
    scanner/      Playwright driving, CDP network capture, axe, Tab crawl
      vendor/     axe.min.js, version pinned in axe-version.json
    carbon/       SWD v3 implementation, constants with provenance, detectors
    scoring/      pure scoring functions, one constants file
    tradeoffs/    rules.json, detector registry, explanation engine
    llm/          Anthropic client, prompt building, schemas, disk cache, vision
    patcher/      fix planning, lxml transforms, contrast solver, images, zip
    security/     ssrf.py, redirects.py, egress_proxy.py, fetch.py
    db/           SQLModel tables and repository
    fixtures/     labelled cached demo results and the offline LLM cache
    models.py     every pydantic model crossing a module boundary
    config.py     the entire environment surface, read once
    cli.py        `python -m app.cli scan <url>`
  tests/          33 test modules, 933 tests
frontend/
  src/
    scenes/       the narrative chapters, one file each
    components/   gauges, bars, chapter nav, score explainer, scroll scenery
    lib/          api client, SSE client, scan and fix hooks, formatters, types
    styles/       design tokens and per-chapter CSS, no UI framework
demo-site/        the Daily Herald: deliberately bad, fully catalogued
  DEFECTS.md      source of truth that the integration tests read
scripts/          asset generation, type generation, contrast gate, dev runner
docs/             supporting notes, including the contrast algorithm
masterspec.md     the full product specification
CLAUDE.md         working agreements and conventions
```

---

## 4. Quick start

Requirements: Python 3.12, Node 20 or newer (developed on 24.18.0), and
[uv](https://docs.astral.sh/uv/) for the Python environment. Docker is optional.

```bash
make install     # backend venv (Python 3.12) plus frontend npm install
make dev         # backend :8000, frontend :5173, demo :8081, trackers :8082
```

Then open `http://localhost:5173` and press "Try the Daily Herald demo".

With Docker instead, using the official pinned Playwright image:

```bash
docker compose up --build
```

No API key is required to run, scan, score, explain trade-offs or patch. The LLM
is offline by default and every deterministic fix works without it. See
[section 11](#11-fix-generation-and-patching) for exactly what the key adds.

A single scan without any server, straight from the CLI:

```bash
make scan URL=http://localhost:8081
```

That prints each pipeline step with its timing, the three scores, every
detection with its estimated byte saving, and every trade-off finding.

---

## 5. Command reference

| Command | What it does |
|---|---|
| `make install` | Creates the backend venv and installs both dependency sets |
| `make dev` | Runs all four services with prefixed, colourised logs |
| `make demo` | Runs only the Daily Herald and its tracker host |
| `make test` | Backend pytest plus frontend vitest |
| `make lint` | ruff check, ruff format check, the WCAG contrast gate, `tsc --noEmit`, eslint |
| `make scan URL=...` | Scanner CLI: steps, scores, detections, trade-offs |
| `make assets` | Regenerates the demo's images and hero video |
| `make weight` | Measures the demo's first-load transfer size |
| `make types` | Regenerates `frontend/src/lib/types.ts` from `backend/app/models.py` |
| `make demo-record` | Full fix loop with the live LLM; records the offline cache |
| `make demo-replay` | The same loop with `LLM_OFFLINE=1`, from the committed cache |
| `make build` | Production frontend build |
| `make image` | Builds the production image: API plus built UI, one origin |
| `make image-run` | Runs that image on :8000 with a local data volume |
| `make deploy` | `fly deploy` the application. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| `make deploy-demo` | `fly deploy` the Daily Herald and its tracker host |
| `make public` | Free public HTTPS URL with no credit card: the production image plus a Cloudflare tunnel. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) §9 |
| `make public-down` | Stop the public stack |
| `make clean` | Removes generated runtime data |
| `make e2e` | Not implemented. See [section 18](#18-project-status-and-known-gaps) |
| `make dogfood` | Not implemented. See [section 18](#18-project-status-and-known-gaps) |

---

## 6. The scan pipeline

Ten steps, each emitting a `step` SSE event with a name, a status and a duration,
so the operator watches real work rather than a fake progress bar.

**1. validate.** The submitted URL goes through `security/ssrf.py` before any
network call. Details in [section 12](#12-security-model).

**2. load.** A fresh Chromium context per scan: viewport 1366x768, cache
disabled, a user agent identifying `GreenAccessBot`, and a CDP session with
`Network` enabled. The page is navigated, allowed to reach network idle (capped
at 15 seconds), then scrolled a viewport at a time with a 250 ms pause per step,
up to 30 steps, to trigger lazy-loaded content. The scan then returns to the top
and takes a full-page screenshot. The load aborts if cumulative transfer exceeds
25 MB.

**3. a11y.** The vendored `axe.min.js` is injected and run with the tags
`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` and `best-practice`, using axe's own
defaults so iframes are not skipped. For each violation the scan keeps the rule
id, impact, help text, help URL, tags, and every failing node with its selector,
HTML snippet and failure summary. Items axe marks `incomplete` are counted and
reported but deliberately never scored, because "needs review" is not a
violation.

**4. keyboard.** Keyboard traps are not something axe detects, so GreenAccess
crawls for them itself. Focus starts on `body`, then Tab is pressed up to 60
times. After each press the crawl records the active element's identity: tag,
selector path and bounding box. A trap is declared when the same small cycle of
at most six elements repeats for at least three full cycles while focusable
elements exist outside that cycle. The crawl also records interactive elements
never reached, and elements that take focus with no visible focus indicator,
comparing computed `outline` and `box-shadow` between the focused and blurred
states and flagging only unambiguous absence.

**5. aria.** Playwright's `aria_snapshot()` on `body`, stored as text, which is
the closest honest approximation of what a screen reader is handed.

**6. carbon.** Per-request CDP data is aggregated. Transfer size is the sum of
`encodedDataLength`, which is bytes on the wire, not decoded size. Decoded size
is kept separately so uncompressed text resources can be detected. Requests are
classified by CDP `resourceType` into html, css, js, img, font, media and other.
Third party means a different registrable domain from the page's own. The eleven
detectors in [section 9](#9-carbon-and-accessibility-detectors) then run over
this data plus the DOM, and the SWD model converts bytes to grams.

**7. green.** A Green Web Foundation lookup for the host with a 5 second timeout.
A failure is reported as `source: "unavailable"` and displayed as "unknown",
never silently treated as green.

**8. score.** The three pure scoring functions in [section 7](#7-scoring).

**9. tradeoffs.** Findings matched against `tradeoffs/rules.json`. No extra page
loads: every detector reads scanner output that already exists.

**10. persist.** The `ScanResult` is written to SQLite.

Budgets enforced throughout: navigation timeout 30 s, total scan timeout 90 s,
maximum 2 concurrent scans, page-weight abort at 25 MB, and a fresh browser
context per scan.

---

## 7. Scoring

All three functions are pure, live in `backend/app/scoring/`, and are covered by
table-driven unit tests. Every constant lives in one file,
`scoring/constants.py`, with a provenance note and a verification status per
block. No other module is permitted to hardcode a scoring number.

### Accessibility, 0 to 100

```
score   = max(0, 100 - sum of penalty per violated rule)
penalty = base[impact] * min(1 + log10(nodes), 2)
base    = { critical: 10, serious: 7, moderate: 3, minor: 1 }
```

The penalty is charged per unique violated rule, not per failing element. The
node multiplier reaches its cap of 2.0 at ten nodes, so a rule broken ten times
and a rule broken five hundred times cost the same. That is deliberate: it stops
one repeated rule from consuming the entire score and flattening every other
signal. A keyboard trap adds a flat penalty of 10, equal to one critical rule at
a single node. Incomplete items are not scored.

Worked example, the demo site's before state, which is how a score of 0 arises:

| Rule | Impact | Nodes | Penalty |
|---|---|---|---|
| image-alt | critical | 7 | 18.5 |
| label | critical | 3 | 14.8 |
| button-name | critical | 1 | 10.0 |
| select-name | critical | 1 | 10.0 |
| color-contrast | serious | 13 | 14.0 |
| html-has-lang | serious | 1 | 7.0 |
| link-name | serious | 1 | 7.0 |
| region | moderate | 15 | 6.0 |
| heading-order | moderate | 1 | 3.0 |
| keyboard trap | flat | n/a | 10.0 |
| **Total** | | | **100.3** |

100 minus 100.3 clamps to 0.

### Carbon, 0 to 100

Grams CO2e per view is banded, then interpolated linearly inside the band:

| Grade | Grams per view (inclusive upper bound) | Score range |
|---|---|---|
| A+ | 0.095 | 95 to 100 |
| A | 0.186 | 85 to 94 |
| B | 0.341 | 70 to 84 |
| C | 0.493 | 55 to 69 |
| D | 0.656 | 40 to 54 |
| E | 0.846 | 25 to 39 |
| F | above 0.846 | 24 down to 0, reaching 0 at 3.0 g |

Verified hosting on green energy adds 3 points, capped at 100.

The band thresholds have a documented verification status rather than an
assurance. The specification asks for them to be checked against
websitecarbon.com's published ratings. Those pages returned HTTP 403 on
2026-09-29 and could not be read, so the status against that source is recorded
in the source file as UNVERIFIED. They were instead verified against the pinned
upstream implementation those pages describe, `@tgwf/co2` 0.19.0,
`SWDMV3_RATINGS`, where all six thresholds match exactly. That is recorded as
VERIFIED with its date. A test asserts the duplicate copy of the band edges in
`carbon/constants.py` agrees with this one, so the two cannot drift.

### Combined

```
combined = round(w_a11y * a11y + w_carbon * carbon)
```

Default weights 0.5 and 0.5, adjustable between 0.3 and 0.7.

Every score in the UI carries a "How is this calculated?" explanation that links
to the formula and lists the specific items contributing most to it.

---

## 8. The carbon model

Implemented in `carbon/swd.py` with every constant in `carbon/constants.py`. The
constants were not written from memory. Each block carries the upstream file and
symbol it came from, for a pinned version.

Model: Sustainable Web Design v3, as implemented by `@tgwf/co2` 0.19.0.

```
bytes ─► gigabytes ─► kWh  (0.81 kWh per GB)
                        │
                        ├─ end-user device   52 percent
                        ├─ network           14 percent
                        ├─ data centre       15 percent
                        └─ production        19 percent
                        │
                        ▼
      multiplied by grid intensity, 472.94 gCO2e/kWh global average,
      except the data-centre segment on a verified green host, which
      uses 50 gCO2e/kWh
                        │
                        ▼
                  grams CO2e
```

The reported `grams_per_view` is the model's per-visit blend: 75 percent first
time visitors loading the full page, 25 percent returning visitors loading 2
percent of it from cache. First visit and return visit figures are reported
separately as well, and every assumption used is exposed on the API response in
`CarbonResult.assumptions` rather than buried in the implementation.

Correctness is checked by parity rather than by assertion. `scripts/gen_co2_fixtures.mjs`
runs the real CO2.js library over a set of byte counts once, and commits the
output to `backend/tests/fixtures/co2js.json`. `test_swd_parity.py` then asserts
this Python port produces the same numbers. If the port drifts from the reference
implementation, that test fails.

Carbon figures are labelled estimates everywhere they appear: in the API via
`is_estimate: true`, in the CLI output, and in the UI.

---

## 9. Carbon and accessibility detectors

Eleven detectors run over scanner output. Each returns findings plus an estimated
byte saving, and each estimate is labelled as an estimate rather than presented as
a measurement.

| Detector | Logic | Estimated saving |
|---|---|---|
| `oversized_image` | Natural width more than twice rendered width, allowing for DPR 2 | `bytes * (1 - (rendered*2 / natural)^2)`, floored at 0 |
| `legacy_format` | JPEG, PNG or GIF above 30 KB where WebP or AVIF applies | 30 percent for JPEG, 50 percent for PNG |
| `no_dimensions` | `<img>` with no width or height attribute | No bytes. Layout shift is an accessibility note |
| `eager_below_fold` | Image below the first viewport without `loading="lazy"` | Full bytes, deferred rather than removed |
| `text_in_image_suspected` | Image at least 40 KB, banner aspect ratio, wide, JPEG or PNG, alt longer than 25 characters | Image bytes minus roughly 2 KB of real text |
| `autoplay_media` | `<video autoplay>`, or an animated GIF above 200 KB | Full media bytes if replaced by a poster |
| `third_party_scripts` | JavaScript from another registrable domain | Script bytes |
| `font_bloat` | More than 3 font files, or more than 150 KB of fonts | Total fonts minus a 60 KB estimate |
| `uncompressed_text` | HTML, CSS or JS with no gzip or brotli and decoded size above 2 KB | Roughly 70 percent of decoded size |
| `no_reduced_motion` | Animations or transitions exist and no `prefers-reduced-motion` rule in any same-origin stylesheet | No bytes. CPU and vestibular note |
| `no_color_scheme` | No `prefers-color-scheme` query, `color-scheme` declaration or meta tag | No bytes. Every visitor gets the light theme |

Two further findings are accessibility-only and carry no byte saving:
`div_soup_widgets` (clickable elements that are not buttons and cannot be reached
or activated from the keyboard) and `video_no_captions`.

The word "suspected" in `text_in_image_suspected` is load-bearing. The detector
uses a heuristic, not OCR, and the finding is presented as a suspicion.

---

## 10. The trade-off engine

This is the part that justifies auditing both dimensions together, and it works
completely without an LLM.

Rules live in `tradeoffs/rules.json`. Each names a detector from a registry, an
accessibility effect, a carbon effect with a byte function, a recommended fix
kind, and an explanation template. Detectors reuse scanner output, so matching
costs no extra page loads. If a key is configured, the LLM rewrites only the
template's fill-in language. The engine's findings, byte deltas and gram deltas
are produced deterministically either way.

**Synergies**, where one change helps both dimensions:

| Rule | Accessibility gain | Carbon gain |
|---|---|---|
| `text_in_image` | Real text is selectable, translatable and readable by a screen reader | Text weighs kilobytes; the image weighs hundreds |
| `autoplay_media` | No unrequested motion, a vestibular and cognitive win | The entire video transfer |
| `no_reduced_motion` | Honours a user's stated motion preference | CPU and therefore device energy |
| `eager_below_fold` | Less contention for content the reader wants now | Deferred bytes |
| `third_party_widgets` | Fewer unlabelled injected controls | Third-party script bytes |
| `div_soup_widgets` | Real buttons are focusable and announced | Less widget JavaScript |

**Tensions**, where the two dimensions genuinely disagree:

| Rule | The conflict |
|---|---|
| `captions_bytes` | Captions are required for deaf and hard-of-hearing users and add a caption file. Quantified: a few KB against the video's megabytes |
| `dark_mode` | Cuts OLED energy, risks contrast failures, and adds a CSS block |
| `lazy_above_fold` | Lazy loading saves bytes but delays content the reader is already looking at |
| `high_res_zoom` | Users who zoom need more pixels than the byte-minimal image provides |
| `font_subsetting` | Subsetting saves bytes and can drop glyphs some readers need |

Every finding card reports the accessibility impact, the carbon impact in both
bytes and grams, a synergy or tension badge, the evidence (selectors or URLs),
and the cheapest fix that satisfies both dimensions. On the demo site the engine
finds 6 synergies and 4 tensions.

---

## 11. Fix generation and patching

### What the LLM does and does not do

The LLM is deliberately confined. It is offline by default, and a missing API key
can never become a live call. Scanning, scoring, detection, the trade-off engine
and the majority of fixes all work with no key at all.

What it contributes when enabled: alt text for images, including the
decorative-versus-informative judgement, which is a genuine semantic decision and
sets `alt=""` for decorative images; accessible names drawn from surrounding
context; and the natural-language fill-ins in trade-off explanations.

What it is never allowed to do: compute a score, compute a carbon figure, or
decide a colour. Contrast repair is a deterministic algorithm that adjusts
lightness until the ratio reaches 4.5:1, or 3:1 for large text. That is arithmetic
and it belongs in code, not in a language model.

Controls on its use: one structured call per batch of fixes grouped by kind, with
a JSON schema enforced and the response validated by pydantic, retried once on
invalid JSON. Vision calls are limited to the six heaviest or most important
images, each downscaled to at most 768 px before sending. Prompts contain only
the offending element's HTML, at most two levels of parent context, and nearby
text. Full page HTML is never sent. Answers are cached on disk keyed by a hash of
prompt, image and model. Every AI-produced fix carries `ai_generated` and a
confidence value, and the UI labels it "AI-generated, review before use". The
scan summary reports the AI's own cost honestly: call counts and token counts.

### Fix kinds

Twenty kinds are generated. On the demo site this produces 52 fixes, of which 36
apply automatically and 16 are reported as needing manual review.

Applied automatically, 36 fixes in the measured run:

| Kind | Count | Note |
|---|---|---|
| `image_compress` | 10 | Resize to 2x rendered width, convert to WebP at quality 78 |
| `lazy_load` | 9 | Below-fold images only, plus width and height |
| `form_label` | 4 | `<label for>` or `aria-label` from context |
| `third_party_remove` | 4 | Only scripts on an explicit removable allow-list |
| `contrast` | 3 | Deterministic lightness solve, not an LLM decision |
| `html_lang`, `link_name`, `button_name` | 1 each | |
| `heading_order` | 1 | Only when the level change is trivial |
| `autoplay_video` | 1 | Drop `autoplay`, add `preload="none"` and `controls` |
| `reduced_motion` | 1 | Append a `prefers-reduced-motion` block |

Reported as needing manual review, 16 fixes in the same run:

| Kind | Count | Why it is not applied automatically |
|---|---|---|
| `img_alt` | 7 | Alt text is a semantic judgement. With no API key configured there is nothing to generate it, so all seven are deferred. With a key these become AI-generated fixes within the six-image vision budget |
| `text_in_image` | 2 | Replacement text and layout cannot be swapped in reliably on an arbitrary page |
| `region` | 1 | Adding landmarks safely requires understanding the page's structure |
| `focus_visible` | 1 | Needs a judgement about the existing focus style |
| `keyboard_trap` | 1 | The trap is in JavaScript, and no fix kind edits JavaScript |
| `div_soup_widgets` | 1 | Converting divs to buttons can break attached handlers |
| `font_bloat` | 1 | Subsetting risks dropping glyphs some readers need |
| `uncompressed_text` | 1 | A server configuration change, not a change to the page |
| `dark_mode_tokens` | 1 | Only safe where the page already uses CSS variables |

Nothing in the second table is guessed at. A tool that silently breaks a page is
worse than a tool that says plainly it cannot help.

### The patcher

The original HTML from the scan, plus same-origin assets, are written to a
per-scan working directory. Relative URLs are rewritten rather than papered over
with a `<base href>`. DOM transforms are applied with lxml. CSS changes go into an
appended `greenaccess-patch.css` rather than editing the original stylesheet.
Images are resized to twice their rendered width and converted to WebP at quality
78.

The result is served at `/patched/{scan_id}/index.html` as static files under a
restrictive Content Security Policy, never executed server-side. The policy
actually sent, verified with `curl`:

```
default-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; font-src 'self' data:; media-src 'self' data:;
frame-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none';
form-action 'none'
```

`X-Content-Type-Options: nosniff` is sent alongside it.

That patched URL is then re-scanned through the identical pipeline, which is what
produces the after numbers. The downloadable zip contains the patched HTML, the
CSS patch, the optimised images and a `CHANGES.md` listing every fix applied and
every fix skipped with its reason.

---

## 12. Security model

Accepting an arbitrary URL from an untrusted user and loading it in a real
browser is a server-side request forgery risk by construction. The defence is
layered, and the layering is the point.

**Layer 1, `security/ssrf.py`.** Every user-supplied URL passes through this
before any browser or HTTP call. It rejects non-HTTP(S) schemes and resolves the
host, then checks every returned A and AAAA record against the blocked ranges:
private, loopback, link-local, and the cloud metadata address 169.254.169.254.
Obfuscated forms are handled explicitly, including decimal and hexadecimal
integer addresses such as `0x7f000001` and octal forms such as `0177.0.0.1`.

**Layer 2, `security/redirects.py`.** A URL that is safe can redirect to one that
is not. This module follows the chain hop by hop without a browser and validates
every hop, so `POST /api/scans` can answer `URL_BLOCKED` synchronously instead of
accepting the scan and failing it later over SSE. Each hop connects to the exact
address validation just approved, carrying the original `Host` header and TLS
server name, so a DNS answer that changes between the check and the connection
cannot redirect the request. Proxies from the environment are ignored, because a
proxy would perform its own resolution.

**Layer 3, `security/egress_proxy.py`.** This is the enforcement layer, and it
exists because of a specific, documented limitation: Playwright's route handler
sees requests a page starts, but Playwright does not route redirect hops. If an
allowed URL answers `302 Location: http://169.254.169.254/`, Chromium follows it
without ever calling the route handler. A guard that only sees the first hop is
not a guard. So each scan gets its own proxy on an ephemeral loopback port, and
the browser is launched pointing at it. The proxy sees every connection the
browser makes: first hop, every redirect hop, subresources, iframes, workers and
WebSocket tunnels. It validates each one with the same `validate_url`, then
connects to the exact validated address and never re-resolves. Response bytes pass
through untouched, so the CDP transfer sizes the carbon model reads are
unaffected.

**Development hosts.** The demo site is on localhost, which the guard blocks by
default. It is permitted through an explicit `ALLOWED_LOCAL_HOSTS` list, never by
disabling the guard. `make scan` demonstrates the intended pattern: it passes
`--allow-local localhost:8081 --allow-local localhost:8082`.

**Other limits.** Navigation timeout 30 s, total scan timeout 90 s, at most 2
concurrent scans, page-weight abort at 25 MB, a fresh browser context per scan,
and API rate limiting at 10 requests per minute per IP. Asset downloads for the
patcher are streamed with a per-file cap and a total budget. API keys come only
from the environment. Keys and full page contents are never logged.

The SSRF test suite is table-driven over good and bad URLs, including decimal and
hexadecimal address forms, every redirect status code, relative `Location`
resolution, and a simulated DNS rebinding attack that changes the resolver's
answer between validation and connection.

---

## 13. HTTP API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | Status, version, engine versions |
| GET | `/api/demo` | The demo site's URL, so the UI hardcodes nothing |
| POST | `/api/scans` | Body `ScanRequest`, returns `{scan_id}`. Rate limited 10/min/IP |
| GET | `/api/scans/{id}/events` | SSE: `step`, `done`, `error` |
| GET | `/api/scans/{id}` | The full `Scan`, including `before`, `after` and `patch` |
| POST | `/api/scans/{id}/cancel` | Cancels a running scan |
| POST | `/api/scans/{id}/fixes` | Generates fixes, returns `[Fix]` |
| POST | `/api/scans/{id}/patch` | Body `{accepted_fix_ids}`, runs patch and re-scan |
| GET | `/api/scans/{id}/patch.zip` | The patched site as a zip |
| GET | `/api/scans/{id}/screenshot?state=before\|after` | PNG |
| GET | `/api/badge/{id}.svg` | Embeddable SVG badge for a finished scan |
| GET | `/api/history?host=` | Past scans and trend for a host |
| GET | `/patched/{scan_id}/...` | The patched copy, static, restrictive CSP |

Errors use a single envelope, `{"error": {"code": ..., "message": ...}}`, with
codes `URL_BLOCKED`, `TIMEOUT`, `PAGE_TOO_LARGE`, `NAV_FAILED`,
`LLM_UNAVAILABLE`, `PATCH_FAILED` and `NOT_FOUND`.

SSE step names are `validate`, `load`, `a11y`, `keyboard`, `aria`, `carbon`,
`green`, `score`, `tradeoffs` and `persist` for a scan, plus `fixes`, `patch` and
`rescan` for the fix pipeline. The fix pipeline reports on the same event stream.

A complete session against the demo site:

```bash
# 1. start a scan
curl -s -X POST http://localhost:8000/api/scans \
     -H 'Content-Type: application/json' \
     -d '{"url":"http://localhost:8081"}'
# -> {"scan_id":"555b0522b893417290fcb385b6596937"}

# 2. watch it happen
curl -s -N http://localhost:8000/api/scans/$ID/events

# 3. generate fixes, then apply the ones you accept
curl -s -X POST http://localhost:8000/api/scans/$ID/fixes
curl -s -X POST http://localhost:8000/api/scans/$ID/patch \
     -H 'Content-Type: application/json' \
     -d '{"accepted_fix_ids":[...]}'

# 4. read the real before and after
curl -s http://localhost:8000/api/scans/$ID
```

---

## 14. Data model

Every structure crossing a module boundary is a pydantic v2 model in
`backend/app/models.py`. The TypeScript mirror in `frontend/src/lib/types.ts` is
generated from it by `make types`, and a backend test fails if the committed file
is stale, so the two cannot silently diverge.

```
Scan             id, url, host, status, created_at, before, after?, patch?
ScanResult       scores, a11y, keyboard, carbon, green, tradeoffs,
                 aria_snapshot, screenshot_path, engine_versions
Scores           a11y, carbon, combined, carbon_grade, weights, is_placeholder
A11yResult       violations, counts_by_impact, unique_rules, total_nodes,
                 incomplete_count
Violation        rule_id, impact, help, help_url, tags, nodes[]
KeyboardResult   tabs_pressed, trap_detected, trap_container?, reached_count,
                 unreachable_interactive_count, focus_visible_missing_count
CarbonResult     total_bytes, request_count, by_type, third_party, images[],
                 autoplay_media[], fonts, uncompressed_text[], detections[],
                 grams_per_view, grams_first_visit, grams_return_visit,
                 assumptions, is_estimate
GreenResult      host, green, hosted_by?, source
TradeoffFinding  rule_id, type, title, a11y_impact, carbon_delta_bytes,
                 carbon_delta_grams, evidence[], recommended_fix_id, explanation
Fix              id, kind, target, description, diff, ai_generated, applied,
                 confidence, manual_review
PatchInfo        fixes[], zip_path, patched_url, skipped[]
```

Note `Scores.is_placeholder`. If the pipeline degrades and a score cannot be
computed honestly, it is marked, and the UI declines to render a verdict chapter
rather than showing a number it does not trust.

---

## 15. The demo site

`demo-site/` is The Daily Herald, a deliberately terrible but realistic newspaper
page served on port 8081, with a second host on 8082 serving fake trackers so
third-party detection has something genuine to find.

It exists for a specific reason. A demo that scans a live public website depends
on whatever that site happens to be serving that morning, which makes the
before-and-after numbers unreproducible and the demo fragile. The Daily Herald
makes them deterministic. Scanning a real site works and is a useful bonus, but
it is never the backbone.

Every defect is planted on purpose and catalogued in
[demo-site/DEFECTS.md](demo-site/DEFECTS.md), which the integration tests read as
their source of truth. Planted defects include a 1 MB autoplaying muted hero
video, two banner images with text baked into them, eight 3000 px JPEGs rendered
at 400 px with no dimension attributes and no lazy loading, missing alt text on
at least five images, four third-party tracker scripts, a chat widget with an
unlabelled close button, unlabelled form inputs, grey-on-white text below 3:1,
`<html>` with no `lang`, a heading order that jumps from h1 to h4, clickable divs
instead of buttons, CSS animations with no `prefers-reduced-motion`, four web
fonts including unused weights, a modal that traps Tab, and uncompressed CSS and
JS. First load is roughly 2.3 MB.

The scanner finds all of it. Measured: 9 axe rules over 43 elements, the Tab trap
correctly localised to `div#promo`, 6 elements with no focus indicator,
2,327,022 bytes over 22 requests, 39 carbon findings, and carbon grade E.

---

## 16. Testing and verification

Everything below was run on 2026-09-30 on Windows 11, Python 3.12.13, Node
24.18.0. Commands and output are reproduced rather than summarised.

```
$ make test-backend
933 passed, 1 xfailed in 185.16s

$ make test-frontend
Test Files  9 passed (9)
     Tests  109 passed (109)

$ make lint
All checks passed!                        # ruff check
109 files already formatted               # ruff format --check
66 pairs checked across 3 themes.         # WCAG contrast gate
All token pairs meet their WCAG 2.1 AA target.
                                          # tsc --noEmit, clean
                                          # eslint, clean

$ make build
✓ built in 293ms                          # 200.54 kB JS, 63.18 kB gzipped
```

The single `xfail` is deliberate and is the honest kind. It asserts the
specification's target of at least 85 accessibility after fixes, records exactly
why that target is not currently reachable, and is marked `strict=True` so it
will fail loudly the moment it starts passing. The recorded reasons: the keyboard
trap lives in the demo's `js/main.js` and no fix kind covers JavaScript; axe's
`region` rule has no corresponding landmark fix kind; and `image-alt` survives on
images beyond the six-image vision budget, or on all of them when no API key is
configured. The target was not tuned away to make a test pass.

Test strategy by layer:

- **Unit.** Scoring and carbon as table-driven tests over pure functions. SSRF
  over a table of good and bad URLs including decimal and hexadecimal addresses,
  every redirect status, relative `Location` resolution, and simulated DNS
  rebinding. Detectors against HTML fixtures. The contrast algorithm. Patcher
  transforms.
- **Parity.** `test_swd_parity.py` asserts the Python SWD port matches fixtures
  generated by the real CO2.js library.
- **Integration.** `test_demo_integration.py` runs the full pipeline against the
  demo on its real ports and asserts every defect in `DEFECTS.md` is found, using
  ranges rather than exact bytes so the test is not brittle.
- **Fix loop.** `test_patch_integration.py` runs scan, fixes, patch and re-scan
  and asserts the real after state. `make demo-replay` runs it offline.
- **Contrast gate.** `scripts/contrast_check.py` fails the build if any design
  token pair falls below its WCAG AA target. GreenAccess holds itself to the
  standard it audits for: 66 pairs across three themes.
- **Generated types.** A test fails if `frontend/src/lib/types.ts` is stale
  relative to `backend/app/models.py`.

---

## 17. Configuration

Every value is read once, in `backend/app/config.py`. The application runs
correctly with a completely empty environment.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables live LLM calls. Environment only, never logged |
| `ANTHROPIC_MODEL` | `claude-opus-5` | Model id |
| `LLM_OFFLINE` | `1` | Offline by default. A missing key can never become a live call |
| `ALLOWED_LOCAL_HOSTS` | empty | Explicit `host:port` allow-list for dev and demo hosts |
| `MAX_CONCURRENT_SCANS` | `2` | Concurrency cap |
| `SCAN_TIMEOUT_S` | `90` | Total scan budget |
| `NAV_TIMEOUT_S` | `30` | Navigation budget |
| `MAX_PAGE_BYTES` | `26214400` | Page-weight abort, 25 MB |
| `DATABASE_URL` | `sqlite:///./greenaccess.db` | Persistence |
| `DEMO_FALLBACK` | `0` | Serve the labelled cached demo result if a live scan fails |
| `PUBLIC_BASE_URL` | `http://localhost:5173` | Where the frontend is served |
| `DEMO_URL` | `http://localhost:8081` | Where the demo site is served |
| `PATCHED_BASE_URL` | `http://localhost:8000/patched` | Where patched copies are served |
| `LLM_CACHE_DIR` | `backend/.llm_cache` | Writable LLM answer cache |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE` | unset | Only for hosts whose Chromium build differs from the pinned one |

`DEMO_FALLBACK` deserves a note. When it is set and a live scan fails, the API
serves the committed cached result from `backend/app/fixtures/`, and the UI shows
a "cached result" chip. The fallback is visible to the viewer by design. A demo
that quietly substitutes cached numbers for live ones would be dishonest.

---

## 18. Project status and known gaps

This section is the honest inventory. It lists what is not finished as plainly as
what is.

### Complete and verified

Scanner core, all ten pipeline steps, CDP network capture, vendored axe
integration, the keyboard Tab crawl, the ARIA snapshot, the SWD v3 carbon model
with parity tests, the Green Web Foundation lookup, all three scoring functions,
the eleven detectors, the trade-off engine, the full HTTP API with SSE, SQLite
persistence and history, the SSRF guard, the redirect pre-flight, the per-scan
egress proxy, fix generation across twenty kinds, the lxml patcher, the image
optimiser, the deterministic contrast solver, the zip export, the badge
endpoint, the real re-scan,
and the frontend narrative through nine chapters: Opening, Scan, Progress,
Verdict, Access, Carbon, Trade-offs, Fixes and After.

### Not implemented

- **The offline LLM cache is empty.** `backend/app/fixtures/llm_cache/` contains
  only a README. `make demo-record` requires an `ANTHROPIC_API_KEY` and has not
  been run, so `make demo-replay` currently completes with zero LLM calls, live
  or cached. The consequence is visible and is reported by the tool rather than
  hidden: `image-alt` remains the largest single penalty in the after state,
  because nothing generated the alt text. Every deterministic fix still applies,
  which is why the after score is 66 rather than 0. This is the single largest
  gap in the project.
- **The Simulate chapter.** The ARIA tree is captured, stored and returned by the
  API, but no UI renders it, and the colour-blindness SVG filters are not built.
  Tier 2, cut-order item 3.
- **The impact panel.** The visitors slider converting to kg CO2e per year, km
  driven and phone charges is not built. Tier 2.
- **The history screen.** `GET /api/history` works and returns trend data. No UI
  consumes it. Tier 2, cut-order item 2.
- **`make e2e`.** The Playwright end-to-end target prints a not-implemented
  notice. The flow it would cover is exercised by `test_patch_integration.py`
  through the real API instead.
- **`make dogfood`.** The self-audit target prints a not-implemented notice. The
  contrast half of self-auditing is enforced on every `make lint` run by the
  contrast gate, which currently passes on 66 token pairs across three themes.

### Known behaviour worth explaining before someone reports it as a bug

- **The demo's before accessibility score is 0, not the 30 to 45 the
  specification targets.** The arithmetic is shown in
  [section 7](#7-scoring): the planted defects sum to 100.3 penalty points and
  the formula clamps at zero. The specification is explicit that if this target
  is missed, the demo's planted defects should be adjusted and never the scoring
  formula. The formula has not been touched. Reducing the planted defects
  slightly, for example dropping two of the seven missing-alt images, would put
  the before score inside the intended band. That is a demo-tuning decision,
  deliberately left open rather than resolved by weakening the scoring.
- **The keyboard trap survives patching.** The trap is implemented in the demo's
  JavaScript, and no fix kind modifies JavaScript. It is correctly detected both
  before and after, and reported as a manual fix.
- **`make dev` does not fail loudly on a port conflict.** If port 8000 is already
  in use, the backend exits with code 3 and the message appears in the combined
  log, while the other three services keep running. Check the log if the UI
  cannot reach the API.

---

## 19. Accuracy commitments

These are constraints on what the tool is permitted to claim, and they are
enforced in the code and the copy, not just aspired to.

- The tool says "automated checks" and "issues detected". It never says "WCAG
  compliant" or describes a page as "accessible" as a guarantee. Automated
  testing catches a minority of accessibility problems and cannot certify a page.
- Carbon values are estimates from a published model, and are labelled as
  estimates in the API (`is_estimate: true`), in the CLI output and in the UI.
- Keyboard traps are attributed to "our own Tab crawl, not axe", because that is
  where they come from.
- Detector byte savings are labelled estimates, not measurements. The only
  measured byte figures are the CDP transfer sizes.
- AI output is labelled "AI-generated, review before use" and carries a
  confidence value. Decorative images get `alt=""` rather than invented prose.
- The after score always comes from a real re-scan of the patched page. There is
  no prediction code path.
- No hardcoded scores and no mock numbers presented as real. Fixtures exist only
  in `backend/tests/` and `backend/app/fixtures/`, and the latter are labelled as
  a cached run.
- Where a value could not be verified against the source the specification names,
  the source file records that, with a date and a status, instead of implying
  verification that did not happen. See the carbon band provenance in
  [section 7](#7-scoring).

---

## Further reading

- [masterspec.md](masterspec.md) is the full product specification: goals,
  non-goals, data models, formulas, scope tiers and cut order.
- [CLAUDE.md](CLAUDE.md) holds the working agreements and code conventions.
- [demo-site/DEFECTS.md](demo-site/DEFECTS.md) catalogues every planted defect
  and is read by the integration tests.
- [docs/CONTRAST.md](docs/CONTRAST.md) documents the deterministic contrast
  repair algorithm.
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) is the runbook for hosting
  GreenAccess, including the two demo behaviours that differ in the cloud.
