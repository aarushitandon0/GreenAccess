# Deploying GreenAccess

This is the runbook for putting GreenAccess on the public internet, using
Fly.io. It covers the shape of the deployment, the commands in order, the
configuration that matters, and the things that behave differently once hosted.

Read [What changes when it is hosted](#what-changes-when-it-is-hosted) before
demoing the hosted version to anyone. Two planted defects in the demo site
behave differently in the cloud, and it is better to know that than to discover
it mid-demo.

---

## 1. Shape of the deployment

Three Fly applications:

```
                          ┌──────────────────────────────────────┐
   visitor ──── https ───►│  greenaccess-aarushi.fly.dev         │
                          │                                      │
                          │  one container, one origin:          │
                          │    /            the built React UI   │
                          │    /api/*       FastAPI              │
                          │    /patched/*   patched copies       │
                          │                                      │
                          │  volume: /app/data                   │
                          │    greenaccess.db, screenshots/,     │
                          │    patched/, zips/, work/            │
                          └───────┬──────────────────────┬───────┘
                                  │                      │
                     scans (Chromium, through the        │ the patch re-scan
                     per-scan SSRF egress proxy)         │ visits its own
                                  │                      │ public /patched URL
                                  ▼                      ▼
        ┌──────────────────────────────────────┐  ┌─────────────────────────────┐
        │ greenaccess-aarushi-demo.fly.dev     │  │ greenaccess-aarushi.fly.dev │
        │ The Daily Herald, port 8081          │  │ /patched/{scan_id}/...      │
        └──────────────┬───────────────────────┘  └─────────────────────────────┘
                       │ the page pulls four tracker scripts
                       ▼
        ┌──────────────────────────────────────┐
        │ greenaccess-aarushi-trackers.fly.dev │
        │ fake trackers, port 8082             │
        └──────────────────────────────────────┘
```

**Why one container for the app rather than two.** `frontend/src/lib/api.ts`
calls the API at the relative base `/api`. In development Vite proxies that to
port 8000. Keeping both on one origin in production keeps that base true, which
means no CORS pre-flight on every call, no second certificate, and an SSE stream
that no cross-origin proxy can buffer. The root `Dockerfile` builds the
frontend in a Node stage and copies `dist/` into the Playwright image, where
`app/api/spa.py` serves it.

**Why the demo is two more apps.** The Daily Herald needs its tracker scripts to
come from a different host, or planted defect `THIRD-PARTY-01` has nothing to
find. Both are the same image from `demo-site/`, run with different arguments.

---

## 2. Prerequisites

- A Fly.io account and `flyctl` installed and logged in (`fly auth login`).
- **A card on the Fly account.** `fly apps create` refuses outright with
  `We need your payment information to continue!` until billing is set up, so
  this blocks the very first command in section 3, before any build happens.
  Add it at `https://fly.io/dashboard/<org>/billing`. The three apps here fit
  well inside Fly's smallest paid tier, and the two demo apps scale to zero.
- Docker running locally, or let Fly build remotely with `--remote-only`.
- The demo's generated media built locally: **`make assets`**. The hero video,
  article JPEGs and banners are gitignored, and `demo-site/Dockerfile` copies
  them with `COPY asset[s]` which silently copies nothing if they are absent.
  Deploy without this step and the hosted demo has no images, which quietly
  changes every carbon number it produces.

### On Windows

Two things bite on a stock Windows 11 machine. Both were hit and worked around
on 2026-09-30.

**`make` is not installed.** Git Bash and PowerShell both answer
`command not found`. GNU Make ships with msys64 as `mingw32-make`, and the
Makefile already branches on `OS=Windows_NT` to select
`backend/.venv/Scripts/python.exe`, so it works unmodified under that name:

```bash
/c/msys64/ucrt64/bin/mingw32-make assets
```

Add `C:\msys64\ucrt64\bin` to PATH to type plain `make`. Failing that, every
recipe in this Makefile is deliberately a single command, so any target can be
run directly:

```bash
backend/.venv/Scripts/python.exe scripts/make_demo_assets.py   # = make assets
backend/.venv/Scripts/python.exe scripts/demo_page_weight.py   # = make weight
```

**Smart App Control blocks `flyctl`.** `winget install Fly-io.flyctl` installs
it successfully, but the binary is unsigned and Windows then refuses to execute
it: `An Application Control policy has blocked this file`. Confirm the cause:

```powershell
Get-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy |
  Select-Object VerifiedAndReputablePolicyState    # 1 = Smart App Control on
```

Do **not** switch Smart App Control off to get around this. Once disabled it
cannot be re-enabled without reinstalling Windows, which is a steep price for a
deploy tool. Run `flyctl` from WSL instead, where the policy does not apply:

```powershell
wsl -d Ubuntu -- curl -sSL https://fly.io/install.sh | sh
wsl -d Ubuntu -- /home/$USER/.fly/bin/flyctl auth login
```

WSL mounts the repository at `/mnt/c/Users/<you>/...`, so deploys run against
these same files, and `fly auth login` opens a browser on the Windows side as
normal. Invoke `wsl` from PowerShell rather than Git Bash: Git Bash rewrites the
Linux paths inside the command and the call fails with the misleading
`C:/Program: No such file or directory`.

Running `flyctl` in Docker (`flyio/flyctl`) is the other option, and needs a
deploy token from the Fly dashboard rather than the browser login flow.

Verify before deploying anything:

```bash
make test        # 974 backend + 109 frontend
make lint        # ruff, contrast gate, tsc, eslint
make assets      # regenerates demo-site/assets/generated/
```

---

## 3. First deploy, in order

App names on Fly are globally unique, so the committed configs use the base
name `greenaccess-aarushi`; the undecorated `greenaccess` is almost certainly
taken. The commands below use those names directly.

To use a different base name, change it consistently in four places:
`fly.toml` (`app`, `PUBLIC_BASE_URL`, `PATCHED_BASE_URL`, `DEMO_URL`),
`demo-site/fly.toml` (`app`, `--tracker-base`) and
`demo-site/fly.trackers.toml` (`app`). `fly launch` would also offer to choose a
name, but it rewrites `fly.toml` from its own template and would discard the
settings in this one, so use `fly apps create` as below.

A taken name is rejected at `fly apps create`, before anything is built or
deployed, so a collision costs nothing but the retry.

### 3.1 The tracker host

```bash
fly apps create greenaccess-aarushi-trackers
fly deploy demo-site --config demo-site/fly.trackers.toml
```

The bare `demo-site` is not decoration. `flyctl deploy [WORKING_DIRECTORY]`
takes the build context as a positional argument, and `demo-site/Dockerfile`
copies `server.py`, `css`, `js` and `third-party` from the context root. Deploy
these two apps from the repository root without it and the build fails on
`"/third-party": not found`, because the context is then the repository root,
where the root `.dockerignore` also excludes `demo-site/` outright. With the
argument present, `--dockerfile` is unnecessary: it defaults to the Dockerfile
in the working directory.

### 3.2 The demo site

```bash
make assets    # do not skip this
fly apps create greenaccess-aarushi-demo
fly deploy demo-site --config demo-site/fly.toml
```

Check it serves, and that the tracker URLs were rewritten away from localhost:

```bash
curl -s https://greenaccess-aarushi-demo.fly.dev/ | grep -o 'src="[^"]*analytics.js"'
# expect: src="https://greenaccess-aarushi-trackers.fly.dev/t/analytics.js"
```

If that still says `http://localhost:8082`, the `--tracker-base` argument in
`demo-site/fly.toml` did not take effect, and the hosted demo will lose four
scripts to mixed-content blocking.

### 3.3 The application

```bash
fly apps create greenaccess-aarushi
fly volumes create greenaccess_data --app greenaccess-aarushi --region sin --size 3
fly deploy
```

The volume is not optional. Without it the scan history, every screenshot and
every patched copy are wiped on each deploy, which means a badge or a patched
URL handed to someone stops resolving the next time you ship.

### 3.4 Smoke test

```bash
curl -s https://greenaccess-aarushi.fly.dev/api/health
curl -s https://greenaccess-aarushi.fly.dev/api/demo
curl -s -o /dev/null -w '%{http_code}\n' https://greenaccess-aarushi.fly.dev/
```

Then open the site, run the demo scan end to end, generate fixes, apply them and
confirm the after state appears. That exercises the volume, the re-scan and the
SSE stream in one pass.

---

## 4. Configuration

Everything comes from the environment (`backend/app/config.py`). The values that
must be right for a hosted deployment:

| Variable | Value | Why it matters |
|---|---|---|
| `PUBLIC_BASE_URL` | `https://<app>.fly.dev` | The CORS allow-list |
| `DEMO_URL` | `https://<demo app>.fly.dev` | Where "Try the demo" points |
| `PATCHED_BASE_URL` | `https://<app>.fly.dev/patched` | The re-scan fetches the patched copy from here. Point it at localhost and every re-scan fails |
| `DATABASE_URL` | `sqlite:////app/data/greenaccess.db` | Must be on the volume. Four slashes: absolute path |
| `GREENACCESS_STATIC_DIR` | `/app/static` | Set in the Dockerfile. Without it no UI is served |
| `LLM_OFFLINE` | `1` | See below |
| `ALLOWED_LOCAL_HOSTS` | unset | Every host here is public. Never set this in production |

`ALLOWED_LOCAL_HOSTS` deserves emphasis. It is the one switch that lets the SSRF
guard through to a private address, and it exists for local development only. A
hosted deployment that sets it can be pointed at Fly's own internal network.

### The LLM is off by default

`LLM_OFFLINE=1` in `fly.toml`. A public deployment with a live key lets any
visitor spend your Anthropic credits: fix generation is one POST away and there
is no authentication. The deterministic fixes (contrast, lazy loading, image
compression, autoplay, reduced motion, form labels, lang, link and button names)
all work with it off, which is most of the fix set.

To turn it on deliberately:

```bash
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly secrets set LLM_OFFLINE=0
```

Consider lowering the rate limit first (`SCANS_PER_MINUTE` in
`backend/app/api/ratelimit.py`, currently 10 per minute per IP).

---

## 5. What changes when it is hosted

Three behaviours differ between `make dev` and the cloud. None is a bug, all are
worth knowing before a demo.

### 5.1 The tracker scripts are no longer third party

MASTERSPEC §6.2 defines third party as "the registrable domain differs from the
page's". `fly.dev` is **not** in the public suffix list bundled with
`tldextract`, so:

```
greenaccess-aarushi-demo.fly.dev      -> registrable domain "fly.dev"
greenaccess-aarushi-trackers.fly.dev  -> registrable domain "fly.dev"
```

Both resolve to the same registrable domain, so on the hosted demo the scanner
does not count those four scripts as third party. Planted defect
`THIRD-PARTY-01` goes undetected: about 10 KB of attributed savings and one
synergy card disappear. Everything else is unaffected, and the local demo is
completely unaffected, because hosts with no registrable domain are compared by
`host:port` instead.

To fix it properly, put the two demo apps on two different registrable domains:

```bash
fly certs add dailyherald.example      --app greenaccess-aarushi-demo
fly certs add herald-trackers.example  --app greenaccess-aarushi-trackers
```

then set `DEMO_URL` to `https://dailyherald.example` and `--tracker-base` in
`demo-site/fly.toml` to `https://herald-trackers.example`.

### 5.2 The demo's tracker URLs are rewritten

`demo-site/index.html` hardcodes `http://localhost:8082`, which is correct
locally and useless anywhere else: over HTTPS those scripts are blocked as mixed
content and never load. `server.py --tracker-base https://host` substitutes that
literal as the page is served. The default is the literal itself, so a local run
is byte-for-byte unchanged and the integration tests keep asserting real
behaviour.

### 5.3 Cold starts on the demo apps

Both demo apps have `min_machines_running = 0` and stop when idle, so the first
scan after a quiet period pays a second or two of start-up inside the 30 second
navigation budget. The application itself has `min_machines_running = 1` and
`auto_stop_machines = false`, deliberately: a scan is a long request and its SSE
stream is longer, and stopping the machine underneath one would drop it.

---

## 6. Operating it

```bash
fly logs --app greenaccess-aarushi
fly status --app greenaccess-aarushi
fly ssh console --app greenaccess-aarushi
fly machine restart <id> --app greenaccess-aarushi
```

The volume holds everything the app writes. To see how full it is:

```bash
fly ssh console --app greenaccess-aarushi -C "du -sh /app/data/*"
```

Screenshots and patched copies accumulate, one set per scan, and nothing prunes
them. `make clean` does this locally; in the cloud, delete under `/app/data` or
extend the volume (`fly volumes extend`). A full volume makes scans fail at the
`persist` step.

**Scaling has a hard limit worth knowing.** The app runs a single Uvicorn worker
on purpose: the SSE hub keeps each scan's subscribers in memory, SQLite has one
writer, and `MAX_CONCURRENT_SCANS` caps scanning inside the process. Running two
machines or two workers breaks all three, because a visitor's SSE stream can
land on a machine that is not running their scan. Scale up, not out, until that
state moves somewhere shared.

---

## 7. Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

- **backend**: ruff, ruff format, the WCAG contrast gate, and pytest, inside the
  same pinned Playwright image the deployment uses.
- **frontend**: `tsc --noEmit`, eslint, vitest and a production build.
- **image**: builds the deployment `Dockerfile` (does not push), so a broken
  Dockerfile is caught before a deploy rather than by one.

There is deliberately no automatic deploy step. Add one only when you want a
push to main to reach the public internet without a human in between.

---

## 8. Other hosts

Nothing here is Fly-specific except `fly.toml` and the commands. The image is an
ordinary container that needs:

- roughly 2 GB of memory (Chromium on a heavy page; 512 MB will not boot it)
- a persistent volume at `/app/data`
- one instance, not several, for the reason in section 6
- long-lived HTTP responses, for SSE

That runs on Render, Railway, a VPS with `docker compose`, or anything else that
can do those four things. Serverless platforms cannot: Playwright needs a real
browser and a writable filesystem, and SSE needs a connection that outlives a
function invocation.

---

## 9. Free hosting, with no credit card

Fly requires a card before `fly apps create` will run at all, and so does every
other host that could actually run this image. That is not an oversight on their
part: section 8's four requirements put this application above every no-card free
tier that exists.

| Host | Why it does not work |
|---|---|
| Render free | 512 MB of memory; Chromium will not boot |
| Railway, Koyeb, Northflank, Zeabur | card required |
| Cloud Run, Oracle Always Free | usage is free, but the account needs a card |
| PythonAnywhere free | no Docker, and outbound traffic is whitelist-only, so scanning cannot work |
| Vercel, Netlify, GitHub Pages | static or serverless only: no browser, no long-lived SSE |
| Back4App free containers | 256 MB |
| Heroku, Glitch, Deta | free tiers discontinued |

So the free option is not to host the app elsewhere, but to run the production
image here and publish it through a Cloudflare quick tunnel, which is free, needs
no account and no card, and issues a real HTTPS URL.

```bash
make public        # builds if needed, opens the tunnel, prints the URL
make public-down   # stop the stack (Ctrl-C on `make public` does this too)
```

`scripts/public_tunnel.py` starts the tunnel *before* the stack, on purpose. The
URL cloudflared issues has to become `PATCHED_BASE_URL`, because that value is
handed to the visitor's browser in the After chapter, and the container's own
loopback is not reachable from outside. `docker-compose.public.yml` runs the
production image, so the UI and the API are one origin, which is what a single
tunnel can serve; `docker-compose.yml` cannot be used here because its Vite dev
server is a second origin on :5173.

The stack listens on host port 8100, not 8000, so `make dev` can keep running
alongside it. Override with `PUBLIC_PORT`.

### What this gives up, and what it gains

It gives up permanence: the URL works only while `make public` is running, and a
quick tunnel gets a new random hostname each time. For a judged demo or a link
shared during a call that is usually enough; for a URL in a submission that
someone opens next week, it is not.

It gains two things over the Fly deployment.

**Third-party detection actually works.** On `fly.dev` both demo apps share one
registrable domain, so defect `THIRD-PARTY-01` goes undetected (see
`demo-site/fly.trackers.toml`). Here the demo hosts are bare Docker service
names with no registrable domain, so the scanner compares them by `host:port`,
finds them different, and attributes the tracker bytes correctly. Measured on
2026-09-30: 10,289 third-party bytes found, and 10 trade-off findings.

**Storage persists.** The `public-data` volume survives restarts, so patched URLs
and scan history keep resolving. Free hosts' ephemeral disks would not.

### Verified through the tunnel

The full loop was run end to end against the public URL on 2026-09-30, not just
the health endpoint:

| | Before | After |
|---|---|---|
| Accessibility | 0 | 66 |
| Carbon | 38 (grade E) | 91 (grade A) |
| Combined | 19 | 79 |
| Transfer | 2,327,050 bytes over 22 requests | 424,374 bytes |

52 fixes generated, 36 applied automatically and 16 reported as manual, matching
the fix-kind counts in the README. The patched page returned 200 when fetched
from outside, so the After chapter's link works for a visitor.

One honest caveat about that after figure. Cloudflare compresses responses it
proxies, and the re-scan fetches the patched copy through the tunnel, so it
measured 424,374 bytes where the same patch measured 454,881 over the container's
loopback. Both are real measurements of a real URL; the tunnel one includes a CDN
in the path and so flatters the carbon score by about a point. If you are quoting
a number, quote the loopback one, or say which path it came through.

### Security of a public URL

Anyone with the URL can start scans, rate limited to 10 per minute per IP. Two
things are worth knowing:

- `ALLOWED_LOCAL_HOSTS` here names only `demo-site:8081`,
  `demo-third-party:8082` and `localhost:8000`. Those resolve inside the
  container's own network namespace, so `localhost` is the container, **not your
  machine**. A visitor cannot use the scanner to reach your host's services.
- `LLM_OFFLINE` stays `1`. Setting a key on a public URL lets any visitor spend
  your Anthropic credits, since fix generation needs no authentication.

Windows note: `cloudflared` is a signed binary, so Smart App Control permits it,
unlike `flyctl`. See the Windows subsection of section 2.
