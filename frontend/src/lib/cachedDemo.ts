/**
 * The cached-demo build: serve one recorded run instead of calling the API.
 *
 * Single responsibility: decide whether this build is the cached one, and if
 * so answer the handful of API calls the scan flow makes from a recording
 * bundled beside the page, so the UI works with no backend at all.
 *
 * Why this exists
 * ---------------
 * The scanner needs a real Chromium and about 2 GB of RAM, which no free host
 * that takes no credit card provides. A permanent URL therefore cannot run a
 * live scan. It can, however, show the real recorded result of a real run.
 *
 * What it is not
 * --------------
 * It is not a mock. Every number comes from `cached-demo.json`, written by
 * `scripts/build_static_demo.py` from a recording of an actual scan, and the
 * build injects a visible banner saying the page is cached. A URL other than
 * the recorded one is refused rather than answered with someone else's
 * numbers (CLAUDE.md rule 4).
 *
 * This module is inert unless the build sets `VITE_CACHED_DEMO=1`, so the
 * normal app is unaffected: `isCachedDemo()` is false and nothing here runs.
 */

import type { Scan, StepEvent } from './types'

/** The recording `build_static_demo.py` writes next to `index.html`. */
export interface CachedRecording {
  /** e.g. "Cached run of 2026-10-02 ... Not live data." */
  label: string
  recorded_at: string
  /** Always false. Present so a consumer cannot read this as live. */
  live: false
  fix_count: number
  /** The scan before any fixing: `after` and `patch` are null. */
  before: Scan
  /** The same scan after the fix loop. */
  after: Scan
}

/** The id the recording is served under; matches the backend's fallback. */
export const CACHED_SCAN_ID = 'demo'

/** Where the build writes the recording, relative to the page. */
const RECORDING_URL = 'cached-demo.json'

/**
 * Is this the cached build?
 *
 * Set at build time by `scripts/build_static_demo.py`. Read through
 * `import.meta.env` so the bundler can eliminate this whole path in a normal
 * build rather than ship a recording nobody asks for.
 */
export function isCachedDemo(): boolean {
  return import.meta.env.VITE_CACHED_DEMO === '1'
}

let pending: Promise<CachedRecording> | null = null

/** Fetch the recording once and keep it; it never changes while the page lives. */
export function loadRecording(): Promise<CachedRecording> {
  if (pending === null) {
    pending = fetch(RECORDING_URL, { headers: { Accept: 'application/json' } }).then(
      async (response) => {
        if (!response.ok) {
          throw new Error(`cached recording unavailable (${response.status})`)
        }
        return (await response.json()) as CachedRecording
      },
    )
  }
  return pending
}

/**
 * The chapter to show.
 *
 * `patched` follows the same contract as the backend fallback: the UI asks for
 * the patched chapter only once it has run the fix loop.
 */
export async function cachedScan(patched: boolean): Promise<Scan> {
  const recording = await loadRecording()
  return patched ? recording.after : recording.before
}

/** The URL the recording is of, for refusing anything else. */
export async function recordedUrl(): Promise<string> {
  return (await loadRecording()).before.url
}

/**
 * The screenshots the build copied beside the page.
 *
 * The recorded `screenshot_path` is a server path that does not exist here, so
 * the two images are served under fixed names instead.
 */
export function cachedScreenshotUrl(state: 'before' | 'after'): string {
  return `screenshots/${state}.png`
}

/**
 * The steps to replay for a phase.
 *
 * These are the step names the real pipeline emits, in the order it emits them,
 * so the progress list describes what a live run genuinely does.
 *
 * `ms` is 0 because the recording does not store per-step timings: showing a
 * made-up duration would be inventing a measurement (CLAUDE.md rule 4). Every
 * number the UI shows afterwards comes from the recording.
 */
export const CACHED_SCAN_STEPS: readonly StepEvent[] = [
  { name: 'validate', status: 'ok', ms: 0, detail: 'Checking the URL' },
  { name: 'load', status: 'ok', ms: 0, detail: 'Loading the page' },
  { name: 'a11y', status: 'ok', ms: 0, detail: 'Running automated accessibility checks' },
  { name: 'keyboard', status: 'ok', ms: 0, detail: 'Crawling with the keyboard' },
  { name: 'aria', status: 'ok', ms: 0, detail: 'Reading the accessibility tree' },
  { name: 'carbon', status: 'ok', ms: 0, detail: 'Estimating transfer and carbon' },
  { name: 'green', status: 'ok', ms: 0, detail: 'Checking green hosting' },
  { name: 'score', status: 'ok', ms: 0, detail: 'Scoring' },
  { name: 'tradeoffs', status: 'ok', ms: 0, detail: 'Finding trade-offs and synergies' },
  { name: 'persist', status: 'ok', ms: 0, detail: 'Saving the result' },
]

export const CACHED_PATCH_STEPS: readonly StepEvent[] = [
  { name: 'fixes', status: 'ok', ms: 0, detail: 'Reading the recorded fixes' },
  { name: 'patch', status: 'ok', ms: 0, detail: 'Applying them to a copy' },
  { name: 'rescan', status: 'ok', ms: 0, detail: 'Re-scanning the patched copy' },
]

/** Pause between replayed steps, so the progress list reads rather than jumps. */
export const CACHED_STEP_DELAY_MS = 350
