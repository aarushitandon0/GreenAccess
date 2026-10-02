/**
 * Typed HTTP client for the GreenAccess API (MASTERSPEC §12).
 *
 * Single responsibility: turn each documented endpoint into one typed function,
 * and turn every failure into an `ApiFailure` carrying the spec's error
 * envelope, so callers never branch on raw `Response` objects.
 *
 * The SSE stream is deliberately not here: it is a long-lived subscription
 * rather than a request, and lives in `events.ts`.
 */

import {
  CACHED_SCAN_ID,
  cachedScan,
  cachedScreenshotUrl,
  isCachedDemo,
  loadRecording,
  recordedUrl,
} from './cachedDemo'
import type {
  ApiError,
  ApiErrorEnvelope,
  DemoInfo,
  ErrorCode,
  FixesResponse,
  HistoryResponse,
  PatchAccepted,
  Scan,
  ScanCreated,
  ScanRequest,
  Weights,
} from './types'

/** Requests go to the same origin; Vite proxies `/api` to the backend in dev. */
const BASE = '/api'

/** A failed API call, carrying the `{error: {code, message}}` envelope of §12. */
export class ApiFailure extends Error {
  readonly code: ErrorCode
  readonly status: number
  /** Seconds to wait, from `Retry-After`, when the code is `RATE_LIMITED`. */
  readonly retryAfter: number | null

  constructor(error: ApiError, status: number, retryAfter: number | null = null) {
    super(error.message)
    this.name = 'ApiFailure'
    this.code = error.code
    this.status = status
    this.retryAfter = retryAfter
  }
}

function isErrorEnvelope(value: unknown): value is ApiErrorEnvelope {
  if (typeof value !== 'object' || value === null || !('error' in value)) {
    return false
  }
  const { error } = value as { error: unknown }
  return (
    typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    'message' in error &&
    typeof (error as { message: unknown }).message === 'string'
  )
}

/**
 * The failure behind a non-2xx response.
 *
 * A proxy or a crash can return a non-JSON body, so a body that does not parse
 * as the documented envelope is reported under a code derived from the status
 * rather than allowed to throw a second, less informative error.
 */
async function failureFrom(response: Response): Promise<ApiFailure> {
  const header = response.headers.get('Retry-After')
  const parsed = header === null ? Number.NaN : Number.parseInt(header, 10)
  const retryAfter = Number.isFinite(parsed) ? parsed : null

  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    body = null
  }

  if (isErrorEnvelope(body)) {
    return new ApiFailure(body.error, response.status, retryAfter)
  }

  const code: ErrorCode = response.status === 404 ? 'NOT_FOUND' : 'INVALID_REQUEST'
  return new ApiFailure(
    { code, message: `${response.status} ${response.statusText || 'request failed'}` },
    response.status,
    retryAfter,
  )
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { Accept: 'application/json', ...init?.headers },
    })
  } catch {
    // A network-level failure never reached the API, so there is no envelope.
    throw new ApiFailure({ code: 'NAV_FAILED', message: 'could not reach the GreenAccess API' }, 0)
  }

  if (!response.ok) {
    throw await failureFrom(response)
  }
  return (await response.json()) as T
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// --------------------------------------------------------------------------
// Endpoints
// --------------------------------------------------------------------------

/**
 * Cached build only: which chapter of the recording `getScan` should return.
 *
 * The live API decides this from the scan's own stored state. There is no
 * server here, so the one bit of progress the flow has -- whether the fix loop
 * has been run -- is held here, set by `applyPatch` and read by `getScan`.
 */
let patchApplied = false

/** Cached build only: forget that the fix loop ran. Exported for tests. */
export function resetCachedDemoState(): void {
  patchApplied = false
}

/** `GET /api/demo` — where the Daily Herald demo site is served. */
export async function getDemo(): Promise<DemoInfo> {
  if (isCachedDemo()) {
    return { url: await recordedUrl() }
  }
  return request<DemoInfo>('/demo')
}

/**
 * `POST /api/scans` — queue a scan. Rate limited to 10/min/IP.
 *
 * In the cached build there is no scanner, so only the recorded URL is
 * answered. Returning the recording for someone else's site would be
 * presenting cached data as their result (CLAUDE.md rule 4), so anything else
 * is refused with a message that says why.
 */
export async function createScan(url: string, weights?: Weights): Promise<ScanCreated> {
  if (isCachedDemo()) {
    const recorded = await recordedUrl()
    if (url.replace(/\/$/, '') !== recorded.replace(/\/$/, '')) {
      throw new ApiFailure(
        {
          code: 'URL_BLOCKED',
          message:
            // No trailing full stop: the UI appends its own sentence after this.
            'this is a cached demo and cannot scan new sites. It replays one ' +
            `recorded run of ${recorded}, and GreenAccess must be run locally ` +
            'to scan your own URL',
        },
        403,
      )
    }
    return { scan_id: CACHED_SCAN_ID }
  }
  const body: ScanRequest = weights ? { url, weights } : { url }
  return postJson<ScanCreated>('/scans', body)
}

/** `GET /api/scans/{id}` — the whole scan, including `before`/`after`/`patch`. */
export function getScan(scanId: string): Promise<Scan> {
  if (isCachedDemo()) {
    return cachedScan(patchApplied)
  }
  return request<Scan>(`/scans/${encodeURIComponent(scanId)}`)
}

/** `POST /api/scans/{id}/cancel` — idempotent; a finished scan is unchanged. */
export function cancelScan(scanId: string): Promise<Scan> {
  if (isCachedDemo()) {
    return cachedScan(patchApplied)
  }
  return postJson<Scan>(`/scans/${encodeURIComponent(scanId)}/cancel`, {})
}

/** `POST /api/scans/{id}/fixes` — generate fixes for a finished scan. */
export async function generateFixes(scanId: string): Promise<FixesResponse> {
  if (isCachedDemo()) {
    const { patch } = (await loadRecording()).after
    if (!patch) {
      throw new ApiFailure(
        { code: 'LLM_UNAVAILABLE', message: 'the recording holds no fixes' },
        503,
      )
    }
    if (patch.ai_usage === null) {
      throw new ApiFailure(
        { code: 'LLM_UNAVAILABLE', message: 'the recording holds no AI usage record' },
        503,
      )
    }
    return { scan_id: CACHED_SCAN_ID, fixes: patch.fixes, ai_usage: patch.ai_usage }
  }
  return postJson<FixesResponse>(`/scans/${encodeURIComponent(scanId)}/fixes`, {})
}

/**
 * `POST /api/scans/{id}/patch` — apply the accepted fixes and re-scan.
 *
 * Returns `events_after`: the last event id before this phase, which the caller
 * passes to the SSE stream as `?after=` to follow only the new steps.
 */
export function applyPatch(scanId: string, acceptedFixIds: string[]): Promise<PatchAccepted> {
  if (isCachedDemo()) {
    // The patched chapter is already recorded; flip to it and let the stream
    // replay the fix phase. The non-zero `events_after` is what makes the UI
    // reopen the stream for that phase, exactly as against a live backend.
    patchApplied = true
    return Promise.resolve({ scan_id: CACHED_SCAN_ID, events_after: 7 })
  }
  return postJson<PatchAccepted>(`/scans/${encodeURIComponent(scanId)}/patch`, {
    accepted_fix_ids: acceptedFixIds,
  })
}

/** `GET /api/history` — past scans, plus a per-host trend for sparklines. */
export function getHistory(host?: string): Promise<HistoryResponse> {
  const query = host ? `?host=${encodeURIComponent(host)}` : ''
  return request<HistoryResponse>(`/history${query}`)
}

// --------------------------------------------------------------------------
// URLs for resources the browser fetches itself (<img>, download links)
// --------------------------------------------------------------------------

/** `GET /api/scans/{id}/screenshot?state=` — full-page PNG. */
export function screenshotUrl(scanId: string, state: 'before' | 'after'): string {
  if (isCachedDemo()) {
    return cachedScreenshotUrl(state)
  }
  return `${BASE}/scans/${encodeURIComponent(scanId)}/screenshot?state=${state}`
}

/** `GET /api/scans/{id}/patch.zip` — the patched site as a download. */
export function patchZipUrl(scanId: string): string {
  return `${BASE}/scans/${encodeURIComponent(scanId)}/patch.zip`
}
