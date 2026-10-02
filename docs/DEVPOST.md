## Inspiration

Accessibility and carbon are audited by different people, with different tools, on different days, and the two reports never end up in the same conversation. We kept noticing that this separation is an accident of tooling, not a fact about websites. Both audits are looking at the same bytes.

A 1 MB autoplaying hero video is a carbon problem and a vestibular problem at the same time. A banner image with the headline baked into it is a carbon problem, because text weighs kilobytes and an image weighs hundreds of kilobytes, and an accessibility problem, because that text cannot be selected, translated, resized or read out by a screen reader. Eight 3000 pixel JPEGs rendered at 400 pixels waste transfer and push the content a reader actually wants further down the queue. Fix one, and you have fixed the other for free.

The conflicts are just as interesting, and nobody quantifies them. Captions are required for deaf and hard of hearing users and they add a file to the page. A dark theme cuts energy on OLED screens and can quietly destroy text contrast. Lazy loading saves bytes and delays the content the reader is already looking at. Font subsetting saves bytes and can drop the glyphs some readers need. These are real tensions, and a tool that only measures one axis will confidently recommend the wrong thing.

The second thing that pushed us was simpler. Almost every audit tool stops at advice. It tells you what is wrong, sometimes it estimates what fixing it would save, and then it leaves. Nobody proves the fix worked. "Estimated improvement" is a guess produced by the same code that produced the complaint, so it can drift from reality forever without anyone noticing.

So we set the bar deliberately high for ourselves: measure both dimensions in one pass, say explicitly which fixes are wins on both axes and which are genuine conflicts, generate the fix, apply it to a real copy of the page, serve that copy, and then run the entire scan pipeline again against it. Every "after" number in GreenAccess is measured. There is no prediction code path anywhere in the repository.

## What it does

You paste a URL, or press "Try the Daily Herald demo". GreenAccess loads the page in a real headless Chromium, runs a ten step pipeline, and streams each step to the browser over Server Sent Events, so you watch actual work rather than a fake progress bar.

**The ten steps:** `validate`, `load`, `a11y`, `keyboard`, `aria`, `carbon`, `green`, `score`, `tradeoffs`, `persist`.

**What it measures**

- **Accessibility** through a vendored, version pinned axe-core run with the `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` and `best-practice` tags, keeping each violation's rule id, impact, help text, help URL, tags, and every failing node with its selector, HTML snippet and failure summary. Items axe marks `incomplete` are counted and reported but never scored, because "needs review" is not a violation.
- **Keyboard traps**, which axe does not detect, through our own Tab crawl. Focus starts on `body`, Tab is pressed up to 60 times, and after each press the crawl records the active element's tag, selector path and bounding box. A trap is declared when the same cycle of at most six elements repeats for at least three full cycles while focusable elements still exist outside that cycle. The crawl also records interactive elements that are never reached, and elements that take focus with no visible focus indicator, by comparing computed `outline` and `box-shadow` between the focused and blurred states and flagging only unambiguous absence.
- **The screen reader view**, as a Playwright `aria_snapshot()` of `body`, stored as text, which is the closest honest approximation of what a screen reader is actually handed.
- **Carbon**, from per request Chrome DevTools Protocol data. Transfer size is the sum of `encodedDataLength`, which is bytes on the wire and not decoded size. Decoded size is kept separately so uncompressed text resources can be caught. Requests are classified by CDP `resourceType` into html, css, js, img, font, media and other, and "third party" means a different registrable domain from the page's own.
- **Green hosting**, through a Green Web Foundation lookup with a 5 second timeout. A failure is reported as `source: "unavailable"` and shown as "unknown", never silently treated as green.

**What it explains**

Eleven detectors run over the scanner output: `oversized_image`, `legacy_format`, `no_dimensions`, `eager_below_fold`, `text_in_image_suspected`, `autoplay_media`, `third_party_scripts`, `font_bloat`, `uncompressed_text`, `no_reduced_motion` and `no_color_scheme`, plus two accessibility only findings, `div_soup_widgets` and `video_no_captions`. Each returns findings and an estimated byte saving, and every saving is labelled an estimate rather than dressed up as a measurement.

The trade-off engine then matches those findings against `tradeoffs/rules.json`. Each rule names a detector from a registry, an accessibility effect, a carbon effect with a byte function, a recommended fix kind and an explanation template. It runs entirely without an LLM, it costs no extra page loads because every detector reads scanner output that already exists, and it produces two kinds of card:

- **Synergies**, where one change helps both: `text_in_image`, `autoplay_media`, `no_reduced_motion`, `eager_below_fold`, `third_party_widgets`, `div_soup_widgets`.
- **Tensions**, where the two dimensions genuinely disagree: `captions_bytes`, `dark_mode`, `lazy_above_fold`, `high_res_zoom`, `font_subsetting`.

Every card reports the accessibility impact, the carbon impact in both bytes and grams, a synergy or tension badge, the evidence as selectors or URLs, and the cheapest fix that satisfies both dimensions. On the demo site the engine finds 6 synergies and 4 tensions.

**What it fixes**

Twenty fix kinds are generated. On the demo site that produces 52 fixes: 36 apply automatically and 16 are reported as needing manual review, with the reason stated for each one. Applied automatically in the measured run: 10 `image_compress`, 9 `lazy_load`, 4 `form_label`, 4 `third_party_remove`, 3 `contrast`, and one each of `html_lang`, `link_name`, `button_name`, `heading_order`, `autoplay_video` and `reduced_motion`. Deferred to manual review: 7 `img_alt`, 2 `text_in_image`, and one each of `region`, `focus_visible`, `keyboard_trap`, `div_soup_widgets`, `font_bloat`, `uncompressed_text` and `dark_mode_tokens`.

You select which fixes you accept. The patcher applies them to a real copy of the page, serves it at `/patched/{scan_id}/index.html` as static files under a restrictive Content Security Policy, and then re-scans that URL through the identical ten step pipeline. You get a downloadable zip containing the patched HTML, the CSS patch, the optimised images and a `CHANGES.md` listing every fix applied and every fix skipped with its reason. There is also an embeddable SVG badge endpoint, a per host scan history endpoint, a `before` and `after` screenshot endpoint, and a CLI that runs the whole scan without any server at all.

**Verified results on the bundled demo site**, produced on 2026-09-30 by running the real pipeline twice, once before any fix and once against the patched copy the tool generated itself:

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

Engine versions behind those numbers: Playwright 1.63.0, axe-core 4.13.0, Sustainable Web Design model v3, and `@tgwf/co2` 0.19.0 as the parity reference.

**What it refuses to claim.** The tool says "automated checks" and "issues detected". It never says "WCAG compliant", and it never calls a page "accessible" as a guarantee, because automated testing catches a minority of accessibility problems and cannot certify anything. Carbon values are labelled estimates in the API through `is_estimate: true`, in the CLI output and in the UI. Keyboard traps are attributed to our own Tab crawl and not to axe. Detector byte savings are labelled estimates; the only measured byte figures are the CDP transfer sizes. AI output is labelled "AI-generated, review before use" and carries a confidence value.

## How we built it

### Stack

Backend: Python 3.12, FastAPI 0.141.1, Uvicorn 0.54.0, sse-starlette 3.5.0, pydantic 2.13.5, Playwright 1.63.0 driving Chromium, a vendored axe-core 4.13.0 with its version recorded in `axe-version.json`, lxml 6.1.3, Pillow 12.3.0, SQLModel 0.0.47 over SQLite, httpx 0.28.1, tldextract 5.3.2, and the official Anthropic SDK 1.9.0. Tooling: pytest 9.1.1, pytest-asyncio, respx and ruff 0.16.9.

Frontend: React 18.3.1 with TypeScript 5.9.3 in strict mode, Vite 8.3.1, Vitest 5.0.2, Testing Library, eslint with `eslint-plugin-jsx-a11y`, and plain CSS with design tokens. No UI framework, no CSS framework, no charting library. Two variable fonts, self hosted.

Every version is pinned exactly, and the Playwright pin is kept in lockstep with the Docker base image tag so the browser in CI is the browser the scores were measured with.

### Architecture

```
React 18 + TypeScript + Vite :5173
        |  HTTP JSON  +  Server Sent Events
        v
FastAPI :8000
  api/        routing, SSE fan-out, rate limit 10/min/IP, 2 scan concurrency cap, error envelope
  security/   ssrf.py, redirects.py, egress_proxy.py, fetch.py
  scanner/    browser.py, network.py, a11y.py, keyboard.py, aria.py, pipeline.py
  carbon/     swd.py, constants.py, detectors.py, green.py
  scoring/    a11y.py, carbon.py, combined.py, constants.py, rounding.py
  tradeoffs/  rules.json, detectors.py, engine.py
  llm/        client.py, prompts.py, schemas.py, context.py, cache.py, vision.py
  patcher/    plan.py, fixgen.py, dom.py, transforms.py, contrast.py, images.py,
              allowlist.py, build.py, rescan.py
  db/         SQLModel tables and repository
```

Two pipelines, and the second one reuses the first:

```
SCAN   validate -> load -> a11y -> keyboard -> aria -> carbon -> green -> score -> tradeoffs -> persist

FIX    generate fixes -> operator selects -> patch -> serve patched copy -> re-scan (the SAME pipeline) -> "after"
```

That reuse is the architectural point of the whole project. Because the after numbers come out of the identical ten steps pointed at a real URL serving real patched bytes, a fix that does not actually work shows up as a score that does not actually move. There is no second implementation to drift.

### Loading a page honestly

A fresh Chromium context per scan, viewport 1366x768, cache disabled, a user agent that identifies `GreenAccessBot`, and a CDP session with `Network` enabled. The page is navigated, allowed to reach network idle with a 15 second cap, then scrolled one viewport at a time with a 250 ms pause per step for up to 30 steps so lazy loaded content actually loads, then returned to the top for a full page screenshot. The load aborts if cumulative transfer passes 25 MB.

Budgets throughout: navigation timeout 30 s, total scan timeout 90 s, at most 2 concurrent scans, page weight abort at 25 MB, and a fresh browser context every time.

### Scoring

All three scoring functions are pure, live in `backend/app/scoring/`, and are covered by table driven unit tests. Every constant lives in `scoring/constants.py` with a provenance note and a verification status per block. No other module is allowed to hardcode a scoring number.

Accessibility, 0 to 100:

$$S_{a11y} = \max\left(0,\; 100 - \sum_{r \in \text{violated rules}} \text{base}[\text{impact}_r] \cdot \min\left(1 + \log_{10} n_r,\; 2\right)\right)$$

with \\( \text{base} = \{\text{critical}: 10,\ \text{serious}: 7,\ \text{moderate}: 3,\ \text{minor}: 1\} \\) and \\( n_r \\) the number of failing nodes for rule \\( r \\).

The penalty is charged per unique violated rule, not per failing element, and the node multiplier hits its cap of 2.0 at ten nodes. A rule broken ten times and a rule broken five hundred times cost exactly the same. That is deliberate: without the cap, one repeated rule eats the whole score and flattens every other signal. A detected keyboard trap adds a flat 10, equal to one critical rule at a single node.

Carbon, 0 to 100: grams per view is banded, then interpolated linearly inside the band. A+ up to 0.095 g scores 95 to 100, A up to 0.186 g scores 85 to 94, B up to 0.341 g scores 70 to 84, C up to 0.493 g scores 55 to 69, D up to 0.656 g scores 40 to 54, E up to 0.846 g scores 25 to 39, and F above that falls from 24 to 0, reaching 0 at 3.0 g. Verified green hosting adds 3 points, capped at 100.

Combined:

$$S_{\text{combined}} = \operatorname{round}\left(w_{a} \cdot S_{a11y} + w_{c} \cdot S_{\text{carbon}}\right)$$

with default weights 0.5 and 0.5, adjustable between 0.3 and 0.7. Every score in the UI carries a "How is this calculated?" explainer that links to the formula and lists the specific items contributing most to it.

### The carbon model

Sustainable Web Design v3, as implemented by `@tgwf/co2` 0.19.0. Every constant in `carbon/constants.py` carries the upstream file and symbol it came from, for a pinned version, because writing these from memory is how a carbon number becomes fiction.

$$\text{kWh} = \frac{\text{bytes}}{10^9} \times 0.81$$

$$g_{\text{first}} = \text{kWh}\left[\left(f_{\text{device}} + f_{\text{network}} + f_{\text{production}}\right) I_{\text{grid}} + f_{\text{datacentre}}\, I_{\text{dc}}\right]$$

with segment shares \\( f_{\text{device}} = 0.52 \\), \\( f_{\text{network}} = 0.14 \\), \\( f_{\text{datacentre}} = 0.15 \\), \\( f_{\text{production}} = 0.19 \\), a global average grid intensity \\( I_{\text{grid}} = 472.94 \\) gCO2e/kWh, and \\( I_{\text{dc}} = 50 \\) gCO2e/kWh when the host is verified green.

The reported figure is the model's per visit blend, 75 percent new visitors loading everything and 25 percent returning visitors loading 2 percent of it from cache:

$$g_{\text{view}} = 0.75\, g_{\text{first}} + 0.25\, g_{\text{return}}, \qquad g_{\text{return}} = 0.02\, g_{\text{first}}$$

First visit and return visit figures are reported separately as well, and every assumption used is exposed on the API response in `CarbonResult.assumptions` rather than buried in the implementation.

Correctness is checked by parity instead of by assertion. `scripts/gen_co2_fixtures.mjs` runs the real CO2.js library over a set of byte counts once and commits the output to `backend/tests/fixtures/co2js.json`, and `test_swd_parity.py` asserts our Python port produces the same numbers. If the port drifts from the reference implementation, that test fails.

### Detectors

One example of how an estimate is actually derived, the oversized image saving, allowing for a device pixel ratio of 2:

$$\text{saving} = \text{bytes} \cdot \max\left(0,\; 1 - \left(\frac{2\, w_{\text{rendered}}}{w_{\text{natural}}}\right)^{2}\right)$$

`legacy_format` assumes 30 percent for JPEG and 50 percent for PNG above 30 KB. `uncompressed_text` assumes roughly 70 percent of decoded size for HTML, CSS or JS served with no gzip or brotli above 2 KB. `font_bloat` triggers above 3 font files or 150 KB and estimates the total minus a 60 KB allowance. `text_in_image_suspected` fires on an image of at least 40 KB with a banner aspect ratio, wide, JPEG or PNG, with alt text longer than 25 characters, and estimates the image bytes minus about 2 KB of real text. The word "suspected" in that name is load bearing: it is a heuristic, not OCR, and it is presented to the user as a suspicion. `no_dimensions`, `no_reduced_motion` and `no_color_scheme` carry no byte saving at all, because they are layout shift, CPU and theming notes rather than transfer wins, and we were not willing to invent a number for them.

### What the LLM is allowed to do

The LLM is deliberately confined, and it is offline by default. A missing API key can never become a live call. Scanning, scoring, detection, the trade-off engine and the large majority of fixes all work with no key at all.

It contributes exactly three things: alt text for images including the decorative versus informative judgement, which is a genuine semantic decision and which sets `alt=""` for decorative images rather than inventing prose; accessible names drawn from surrounding context; and the natural language fill-ins inside trade-off explanation templates.

It is never allowed to compute a score, compute a carbon figure, or choose a colour. Contrast repair is a deterministic algorithm that adjusts lightness until the ratio reaches 4.5:1, or 3:1 for large text. That is arithmetic, and arithmetic belongs in code.

The controls around it: one structured call per batch of fixes grouped by kind, a JSON schema enforced and the response validated by pydantic with one retry on invalid JSON; vision calls limited to the six heaviest or most important images, each downscaled to at most 768 px before sending; prompts containing only the offending element's HTML, at most two levels of parent context and nearby text, with full page HTML never sent; answers cached on disk keyed by a hash of prompt, image and model; and every AI produced fix carrying `ai_generated` and a confidence value. The scan summary reports the AI's own cost honestly, as call counts and token counts.

### The patcher

The original HTML from the scan plus its same origin assets are written to a per scan working directory. Relative URLs are rewritten properly rather than papered over with a `<base href>`. DOM transforms are applied with lxml. CSS changes go into an appended `greenaccess-patch.css` instead of editing the original stylesheet, so the diff stays readable and reversible. Images are resized to twice their rendered width and converted to WebP at quality 78. Third party script removal only touches scripts on an explicit removable allow-list in `patcher/allowlist.json`.

The patched copy is served as static files, never executed server side, under a policy we verified with `curl`:

```
default-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; font-src 'self' data:; media-src 'self' data:;
frame-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none';
form-action 'none'
```

`X-Content-Type-Options: nosniff` goes with it.

### Security

Accepting an arbitrary URL from an untrusted user and loading it in a real browser is server side request forgery by construction. The defence is layered, and the layering is the entire point.

**Layer 1, `security/ssrf.py`.** Every user supplied URL passes through this before any browser or HTTP call. It rejects non HTTP(S) schemes, resolves the host, and checks every returned A and AAAA record against private, loopback, link local and cloud metadata ranges including `169.254.169.254`. Obfuscated forms are handled explicitly, including decimal and hexadecimal integer addresses such as `0x7f000001` and octal forms such as `0177.0.0.1`.

**Layer 2, `security/redirects.py`.** A URL that is safe can redirect to one that is not. This module walks the redirect chain hop by hop without a browser and validates every hop, so `POST /api/scans` can answer `URL_BLOCKED` synchronously instead of accepting the scan and failing it later over SSE. Each hop connects to the exact address validation just approved, carrying the original `Host` header and TLS server name, so a DNS answer that changes between the check and the connection cannot redirect the request. Environment proxies are ignored, because a proxy would do its own resolution and undo the pinning.

**Layer 3, `security/egress_proxy.py`.** This is the enforcement layer and it exists because of a specific, documented limitation we hit: Playwright's route handler sees requests a page starts, but Playwright does not route redirect hops. If an allowed URL answers `302 Location: http://169.254.169.254/`, Chromium follows it without ever calling the route handler. A guard that only sees the first hop is not a guard. So every scan gets its own proxy on an ephemeral loopback port and the browser is launched pointing at it. The proxy sees every connection the browser makes: first hop, every redirect hop, subresources, iframes, workers and WebSocket tunnels. It validates each with the same `validate_url`, connects to the exact validated address, and never re-resolves. Response bytes pass through untouched, so the CDP transfer sizes the carbon model reads are unaffected.

The demo site is on localhost, which the guard blocks by default. It is allowed through an explicit `ALLOWED_LOCAL_HOSTS` list, never by turning the guard off. `make scan` demonstrates the intended pattern with `--allow-local localhost:8081 --allow-local localhost:8082`.

### The demo site

`demo-site/` is The Daily Herald, a deliberately terrible but realistic newspaper page on port 8081, with a second host on 8082 serving fake trackers so third party detection has something genuine to find.

It exists for a reason we thought about carefully. A demo that scans a live public website depends on whatever that site happens to be serving that morning, which makes the before and after numbers unreproducible and the demo fragile in front of an audience. The Daily Herald makes them deterministic. Scanning a real site works and is a genuine bonus, but it is never the backbone.

Every defect is planted on purpose and catalogued in `demo-site/DEFECTS.md`, which the integration tests read as their source of truth. The catalogue includes a 1 MB autoplaying muted hero video, two banner images with text baked in, eight 3000 px JPEGs rendered at 400 px with no dimension attributes and no lazy loading, missing alt text on at least five images, four third party tracker scripts, a chat widget with a genuinely nameless close button, unlabelled form inputs, grey on white text below 3:1, `<html>` with no `lang`, a heading order that jumps from h1 to h4, clickable divs instead of buttons, CSS animations with no `prefers-reduced-motion`, four web fonts including unused weights, a modal that traps Tab, and uncompressed CSS and JS. First load is roughly 2.3 MB.

The scanner finds all of it. Measured: 9 axe rules over 43 elements, the Tab trap correctly localised to `div#promo`, 6 elements with no focus indicator, 2,327,022 bytes over 22 requests, 39 carbon findings, and carbon grade E.

### The frontend

The UI is one scrolling narrative rather than a dashboard of tabs: Opening, Scan, Progress, Verdict, Access, Carbon, Trade-offs, Fixes, After. It is built on a pinned scrollytelling system with a procedural voxel terrain, an analysis aperture, a HUD, and an ambient layer of drifting clouds, moving light and pollen motes that runs on its own loops rather than on scroll progress, so it never competes with the argument for the reader's attention.

Two decisions there are worth calling out because they came straight from what the tool measures. First, the clouds are drawn as isometric SVG geometry rather than cut from the artwork plates: the plates' own clouds are 100 pixels across, so scaling one up gives a grey box, and drawing them costs no network request at all on a product that scores transfer weight. Second, every piece of the ambient layer is `aria-hidden` and none of it animates under `prefers-reduced-motion`, because ambient movement with no meaning attached is precisely what that preference is asking to be spared.

Types do not drift: `frontend/src/lib/types.ts` is generated from `backend/app/models.py` by `make types`, and a backend test fails if the committed file is stale.

### Testing and verification

Run on 2026-09-30 on Windows 11, Python 3.12.13, Node 24.18.0:

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
built in 293ms                            # 200.54 kB JS, 63.18 kB gzipped
```

The strategy by layer: table driven unit tests over the pure scoring and carbon functions; the SSRF suite over a table of good and bad URLs including decimal and hexadecimal address forms, every redirect status code, relative `Location` resolution and a simulated DNS rebinding attack that changes the resolver's answer between validation and connection; detectors against HTML fixtures; parity tests against real CO2.js output; a full pipeline integration test that asserts every defect in `DEFECTS.md` is found, using ranges rather than exact byte counts so it is not brittle; a fix loop test that runs scan, fixes, patch and re-scan and asserts the real after state; a contrast gate that fails the build if any design token pair falls below its WCAG AA target, currently 66 pairs across three themes; and the generated types staleness test.

The single `xfail` is the honest kind. It asserts the specification's target of at least 85 accessibility after fixes, records exactly why that target is not currently reachable, and is marked `strict=True` so it will fail loudly the moment it starts passing. We did not tune the target away to make a test pass.

### Deployment

The whole thing runs in Docker on the official pinned Playwright image, and `make image` produces a production image that serves the built UI and the API from one origin. There is a Fly config, a CI workflow, and a runbook in `docs/DEPLOYMENT.md` that documents the two demo behaviours which genuinely differ in the cloud: the tracker host stops being third party unless its URLs are rewritten, and the demo apps cold start. There is also a `make public` path that gives a free public HTTPS URL with no credit card, using the production image behind a Cloudflare tunnel, with its security trade-offs written down rather than glossed over.

## Challenges we ran into

**Playwright does not route redirect hops.** This was the big one, and we only found it because we went looking. Our first SSRF guard validated the submitted URL and installed a Playwright route handler, which felt complete. It was not. If an allowed URL answers `302 Location: http://169.254.169.254/`, Chromium follows the redirect internally and the route handler is never called, so the guard sees the safe first hop and nothing else. The fix was to stop guarding at the API layer and start guarding at the transport layer: a per scan HTTP proxy on an ephemeral loopback port that the browser is launched against, which sees every single connection the browser makes and validates each one. A later fix hardened it further, to try every validated address when pinning a connection rather than only the first.

**DNS rebinding between the check and the connection.** Validating a hostname and then connecting by hostname is two separate resolutions, and an attacker controls what happens in between. Every layer now connects to the exact address that validation approved, carrying the original `Host` header and TLS server name, and environment proxies are ignored because a proxy would perform its own resolution and quietly undo all of it. There is a test that simulates exactly this attack by changing the resolver's answer mid flight.

**We could not verify the carbon bands against the source the spec named.** The specification asked for the band thresholds to be checked against websitecarbon.com's published ratings. Those pages returned HTTP 403 on 2026-09-29 and could not be read. The tempting move was to quietly claim verification. Instead the source file records that status as UNVERIFIED with its date, and the bands were verified against the pinned upstream implementation those pages describe, `@tgwf/co2` 0.19.0 and its `SWDMV3_RATINGS`, where all six thresholds match exactly. That second check is recorded as VERIFIED with its date. A test asserts the duplicate copy of the band edges in `carbon/constants.py` agrees, so the two copies cannot drift apart.

**The demo's before score landed on 0 and we left it there.** The planted defects sum to 100.3 penalty points under the published formula, and the formula clamps at zero, so the before accessibility score is 0 rather than the 30 to 45 the spec targeted. The obvious fix was to soften the scoring formula. The spec is explicit that if this target is missed, the planted defects should be adjusted and never the formula, so the formula was not touched, and the README shows the full penalty arithmetic rule by rule so anyone can check that 0 is a real result and not a scanner failure.

**Defining "keyboard trap" precisely enough to test.** axe does not detect traps, so we had to build the crawl and then decide what actually constitutes a trap rather than a loop that merely looks like one. The rule we settled on, a repeating cycle of at most six elements over at least three full cycles while focusable elements exist outside that cycle, was the result of tightening a much looser first version that produced false positives on ordinary navigation menus. Detecting a missing focus indicator had the same problem, and we ended up comparing computed `outline` and `box-shadow` between focused and blurred states and flagging only unambiguous absence, because an aggressive check here would slander perfectly good pages.

**Patching real HTML without breaking it.** Our first instinct was to inject a `<base href>` so relative URLs kept working in the patched copy. That subtly changes how the page resolves everything, including things we were not trying to touch, so we replaced it with proper URL rewriting. Editing the original stylesheet was similarly tempting and similarly wrong, so CSS changes go into an appended patch file. And a lot of fixes simply cannot be applied safely to an arbitrary page, which is why 16 of the 52 demo fixes are reported as needing manual review with a stated reason rather than attempted: converting divs to buttons can break attached handlers, adding landmarks safely needs an understanding of the page's structure, font subsetting can drop glyphs some readers need, and the demo's keyboard trap lives in JavaScript, which no fix kind edits. A tool that silently breaks a page is worse than a tool that says plainly it cannot help.

**Keeping the LLM out of the parts it would have ruined.** It is genuinely tempting to ask a model for a contrast fixed colour, and it will happily give you one. Contrast is a formula with a threshold, so a model that is right 95 percent of the time is strictly worse than arithmetic that is right every time. Drawing that line, LLM for semantic judgement only and code for everything measurable, took a rewrite of the fix generator into structured, schema validated batches with a disk cache and a strict vision budget of six images.

**Getting real numbers out of a browser.** Transfer size is a surprisingly slippery quantity. Decoded size, `Content-Length` and bytes on the wire disagree, and only one of them belongs in a carbon model. We went to CDP `encodedDataLength` for the measurement and kept decoded size separately purely so uncompressed text could be detected, and the egress proxy had to be written to pass response bytes through untouched so it could not perturb the very numbers it was protecting.

**Holding ourselves to our own standard.** Building a scrollytelling UI with heavy artwork on a product that penalises page weight and unrequested motion is an invitation to hypocrisy. The contrast gate that runs on every `make lint` and checks 66 token pairs across three themes exists to stop us shipping a colour we would have flagged on someone else's site, and the ambient motion layer is fully disabled under `prefers-reduced-motion`.

## Accomplishments that we're proud of

**The after numbers are real.** There is no prediction code path in the repository. The after state comes from re-running the identical ten step pipeline against a real URL serving real patched bytes, which means a fix that does not work cannot hide.

**Carbon down 80.4 percent and grade E to grade A on the demo**, with accessibility 0 to 66 and combined 19 to 78, all measured, all reproducible with `make demo-replay`.

**933 backend tests and 109 frontend tests passing, and a lint suite that is clean**, including parity tests against the real CO2.js library so our Python port of the Sustainable Web Design model cannot silently drift from the reference.

**A security model that survives the attacks it is supposed to survive**, including redirect hops that Playwright never surfaces, decimal and hexadecimal address obfuscation, and a simulated DNS rebinding attack, all covered by table driven tests.

**It works with a completely empty environment.** No API key is needed to scan, score, explain trade-offs or patch. The LLM is offline by default and a missing key can never become a live call.

**The trade-off engine works without any AI at all.** Findings, byte deltas and gram deltas are produced deterministically. If a key is present, the model only rewrites the fill-in language inside the explanation templates.

**We dogfood the contrast rule we enforce on others**, on 66 token pairs across three themes, as part of every lint run.

**The honest inventory.** The README has a "Project status and known gaps" section that lists what is not finished as plainly as what is, including the single largest gap, an empty offline LLM cache. It names the three known behaviours most likely to be reported as bugs and explains each one before anyone has to ask. Shipping that section felt more valuable than shipping a cleaner story.

## What we learned

**Accessibility and carbon overlap far more than they conflict, and the conflicts are worth naming out loud.** Six of our ten trade-off rules are synergies. That is the headline finding of the whole project: most of the time, the green fix and the inclusive fix are the same fix, and the only reason teams do not see that is that the two audits never sit on the same page. The four genuine tensions are worth quantifying precisely because they are the minority, and because getting them wrong hurts somebody.

**Guard at the transport layer, not at the API layer.** Any guard that a browser can route around by following a redirect is not a guard. This generalises well beyond SSRF: validate where the bytes actually leave the process.

**An estimate and a measurement need different words in the code and in the UI.** We ended up with a hard internal rule: CDP transfer sizes are measurements, everything a detector predicts is an estimate, and the two never wear the same label. `is_estimate: true` on the API response is part of that discipline, not decoration.

**Scoring design is a design problem, not an arithmetic problem.** The node multiplier cap of 2.0 exists because our first version charged per failing element and a single repeated rule wiped out the score, making every other signal invisible. Per unique rule with a capped multiplier keeps the score informative.

**Confining a model makes it more useful, not less.** Once we decided the LLM only does semantic judgement, alt text and accessible names, every other part of the system became testable, deterministic and free to run. A tool with a narrow model dependency is a tool that still works when the key is missing.

**Constants written from memory are fiction.** Every carbon and scoring constant now carries the upstream file, symbol and version it came from, plus a verification status and date. The one constant block we could not verify against the source the spec named says so, in the file, with the date and the HTTP 403.

**Saying "manual fix needed" is a feature.** Sixteen of the demo's fixes are deferred with a stated reason. Shipping those as confident automatic patches would have been easy and would have broken pages.

**A deterministic demo is worth building.** The Daily Herald took real effort, and it is the reason the before and after numbers are reproducible on any machine at any hour instead of depending on what some live site happened to be serving.

## What's next for GreenAccess

**Record the offline LLM cache.** This is the single largest gap in the project. `backend/app/fixtures/llm_cache/` currently holds only a README, because `make demo-record` needs an `ANTHROPIC_API_KEY` and has not been run. The consequence is visible in the results rather than hidden: `image-alt` stays the biggest single penalty in the after state, because nothing generated the alt text, which is exactly why the after score is 66 and not higher. Recording that cache lets `make demo-replay` reproduce the full AI assisted loop offline, for free, forever.

**Build the Simulate chapter.** The ARIA tree is already captured, stored and returned by the API, and no UI renders it yet. Pairing that with the colour blindness SVG filters turns the accessibility report from a list of violations into something you can actually experience.

**Build the impact panel.** The visitors slider that converts grams per view into kilograms of CO2e per year, kilometres driven and phone charges. The arithmetic is trivial; the point is turning 0.67 grams into a number a stakeholder feels.

**Build the history screen.** `GET /api/history` already works and returns trend data per host. Nothing consumes it yet, and a trend line over time is what turns a one off audit into a habit.

**Finish `make e2e` and `make dogfood`.** The end to end flow is currently exercised through the real API by `test_patch_integration.py`, and the self audit's contrast half already runs on every lint. Both targets should do what their names say.

**Widen what can be fixed automatically.** A JavaScript aware fix kind would let us address the demo's keyboard trap, which is currently detected correctly and deferred correctly but never fixed. Landmark and `region` fixes need structural understanding of a page. Real OCR would let `text_in_image_suspected` drop the word "suspected".

**Tune the demo without touching the formula.** Dropping two of the seven missing alt images would put the before score inside the intended 30 to 45 band. That is a demo tuning decision and we deliberately left it open rather than resolving it by weakening the scoring.

**Regional grid intensity.** The model currently uses the 472.94 gCO2e/kWh global average. Using the actual grid intensity of the hosting region, and of the visitors' regions, would make the estimate meaningfully sharper.

**The stretch tier from the spec.** GitHub pull request generation so a fix lands as a reviewable diff instead of a zip, a Chrome extension for auditing as you browse, a shareable report link and a PDF export, and multi page crawling so a whole site can be scored rather than one URL at a time.

---

## Built with

python, fastapi, uvicorn, pydantic, playwright, chrome-devtools-protocol, axe-core, lxml, pillow, sqlmodel, sqlite, httpx, react, typescript, vite, vitest, pytest, ruff, eslint, anthropic-claude, server-sent-events, docker, fly.io, cloudflare, css

## Try it out

- GitHub repository: https://github.com/aarushitandon0/GreenAccess
