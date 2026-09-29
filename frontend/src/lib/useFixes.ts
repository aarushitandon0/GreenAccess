/**
 * The fix pipeline: generate fixes, apply the accepted ones, re-scan for real.
 *
 * Single responsibility: own everything between a finished scan and an `after`
 * result.
 *
 * The shape is set by MASTERSPEC §12. `POST /fixes` answers directly.
 * `POST /patch` only queues work and returns `events_after`, the last event id
 * before this phase; the `fixes`, `patch` and `rescan` steps then arrive on the
 * *same* SSE stream as the original scan, which is why that value is passed
 * back as `after` — without it the client would replay the whole original scan.
 *
 * The `after` result is a real re-scan of the patched page. Nothing here
 * predicts it, derives it from the fixes, or adjusts the before-figures to
 * suggest an improvement.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiFailure, applyPatch, generateFixes, getScan } from './api'
import { subscribeToScan, type Unsubscribe } from './events'
import type { AiUsage, ApiError, Fix, Scan, StepEvent } from './types'

export type FixPhase =
  /** Nothing asked for yet. */
  | 'idle'
  /** `POST /fixes` is in flight. */
  | 'generating'
  /** Fixes are listed and waiting for the reader to choose. */
  | 'ready'
  /** The patch is being built and the patched page re-scanned. */
  | 'applying'
  /** The re-scan finished; the scan now carries an `after` result. */
  | 'applied'
  | 'error'

export interface FixState {
  phase: FixPhase
  fixes: Fix[]
  aiUsage: AiUsage | null
  /** Steps of the fix phase only, in arrival order. */
  steps: StepEvent[]
  error: ApiError | null
}

export interface FixController extends FixState {
  generate: (scanId: string) => void
  apply: (scanId: string, acceptedFixIds: string[]) => void
  reset: () => void
}

const INITIAL: FixState = {
  phase: 'idle',
  fixes: [],
  aiUsage: null,
  steps: [],
  error: null,
}

function toApiError(cause: unknown): ApiError {
  if (cause instanceof ApiFailure) {
    return { code: cause.code, message: cause.message }
  }
  return { code: 'PATCH_FAILED', message: 'the fix pipeline could not be reached' }
}

/** One row per step name, latest status kept in place. */
function mergeStep(steps: StepEvent[], incoming: StepEvent): StepEvent[] {
  const index = steps.findIndex((step) => step.name === incoming.name)
  if (index === -1) {
    return [...steps, incoming]
  }
  const next = [...steps]
  next[index] = incoming
  return next
}

/**
 * @param onApplied Called with the re-fetched scan once the re-scan finishes.
 *   The caller owns the scan; this hook does not hold a second copy of it.
 */
export function useFixes(onApplied: (scan: Scan) => void): FixController {
  const [state, setState] = useState<FixState>(INITIAL)

  const unsubscribe = useRef<Unsubscribe | null>(null)

  // The callback is held in a ref so that `apply` does not have to be rebuilt
  // when the caller passes a new closure, which would tear down a live stream
  // mid-patch. Written in an effect, never during render.
  const applied = useRef(onApplied)
  useEffect(() => {
    applied.current = onApplied
  }, [onApplied])

  const teardown = useCallback(() => {
    unsubscribe.current?.()
    unsubscribe.current = null
  }, [])

  useEffect(() => teardown, [teardown])

  const generate = useCallback((scanId: string) => {
    setState({ ...INITIAL, phase: 'generating' })
    generateFixes(scanId)
      .then((response) => {
        setState((prev) => ({
          ...prev,
          phase: 'ready',
          fixes: response.fixes,
          aiUsage: response.ai_usage,
        }))
      })
      .catch((cause: unknown) => {
        setState((prev) => ({ ...prev, phase: 'error', error: toApiError(cause) }))
      })
  }, [])

  const apply = useCallback(
    (scanId: string, acceptedFixIds: string[]) => {
      teardown()
      setState((prev) => ({ ...prev, phase: 'applying', steps: [], error: null }))

      applyPatch(scanId, acceptedFixIds)
        .then(({ events_after }) => {
          // Resume the shared stream past the original scan's events, so only
          // the fixes/patch/rescan steps arrive.
          unsubscribe.current = subscribeToScan(
            scanId,
            {
              onStep: (step) => {
                setState((prev) => ({ ...prev, steps: mergeStep(prev.steps, step) }))
              },
              onDone: () => {
                getScan(scanId)
                  .then((scan) => {
                    setState((prev) => ({ ...prev, phase: 'applied' }))
                    applied.current(scan)
                  })
                  .catch((cause: unknown) => {
                    setState((prev) => ({
                      ...prev,
                      phase: 'error',
                      error: toApiError(cause),
                    }))
                  })
              },
              onError: (error) => {
                setState((prev) => ({ ...prev, phase: 'error', error }))
              },
            },
            events_after,
          )
        })
        .catch((cause: unknown) => {
          setState((prev) => ({ ...prev, phase: 'error', error: toApiError(cause) }))
        })
    },
    [teardown],
  )

  const reset = useCallback(() => {
    teardown()
    setState(INITIAL)
  }, [teardown])

  return { ...state, generate, apply, reset }
}
