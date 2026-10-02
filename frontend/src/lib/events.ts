/**
 * Subscription to a scan's server-sent event stream (MASTERSPEC §12).
 *
 * Single responsibility: turn `GET /api/scans/{id}/events` into typed callbacks
 * and hand back one function that closes the stream.
 *
 * Two details of the backend shape this module:
 *
 * - The stream replays from the start, so a subscriber that opens a moment
 *   after `POST /scans` still sees `validate`. Nothing here needs to race.
 * - The fix pipeline reopens the same stream for its `fixes`, `patch` and
 *   `rescan` steps. `POST /patch` returns `events_after`; passing it as `after`
 *   follows only the new phase. A browser `EventSource` cannot set
 *   `Last-Event-ID` itself, which is why the query parameter exists.
 */

import {
  CACHED_PATCH_STEPS,
  CACHED_SCAN_ID,
  CACHED_SCAN_STEPS,
  CACHED_STEP_DELAY_MS,
  isCachedDemo,
} from './cachedDemo'
import type { ApiError, DoneEvent, StepEvent } from './types'

/** What a subscriber wants to be told. Every handler is optional. */
export interface ScanStreamHandlers {
  onStep?: (event: StepEvent) => void
  /** The phase finished. The caller re-fetches the scan for the result. */
  onDone?: (event: DoneEvent) => void
  /** The phase failed, with the spec's error envelope payload. */
  onError?: (error: ApiError) => void
}

/** Closes the stream. Safe to call more than once. */
export type Unsubscribe = () => void

/**
 * Parse one event's `data`, or return null if it is not what the spec promises.
 *
 * A malformed frame is dropped rather than thrown: an exception inside an
 * `EventSource` listener would leave the stream open with no way to recover.
 */
function parse<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

/**
 * Follow a scan's progress.
 *
 * `after` skips every event up to and including that id — pass the
 * `events_after` returned by `POST /patch` to follow just the fix phase.
 */
export function subscribeToScan(
  scanId: string,
  handlers: ScanStreamHandlers,
  after?: number,
): Unsubscribe {
  if (isCachedDemo()) {
    return replayRecordedSteps(handlers, after)
  }
  const query = after !== undefined && after > 0 ? `?after=${after}` : ''
  const source = new EventSource(`/api/scans/${encodeURIComponent(scanId)}/events${query}`)

  let closed = false
  const close = (): void => {
    if (!closed) {
      closed = true
      source.close()
    }
  }

  source.addEventListener('step', (event) => {
    const step = parse<StepEvent>((event as MessageEvent<string>).data)
    if (step) {
      handlers.onStep?.(step)
    }
  })

  source.addEventListener('done', (event) => {
    const done = parse<DoneEvent>((event as MessageEvent<string>).data)
    // The stream ends after a terminal event; hold the socket open and the
    // browser will reconnect to a finished scan forever.
    close()
    if (done) {
      handlers.onDone?.(done)
    }
  })

  source.addEventListener('error', (event) => {
    // Two different things arrive on this name. A server-sent `error` event has
    // a data payload carrying the API envelope. A transport failure is an empty
    // Event with no data, and EventSource retries those on its own, so it is
    // not reported as a scan failure.
    const raw = (event as Partial<MessageEvent<string>>).data
    if (typeof raw !== 'string') {
      return
    }
    const failure = parse<ApiError>(raw)
    close()
    if (failure) {
      handlers.onError?.(failure)
    }
  })

  return close
}

/**
 * Cached build only: emit the recorded run's steps, then `done`.
 *
 * There is no server to stream from, so the step names the real pipeline emits
 * are replayed on a timer. They describe what a live run does; every number the
 * UI then shows comes from the recording, not from here.
 *
 * `after` selects the phase exactly as the query parameter does against a live
 * backend: unset for the scan, set once `POST /patch` has been accepted.
 */
function replayRecordedSteps(handlers: ScanStreamHandlers, after?: number): Unsubscribe {
  const steps = after !== undefined && after > 0 ? CACHED_PATCH_STEPS : CACHED_SCAN_STEPS
  let cancelled = false
  let timer: ReturnType<typeof setTimeout> | undefined

  const emit = (index: number): void => {
    if (cancelled) {
      return
    }
    if (index >= steps.length) {
      const done: DoneEvent = { scan_id: CACHED_SCAN_ID, status: 'done' }
      handlers.onDone?.(done)
      return
    }
    const step = steps[index]
    if (step !== undefined) {
      handlers.onStep?.(step)
    }
    timer = setTimeout(() => emit(index + 1), CACHED_STEP_DELAY_MS)
  }

  // Start on a timer rather than synchronously: a subscriber that renders in
  // response to the first step must have finished mounting first.
  timer = setTimeout(() => emit(0), CACHED_STEP_DELAY_MS)

  return () => {
    cancelled = true
    if (timer !== undefined) {
      clearTimeout(timer)
    }
  }
}
