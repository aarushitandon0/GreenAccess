# MASTERSPEC — GreenAccess

> Paste a URL. Get one score for how inclusive and how green your website is, then a one-click patch that fixes both, with the trade-offs explained.

Target: a short project, roughly 24–36 hours. Scope tiers in §14 define what to cut.

---

## 1. Goals and non-goals

**Goals**
- Audit one URL for accessibility and carbon in a single scan.
- Show three scores: Accessibility, Carbon, Combined, with transparent methodology.
- Explain overlaps and genuine tensions between accessibility and carbon fixes (trade-off engine).
- Generate fixes (code diffs, AI alt text, image compression), apply them, **re-scan for real after numbers**.
- Look and behave like a polished product, and pass its own audit.

**Non-goals**
- Full WCAG conformance testing. Only automated checks; state this in the UI.
- Crawling multiple pages. One URL per scan.
- Reliable patching of arbitrary third-party sites. Reliable for the fix types in §8; the rest is reported as "manual fix needed".
- Auth, billing, multi-tenant.

## 2. Users and the demo story

Primary user: a developer or site owner. Judge/demo user: sees the flow in 3 minutes (§13).

Demo backbone: a deliberately bad but realistic local site, **Daily Herald** (§11), giving deterministic before/after numbers. A real-site scan is a bonus, never the backbone.

## 3. Feature list

**Tier 1 — MVP**
1. URL input with SSRF-safe validation, plus "Try the demo site" button.
2. Headless scan: load page, scroll to trigger lazy content, wait for network idle.
3. Accessibility audit via axe-core (WCAG 2.0/2.1 A/AA + best-practice tags).
4. Keyboard focus/trap check via custom Tab crawl.
5. Carbon audit: transfer size, request count, third-party requests/scripts, image issues, autoplay media, font weight, uncompressed text resources.
6. g CO₂ per page view (Sustainable Web Design model) + green hosting check (Green Web Foundation).
7. Scores: Accessibility, Carbon, Combined (weight toggle).
8. Live progress via SSE.
9. AI fixes (structured JSON): diffs, alt text for heaviest images, image compression.
10. Patcher + real re-scan → After state.
11. Download patched site as zip.

**Tier 2 — Wow layer**
12. Trade-off/synergy engine (rules JSON).
13. Impact slider (monthly visitors → kg CO₂/yr → km driven, phone charges).
14. Screen-reader view (aria snapshot tree).
15. Color-blindness simulator (SVG filters).
16. Before/after screenshot slider + animated gauges.
17. Badge generator (SVG + embed snippet).
18. Scan history + trend per domain.
19. Self-audit ("GreenAccess scores 100 on itself").

**Tier 3 — Stretch (only if ahead)**
20. GitHub PR generation. 21. Chrome extension. 22. Shareable report link / PDF.

## 4. Architecture

```
React (Vite) ──HTTP + SSE──► FastAPI
                               ├─ scanner/    Playwright + axe + CDP network + tab crawl
                               ├─ carbon/     SWD formula + Green Web Foundation client
                               ├─ scoring/    pure score functions
                               ├─ tradeoffs/  rules.json + detector registry
                               ├─ llm/        Anthropic client, prompts, disk cache
                               ├─ patcher/    lxml transforms, image optimizer, zip
                               ├─ security/   ssrf.py
                               └─ db/         SQLite (SQLModel)
demo-site/ served separately on :8081
```

Scan pipeline (one async job per scan, emits SSE events):
1. `validate` — SSRF guard.
2. `load` — new browser context, CDP session enabled for `Network.*`, navigate, wait `networkidle` (cap 15s), scroll full page in steps, wait, return to top, take full-page screenshot.
3. `a11y` — inject vendored axe-core, run with tags `wcag2a, wcag2aa, wcag21a, wcag21aa, best-practice`; collect violations (id, impact, help, helpUrl, nodes with selector + html snippet).
4. `keyboard` — Tab crawl (§6.4).
5. `aria` — `page.locator("body").aria_snapshot()` stored as text.
6. `carbon` — aggregate network data, run detectors (§7), compute grams.
7. `green` — Green Web Foundation lookup for the host.
8. `score` — compute three scores.
9. `tradeoffs` — match findings against rules.
10. `persist` — save `ScanResult`.

Fix pipeline (separate, user-triggered): `generate fixes → user selects/accepts → patch → serve patched copy → re-scan (same pipeline) → After result`.

## 5. Data models (pydantic; mirror in TS)

```
ScanRequest      { url: HttpUrl, weights?: {a11y: float, carbon: float} }
Scan             { id, url, host, status, created_at, before: ScanResult, after?: ScanResult, patch?: PatchInfo }
ScanResult       { scores: Scores, a11y: A11yResult, keyboard: KeyboardResult, carbon: CarbonResult,
                   green: GreenResult, tradeoffs: [TradeoffFinding], aria_snapshot: str,
                   screenshot_path: str, engine_versions: {playwright, axe, swd_model} }
Scores           { a11y: int, carbon: int, combined: int, carbon_grade: "A+".."F", weights }
A11yResult       { violations: [Violation], counts_by_impact, unique_rules, total_nodes, incomplete_count }
Violation        { rule_id, impact, help, help_url, tags, nodes: [{selector, html, failure_summary}] }
KeyboardResult   { tabs_pressed, trap_detected: bool, trap_container?: str, unreachable_interactive_count, focus_visible_missing_count }
CarbonResult     { total_bytes, request_count, by_type: {html,css,js,img,font,media,other: bytes},
                   third_party: {requests, bytes, hosts: [..]}, images: [ImageIssue], autoplay_media: [..],
                   fonts: {count, bytes}, uncompressed_text: [..], grams_per_view, grams_first_visit, grams_return_visit,
                   assumptions: {...}, is_estimate: true }
ImageIssue       { url, bytes, natural_w, natural_h, rendered_w, rendered_h, format, issues: ["oversized","legacy_format","no_dimensions","eager_below_fold","text_in_image_suspected"], selector }
GreenResult      { host, green: bool, hosted_by?, source: "greenweb"|"unavailable" }
TradeoffFinding  { rule_id, type: "synergy"|"tension", title, a11y_impact, carbon_delta_bytes, carbon_delta_grams,
                   evidence: [selectors/urls], recommended_fix_id, explanation }
Fix              { id, kind, target, description, diff: {before, after}, ai_generated: bool, applied: bool, confidence, manual_review: bool }
PatchInfo        { fixes: [Fix], zip_path, patched_url, skipped: [{fix_id, reason}] }
```

## 6. Scanner details

### 6.1 Loading
- Chromium, viewport 1366×768, `reducedMotion: "no-preference"`, no cache, fresh context. Custom UA string including `GreenAccessBot`.
- Full-page scroll: step by viewport height, 250 ms pause, until bottom or 30 steps.
- Abort if cumulative bytes exceed 25 MB.

### 6.2 Network metrics (CDP)
- Enable `Network`. Track per request: `requestWillBeSent` (url, type, initiator), `responseReceived` (mime, headers incl. `content-encoding`), `loadingFinished` (`encodedDataLength`).
- `total_bytes` = sum of `encodedDataLength` (transfer size). Also record the `Content-Length`/decoded size for uncompressed detection.
- Third party = registrable domain differs from page's. Use `tldextract` (offline mode) or a simple public-suffix approach; document choice.
- Type mapping from CDP `resourceType`: Document→html, Stylesheet→css, Script→js, Image→img, Font→font, Media→media.

### 6.3 Accessibility
- Vendor axe-core file in `backend/app/scanner/vendor/axe.min.js`; record version in `engine_versions`.
- Do not run axe in an isolated way that skips iframes; use axe defaults.
- Store `incomplete` count but do not score it.

### 6.4 Keyboard crawl
- Focus `body`, press Tab up to 60 times. Each press: read `document.activeElement` identity (tag + selector path + bounding box).
- **Trap** = the same small cycle of ≤ 6 elements repeats for ≥ 3 full cycles while more focusable elements exist outside it (compare against a list of all focusable elements). Record container selector.
- Also record: interactive elements never reached (`unreachable_interactive_count`), and elements that receive focus with no visible focus indicator (compare computed `outline`/`box-shadow` on focus vs blur; flag only clear absence).
- Score impact: trap = critical-equivalent penalty (§9.1). Report as "detected by automated keyboard crawl".

### 6.5 Screen-reader view
- `aria_snapshot()` on `body`. Render as tree in UI. Store raw text.

## 7. Carbon module

### 7.1 Formula (Sustainable Web Design)
Implement in `carbon/swd.py` with constants in `carbon/constants.py`. **Do not write constants from memory.** In Phase 1, fetch the current SWD model page and CO2.js (`@tgwf/co2`) source, copy the constants for the pinned SWD version, record the source URL and version in a comment, and add a test that compares our output for several byte counts against fixtures generated once by running CO2.js (`scripts/gen_co2_fixtures.mjs`, output committed to `tests/fixtures/co2js.json`).

Model outline: bytes → GB → energy (kWh/GB) split across data centers, network, devices → × grid intensity (global average by default) → grams. Green-hosted data-center segment uses the model's renewable intensity. First visit uses full bytes; return visit uses a cached fraction. Report `grams_per_view` = weighted blend using the model's default return-visitor assumptions. Expose all assumptions in `CarbonResult.assumptions`.

### 7.2 Green hosting
- Green Web Foundation API for the host, 5s timeout. On failure → `source: "unavailable"`, treated as not green, shown as "unknown".

### 7.3 Detectors (each returns findings + estimated bytes saved)
| Detector | Logic | Est. bytes saved |
|---|---|---|
| oversized_image | natural width > 2× rendered width (× DPR 2 allowance) | bytes × (1 − (rendered·2 / natural)²), min 0 |
| legacy_format | jpg/png/gif > 30 KB where WebP/AVIF would apply | 30% of bytes for jpg, 50% for png (constants, labelled estimates) |
| no_dimensions | `<img>` without width/height | 0 bytes; layout-shift a11y note |
| eager_below_fold | img below first viewport without `loading=lazy` | full bytes of images below fold (deferred, not removed) |
| text_in_image_suspected | image ≥ 40 KB, banner aspect ratio, and either alt contains lots of text or LLM/OCR-free heuristic: wide + JPEG/PNG + `alt` length > 25 chars. Flag as "suspected" | image bytes minus ~2 KB text |
| autoplay_media | `<video autoplay>` or `<video>` with `autoplay` attr, animated GIF > 200 KB | full media bytes if replaced by poster |
| third_party_scripts | JS from other registrable domains | script bytes |
| font_bloat | > 3 font files or > 150 KB fonts | fonts − 60 KB estimate |
| uncompressed_text | html/css/js without gzip/br and decoded size > 2 KB | ~70% of decoded size |
| no_reduced_motion | CSS animations/transitions exist and no `@media (prefers-reduced-motion)` rule in any same-origin stylesheet | 0 bytes; CPU note |

## 8. Fix generation and patching

### 8.1 Fix kinds (reliable set)
1. `img_alt` — add alt. Vision model decides **decorative vs informative**; decorative → `alt=""`.
2. `form_label` — add `<label for>` or `aria-label` from context.
3. `html_lang` — add `lang` (LLM or detect).
4. `link_name` / `button_name` — add accessible name.
5. `heading_order` — adjust levels only if trivial; else manual.
6. `contrast` — CSS override rule with computed accessible color (deterministic algorithm, not LLM): adjust lightness until ≥ 4.5:1 (or 3:1 for large text).
7. `lazy_load` — add `loading="lazy"` + `width/height` for below-fold imgs; never above the fold.
8. `autoplay_video` — remove `autoplay`, add `preload="none"`, `controls`, generate poster from first frame if possible (else skip poster).
9. `reduced_motion` — append `@media (prefers-reduced-motion: reduce)` block neutralizing animations/transitions.
10. `image_compress` — resize to 2× rendered width, convert to WebP (keep original as fallback via `<picture>` only if simple; otherwise swap `src`), quality 78.
11. `text_in_image` — mark **manual/assisted**: LLM proposes replacement text + CSS; applies only on the demo site where selector is known; elsewhere "manual fix needed".
12. `third_party_remove` — on-demand load or removal suggestion; auto-applied only for scripts explicitly allow-listed as removable (demo trackers). Elsewhere suggestion only.
13. `dark_mode_tokens` — optional CSS variables block honoring `prefers-color-scheme`, using `#121212` background and `#E8E8E8` text. Suggestion-only unless page uses CSS variables.

Everything else → `manual_review: true`, listed as "manual fix needed".

### 8.2 LLM usage
- One structured call per fix batch (grouped by kind), JSON schema enforced; validate with pydantic; retry once on invalid JSON.
- Vision calls: top **N=6** heaviest/most important images, images downscaled to ≤ 768 px before sending.
- Prompts include only the offending element HTML + ≤ 2 levels of parent context + nearby text.
- Disk cache keyed by hash(prompt+image hash+model) in `backend/.llm_cache/`; env `LLM_OFFLINE=1` serves cache/fixtures only (demo safety).
- Every fix carries `ai_generated` and `confidence`. UI shows "AI-generated, review before use".
- Report the AI's own cost honestly: number of calls and tokens in the scan summary.

### 8.3 Patcher
- Fetch original HTML (from scan) and same-origin assets; write to `patched/{scan_id}/` with `<base href>` avoided where possible (rewrite relative URLs instead). Apply DOM transforms with lxml, CSS edits by appended stylesheet `greenaccess-patch.css`, images optimized into `patched/{scan_id}/assets/`.
- Serve at `/patched/{scan_id}/index.html` with CSP: `default-src 'self' data:; script-src 'self'; ...` (patched demo pages keep their own scripts only if same-origin).
- Re-scan patched URL with the same pipeline → `after`.
- Zip = patched HTML, CSS patch, optimized images, `CHANGES.md` (list of applied and skipped fixes).
- Dev/demo: patched URL host allowed via `ALLOWED_LOCAL_HOSTS`.

## 9. Scoring (fixed formulas; change only with approval)

### 9.1 Accessibility (0–100)
`score = max(0, 100 − Σ penalty_rule)`.
Per unique violated rule: `penalty = base[impact] × min(1 + log10(nodes), 2)`
`base = {critical: 10, serious: 7, moderate: 3, minor: 1}`.
Keyboard trap adds a penalty of 10. Incomplete/needs-review items are not scored.
Round to integer.

### 9.2 Carbon (0–100)
Bands on `grams_per_view` (Website Carbon rating style; **verify thresholds against websitecarbon.com's published ratings at Phase 1**, store in `scoring/constants.py`):
`A+ ≤0.095, A ≤0.186, B ≤0.341, C ≤0.493, D ≤0.656, E ≤0.846, F >0.846`.
Map to score ranges with linear interpolation inside each band: A+ 95–100, A 85–94, B 70–84, C 55–69, D 40–54, E 25–39, F 24→0 (0 at 3.0 g).
Green host: +3, capped at 100.

### 9.3 Combined
`combined = round(w_a × a11y + w_c × carbon)`, default 0.5/0.5, UI toggle (0.3–0.7).

Every score has a "How is this calculated?" popover linking to the formula and listing the top contributing items.

## 10. Trade-off engine

`tradeoffs/rules.json` array. Each rule:
```
{ "id", "type": "synergy|tension", "title",
  "detector": "<name in registry>", "params": {},
  "a11y_effect": "text", "carbon_effect": {"bytes_fn": "<name>", "note": "text"},
  "fix_id": "<fix kind>", "explanation_template": "..." }
```
Detectors reuse scanner outputs; no extra page loads. The LLM only rewrites `explanation_template` fill-ins if `LLM_OFFLINE` is off; the engine works fully without it.

Synergies: text_in_image, autoplay_media, no_reduced_motion, eager_below_fold, third_party_widgets, div_soup_widgets (custom clickable divs with role=button + heavy JS: detect `div[onclick]` / `role=button` on non-button).
Tensions: dark_mode, captions_bytes (when video lacks captions: quantify caption file ≈ few KB vs video bytes), lazy_above_fold, high_res_zoom, font_subsetting.
Each finding card shows: A11y impact, Carbon impact (± bytes and grams), Type badge, Recommended fix (cheapest satisfying both).

## 11. Demo site: "Daily Herald" (`demo-site/`)

Static site served on `:8081`, deterministic, ~2.0–2.4 MB on first load. Planted defects in `demo-site/DEFECTS.md` (source of truth for tests):
- Hero autoplay muted looping video (~1 MB, generated by `scripts/make_demo_assets.py`; falls back to a committed file if ffmpeg missing).
- Two banner images with text baked in (PNG/JPEG, 150–250 KB each), long alt or none.
- 8 large JPEGs (natural 3000 px, rendered 400 px), no width/height, all `loading` eager, several below fold, no alt on ≥ 5.
- 4 fake tracker scripts served from a second local host (`localhost:8082`, counted as third party) plus one chat widget with an unlabeled close button.
- Contact/newsletter form with unlabeled inputs; low-contrast grey-on-white text (< 3:1); `html` without `lang`.
- Heading order skipped (h1 → h4); clickable `div`s instead of buttons.
- CSS animations with no `prefers-reduced-motion`.
- 4 web font files including unused weights.
- A modal-like widget that traps Tab.
- Uncompressed CSS/JS (server option `--no-compress`).
Targets: before ≈ A11y 30–45, carbon grade E/F; after ≥ 85 a11y, carbon ≥ 75, bytes down ≥ 70%. If targets are not met, adjust the site's planted defects or fix coverage, never the scoring formulas.

## 12. API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | |
| GET | `/api/demo` | returns demo URL |
| POST | `/api/scans` | body `ScanRequest` → `{scan_id}`; rate limit 10/min/IP |
| GET | `/api/scans/{id}/events` | SSE: `step` (name, status, ms), `done`, `error` |
| GET | `/api/scans/{id}` | full `Scan` |
| POST | `/api/scans/{id}/fixes` | generate fixes → `[Fix]` |
| POST | `/api/scans/{id}/patch` | body `{accepted_fix_ids}` → runs patch + re-scan (SSE on same events stream) |
| GET | `/api/scans/{id}/patch.zip` | |
| GET | `/api/scans/{id}/screenshot?state=before\|after` | PNG |
| GET | `/api/badge/{id}.svg` | |
| GET | `/api/history?host=` | list + trend |
| GET | `/patched/{scan_id}/...` | static |

Error format: `{error: {code, message}}`. Codes: `URL_BLOCKED`, `TIMEOUT`, `PAGE_TOO_LARGE`, `NAV_FAILED`, `LLM_UNAVAILABLE`, `PATCH_FAILED`.

SSE step names: `validate, load, a11y, keyboard, aria, carbon, green, score, tradeoffs, persist` (scan), `fixes, patch, rescan` (fix pipeline).

## 13. UI specification

> **Presentation note (agreed change, 2026-09-29).** The results UI is told as one
> continuous scrolling narrative rather than as a tabbed dashboard. The six tabs below
> (Overview, Accessibility, Carbon, Trade-offs, Fixes, Simulate) become **chapters** of
> the same page over the same `ScanResult`, and the tab bar becomes a sticky chapter
> nav of in-page links. This changes arrangement only: §5 data models, §9 scoring and
> §12 API are untouched, every screen below still exists, and the accessibility rules
> in this section apply to the narrative unchanged — no scroll-jacking, every chapter
> a real section with a real heading, full content with JavaScript disabled, and all
> scroll-driven motion neutralised under `prefers-reduced-motion`.

**Design language**: calm, editorial, trustworthy.
- Tokens: `--bg #FAF8F3`, `--surface #FFFFFF`, `--ink #1B2A22`, `--ink-muted #4A5A50`, `--forest #1F5D3A`, `--forest-strong #164A2E`, `--leaf #E3F0E6`, `--amber-fill #F2B84B`, `--amber-text #7A4E00`, `--danger #A4262C`, `--border #DDD8CC`. Check every text/background pair for ≥ 4.5:1 (≥ 3:1 for large text and UI borders).
- Type: headlines Fraunces, UI Inter, both self-hosted via `@fontsource-variable/*`, latin subset, `font-display: swap`, system fallbacks. Type scale 14/16/20/28/40/56.
- Spacing 4-pt scale, radius 12, soft shadows, 1px borders. Dark theme via `prefers-color-scheme`, background `#121212`, ink `#E8E8E8`.
- Motion: gauge sweep 700 ms ease-out, tab underline slide, before/after count-up; **all disabled under `prefers-reduced-motion`**.
- Focus: 3px `--forest` ring with 2px offset on every interactive element. Skip link. Landmarks. All controls keyboard operable. Live region announces scan progress and score changes.

**Screens**
1. **Landing** — headline "How inclusive and how green is your website?", URL field, primary "Scan", secondary "Try the Daily Herald demo", self-audit badge, one-line methodology note ("Automated checks; carbon values are estimates").
2. **Scanning** — vertical step list from SSE with tick / spinner / time per step, cancel button, screenshot preview appears when available.
3. **Dashboard** — header with URL, scan time, "Estimate" chips. Three gauges (A11y, Carbon with grade letter, Combined) with "How is this calculated?" popovers, g CO₂/view, green host chip. Tabs: **Overview, Accessibility, Carbon, Trade-offs, Fixes, Simulate**. Sticky action bar: weights toggle, "Generate fixes" → "Apply fixes & re-scan", Before/After switch.
   - Overview: top 5 issues, biggest carbon contributors (stacked bar by type), synergy highlights.
   - Accessibility: grouped by impact, expandable nodes with highlighted snippet, keyboard-crawl card.
   - Carbon: bytes-by-type bar, third-party list, image table with issue chips, autoplay list.
   - Trade-offs: finding cards (A11y impact / Carbon impact / Type badge / Recommended fix).
   - Fixes: list with kind, AI badge, confidence, toggle accept; opens **Fix drawer** with side-by-side diff and copy button.
   - Simulate: left = aria tree (before/after toggle), right = screenshot with filter buttons (None, Protanopia, Deuteranopia, Tritanopia).
4. **After state** — gauges animate to new values, delta chips (+47, −1.7 MB, −23 violations), before/after screenshot slider (keyboard operable with arrow keys), download zip, "Get badge".
5. **Impact panel** — visitors slider (100 → 10M, log scale) + numeric input; outputs kg CO₂/yr before and after, km driven, phone charges; conversion assumptions in tooltip (constants file, sourced).
6. **Badge modal** — live SVG preview (grade + g/view), copy embed snippet, note that the badge is a snapshot.
7. **History** — table by domain/date, sparkline of combined score, open past scan.
8. **Empty/error states** for every screen; skeleton loaders; friendly `URL_BLOCKED` explanation.

## 14. Scope control and cut order

Cut from the bottom first:
1. Tier 3 items (never start)
2. History/trends
3. Color-blindness filters
4. Badge
5. Weight toggle
6. text_in_image and third_party_remove auto-fix (keep detection + suggestion)
Never cut: real re-scan, trade-off engine, SSRF guard, self-audit, demo fallback.

## 15. Testing strategy

- Unit: scoring, carbon (vs CO2.js fixtures), SSRF (table of good/bad URLs incl. decimal/hex IPs, DNS rebinding simulation, redirects), detectors on HTML fixtures, contrast algorithm, patcher transforms.
- Integration: scan the demo site and assert against `DEFECTS.md` (ranges, not exact bytes).
- E2E (Playwright): landing → demo scan → dashboard → generate fixes → apply → after state → download zip.
- Self-audit: `make dogfood` scans the built frontend; expects a11y 100 (axe) and passes keyboard crawl.

## 16. Demo safety

- `backend/app/fixtures/demo_scan_before.json`, `demo_scan_after.json`, screenshots, and LLM cache committed and clearly labelled "cached run of <date>".
- `DEMO_FALLBACK=1` returns cached results if the live scan fails; UI shows a small "cached result" chip (honesty).
- Backup screen recording stored in `docs/demo.mp4` (not in git if large).

## 17. Environment variables

`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `LLM_OFFLINE`, `ALLOWED_LOCAL_HOSTS` (comma list), `MAX_CONCURRENT_SCANS=2`, `SCAN_TIMEOUT_S=90`, `NAV_TIMEOUT_S=30`, `MAX_PAGE_BYTES=26214400`, `DATABASE_URL=sqlite:///./greenaccess.db`, `DEMO_FALLBACK`, `PUBLIC_BASE_URL`.
