/**
 * The scan state machine: start a scan, follow its steps, hold its result.
 *
 * Single responsibility: own everything about one scan's lifecycle so that no
 * scene component has to know the API exists.
 *
 * The sequence is fixed by MASTERSPEC §12. `POST /scans` returns an id and
 * nothing else; progress arrives on the SSE stream; the result itself is only
 * available from `GET /scans/{id}` once `done` has been seen. So `done` is not
 * the end of the machine — it triggers the fetch that is.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiFailure, createScan, cancelScan, getScan } from './api'
import { subscribeToScan, type Unsubscribe } from './events'
import type { ApiError, Scan, StepEvent, Weights } from './types'

/** Where a scan is, from the UI's point of view. */
export type ScanPhase =
  /** Nothing has been asked for yet. */
  | 'idle'
  /** `POST /scans` is in flight; there is no id to show progress for. */
  | 'starting'
  /** The scan is running and steps are arriving. */
  | 'running'
  /** The pipeline finished; `GET /scans/{id}` is in flight. */
  | 'loading'
  /** `scan` holds a finished result. */
  | 'done'
  /** `error` says what went wrong. */
  | 'error'

export interface ScanState {
  phase: ScanPhase
  scanId: string | null
  /** The URL that was asked for, available before the result exists. */
  url: string | null
  /** Steps in arrival order, one entry per step name, latest status kept. */
  steps: StepEvent[]
  scan: Scan | null
  error: ApiError | null
}

export interface ScanController extends ScanState {
  /** Queue a scan for `url`. Replaces any scan already being followed. */
  start: (url: string, weights?: Weights) => void
  /** Ask the backend to stop a queued or running scan. */
  cancel: () => void
  /** Return to `idle`, dropping the current scan. */
  reset: () => void
  /** Replace the held scan — used after the patch phase re-scans. */
  setScan: (scan: Scan) => void
}

const INITIAL: ScanState = {
  phase: 'idle',
  scanId: null,
  url: null,
  steps: [],
  scan: null,
  error: null,
}

/**
 * Merge a step event into the list.
 *
 * The pipeline emits each step twice — `running`, then `ok`/`error`/`skipped`.
 * The UI wants one row per step that updates in place, not a growing log, so a
 * repeat of a known name replaces it and keeps its position.
 */
function mergeStep(steps: StepEvent[], incoming: StepEvent): StepEvent[] {
  const index = steps.findIndex((step) => step.name === incoming.name)
  if (index === -1) {
    return [...steps, incoming]
  }
  const next = [...steps]
  next[index] = incoming
  return next
}

/** An `ApiError` for anything thrown by the client. */
function toApiError(cause: unknown): ApiError {
  if (cause instanceof ApiFailure) {
    return { code: cause.code, message: cause.message }
  }
  return { code: 'NAV_FAILED', message: 'the scan could not be started' }
}

export function useScan(): ScanController {
  const [state, setState] = useState<ScanState>(INITIAL)

  // The live SSE subscription, and a token identifying the scan it belongs to.
  // Every async continuation checks the token before writing state: a user who
  // starts a second scan while the first is in flight must not see the first
  // one's late result land on top of the second.
  const unsubscribe = useRef<Unsubscribe | null>(null)
  const current = useRef(0)

  const teardown = useCallback(() => {
    unsubscribe.current?.()
    unsubscribe.current = null
  }, [])

  // Closing the stream on unmount matters more than usual here: an open
  // EventSource to a finished scan reconnects on a timer forever.
  useEffect(() => teardown, [teardown])

  const follow = useCallback(
    (scanId: string, token: number) => {
      unsubscribe.current = subscribeToScan(scanId, {
        onStep: (step) => {
          if (token !== current.current) return
          setState((prev) => ({ ...prev, phase: 'running', steps: mergeStep(prev.steps, step) }))
        },

        onDone: () => {
          if (token !== current.current) return
          setState((prev) => ({ ...prev, phase: 'loading' }))
          // The result lives in the database, not in the event.
          getScan(scanId)
            .then((scan) => {
              if (token !== current.current) return
              setState((prev) => ({ ...prev, phase: 'done', scan }))
            })
            .catch((cause: unknown) => {
              if (token !== current.current) return
              setState((prev) => ({ ...prev, phase: 'error', error: toApiError(cause) }))
            })
        },

        onError: (error) => {
          if (token !== current.current) return
          setState((prev) => ({ ...prev, phase: 'error', error }))
        },
      })
    },
    [],
  )

  const start = useCallback(
    (url: string, weights?: Weights) => {
      teardown()
      const token = ++current.current
      setState({ ...INITIAL, phase: 'starting', url })

      createScan(url, weights)
        .then(({ scan_id }) => {
          if (token !== current.current) return
          setState((prev) => ({ ...prev, phase: 'running', scanId: scan_id }))
          follow(scan_id, token)
        })
        .catch((cause: unknown) => {
          if (token !== current.current) return
          setState((prev) => ({ ...prev, phase: 'error', error: toApiError(cause) }))
        })
    },
    [follow, teardown],
  )

  const cancel = useCallback(() => {
    const scanId = state.scanId
    if (scanId === null) return
    const token = current.current

    // Stop listening first: the backend ends the stream with an error event for
    // a cancelled scan, and that is an outcome the user asked for, not a fault.
    teardown()
    cancelScan(scanId)
      .then((scan) => {
        if (token !== current.current) return
        setState((prev) => ({
          ...prev,
          phase: 'error',
          scan,
          error: { code: 'CANCELLED', message: 'Scan cancelled.' },
        }))
      })
      .catch((cause: unknown) => {
        if (token !== current.current) return
        setState((prev) => ({ ...prev, phase: 'error', error: toApiError(cause) }))
      })
  }, [state.scanId, teardown])

  const reset = useCallback(() => {
    teardown()
    current.current += 1
    setState(INITIAL)
  }, [teardown])

  const setScan = useCallback((scan: Scan) => {
    setState((prev) => ({ ...prev, phase: 'done', scan }))
  }, [])

  return { ...state, start, cancel, reset, setScan }
}
