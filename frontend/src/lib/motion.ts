/**
 * The reader's motion preference.
 *
 * Single responsibility: answer "may this page animate?" in one place, so that
 * no component has to remember to ask.
 *
 * MASTERSPEC §13 requires every animation — gauge sweep, tab underline,
 * before/after count-up — to be disabled under `prefers-reduced-motion`. CSS
 * handles the declarative side in base.css. This hook covers the rest: motion
 * driven from JavaScript, where a component must render the *final* state
 * immediately rather than animate towards it.
 */

import { useEffect, useState } from 'react'

const QUERY = '(prefers-reduced-motion: reduce)'

/**
 * True when the reader has asked for reduced motion.
 *
 * Defaults to `true` when the query cannot be evaluated (no `matchMedia`, as in
 * jsdom). Not animating a reader who wanted motion is a missed flourish;
 * animating a reader who asked for stillness can cause real harm, so the safe
 * default is stillness.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return true
    }
    return window.matchMedia(QUERY).matches
  })

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return
    }
    const media = window.matchMedia(QUERY)
    const update = (): void => setReduced(media.matches)
    update()
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  return reduced
}

/**
 * A number that counts up to `target` once `run` is true.
 *
 * Used for the score gauges and the before/after deltas. Under reduced motion,
 * or before the trigger, it returns `target` unchanged — the figure is never
 * withheld, only animated towards when animation is welcome.
 *
 * The easing is the same `ease-out` shape as `--duration-gauge` in tokens.css,
 * so a counter beside a sweeping gauge arrives with it rather than after it.
 */
export function useCountUp(target: number, run: boolean, durationMs = 900): number {
  const reduced = usePrefersReducedMotion()
  const animate = run && !reduced

  // Eased progress from 0 to 1, advanced only from inside requestAnimationFrame.
  // The displayed figure is derived from it rather than stored, so there is no
  // state to keep in sync when `target` or `animate` changes.
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    if (!animate || typeof window === 'undefined') {
      return
    }

    let frame = 0
    const start = window.performance.now()

    const tick = (now: number): void => {
      const elapsed = now - start
      const t = elapsed >= durationMs ? 1 : elapsed / durationMs
      // Cubic ease-out: fast departure, soft landing on the true figure.
      setProgress(1 - Math.pow(1 - t, 3))
      if (t < 1) {
        frame = window.requestAnimationFrame(tick)
      }
    }

    frame = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(frame)
  }, [animate, durationMs])

  // Not animating means the final figure, immediately: under reduced motion,
  // before the trigger, and in any environment without rAF.
  return animate ? target * progress : target
}
