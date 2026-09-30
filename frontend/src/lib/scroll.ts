/**
 * Scroll observation for the narrative scenes.
 *
 * Single responsibility: report where an element is relative to the viewport,
 * without ever taking control of scrolling away from the reader.
 *
 * Three rules hold everything here together:
 *
 * 1. Nothing scroll-jacks. These hooks observe; they never call `scrollTo`,
 *    never swallow wheel events, and never pin the reader in place.
 * 2. Every default is the revealed state. If `IntersectionObserver` is missing,
 *    or an effect has not run yet, `inView` is true and progress is 1 — so a
 *    reader whose JavaScript failed sees the finished page, not a blank one.
 * 3. A reveal is one-way by default. Content that flickers out again as the
 *    reader scrolls back up is worse than content that simply stays.
 */

import { useEffect, useRef, useState } from 'react'

function hasObserver(): boolean {
  return typeof window !== 'undefined' && typeof window.IntersectionObserver === 'function'
}

export interface InViewOptions {
  /** Fraction of the element that must be visible to count. Default 0.25. */
  threshold?: number
  /** Margin around the viewport, as a CSS margin string. */
  rootMargin?: string
  /** Keep reporting false once it leaves again. Default false (one-way). */
  repeat?: boolean
}

/**
 * Whether the referenced element has come into view.
 *
 * Returns a ref to attach and the current state. Used to trigger a reveal, not
 * to decide whether to render: the content is in the DOM either way, so it is
 * always reachable by a screen reader and by find-in-page.
 */
export function useInView<T extends Element>(
  options: InViewOptions = {},
): [React.RefObject<T>, boolean] {
  const { threshold = 0.25, rootMargin = '0px', repeat = false } = options
  const ref = useRef<T>(null)
  const [inView, setInView] = useState(() => !hasObserver())

  useEffect(() => {
    const element = ref.current
    if (element === null || !hasObserver()) {
      setInView(true)
      return
    }

    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0]
        if (entry === undefined) return
        if (entry.isIntersecting) {
          setInView(true)
          if (!repeat) {
            observer.disconnect()
          }
        } else if (repeat) {
          setInView(false)
        }
      },
      { threshold, rootMargin },
    )

    observer.observe(element)
    return () => observer.disconnect()
  }, [threshold, rootMargin, repeat])

  return [ref, inView]
}

/**
 * How far the reader has scrolled through an element, from 0 to 1.
 *
 * 0 is the moment its top reaches the bottom of the viewport; 1 is the moment
 * its bottom reaches the top. This is what drives a sticky figure that changes
 * as the prose beside it scrolls past.
 *
 * Measured against `scroll` on a passive listener and recomputed inside
 * `requestAnimationFrame`, so it never forces layout mid-scroll.
 */
export function useScrollProgress<T extends Element>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null)
  const [progress, setProgress] = useState(1)

  useEffect(() => {
    const element = ref.current
    if (element === null || typeof window === 'undefined') {
      return
    }

    let frame = 0

    const measure = (): void => {
      frame = 0
      const rect = element.getBoundingClientRect()
      const viewport = window.innerHeight
      // Total distance over which the element travels through the viewport.
      const span = rect.height + viewport
      if (span <= 0) {
        setProgress(1)
        return
      }
      const travelled = viewport - rect.top
      const next = travelled / span
      setProgress(next < 0 ? 0 : next > 1 ? 1 : next)
    }

    const onScroll = (): void => {
      if (frame === 0) {
        frame = window.requestAnimationFrame(measure)
      }
    }

    measure()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll, { passive: true })
    return () => {
      if (frame !== 0) {
        window.cancelAnimationFrame(frame)
      }
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [])

  return [ref, progress]
}

/**
 * Which step of a scrolly sequence is active, from a list of step elements.
 *
 * The active step is the last one whose top has passed the trigger line, set at
 * 60% of the viewport height. That keeps the figure changing slightly before
 * the matching prose reaches the middle of the screen, which reads as the
 * figure responding to the text rather than lagging behind it.
 */
export function useActiveStep(count: number): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null)
  const [active, setActive] = useState(0)

  useEffect(() => {
    const container = ref.current
    if (container === null || typeof window === 'undefined' || count === 0) {
      return
    }

    let frame = 0

    const measure = (): void => {
      frame = 0
      const steps = container.querySelectorAll('[data-scrolly-step]')
      const line = window.innerHeight * 0.6
      let next = 0
      steps.forEach((step, index) => {
        if (step.getBoundingClientRect().top <= line) {
          next = index
        }
      })
      setActive(next)
    }

    const onScroll = (): void => {
      if (frame === 0) {
        frame = window.requestAnimationFrame(measure)
      }
    }

    measure()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll, { passive: true })
    return () => {
      if (frame !== 0) {
        window.cancelAnimationFrame(frame)
      }
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [count])

  return [ref, active]
}

/**
 * Drive scroll animation through a CSS custom property.
 *
 * This is the engine behind every scroll-linked visual in the narrative, and it
 * deliberately does not use React state. Setting state on scroll would re-render
 * a whole chapter on every frame; writing one custom property on one element
 * lets the style engine do the rest, and descendants animate by reading the
 * variable in `calc()`. A scene with two hundred moving parts still costs one
 * property write per frame.
 *
 * The variable is a unitless 0-1 number: 0 when the element's top edge reaches
 * the bottom of the viewport, 1 when its bottom edge reaches the top.
 *
 * Under reduced motion the variable is pinned to its resting value and no
 * listener is attached, so every scroll-linked effect resolves to its finished
 * state and stays there.
 */
export function useScrollVar<T extends HTMLElement>(
  name = '--p',
  options: { reduced?: boolean; restingValue?: number } = {},
): React.RefObject<T> {
  const { reduced = false, restingValue = 1 } = options
  const ref = useRef<T>(null)

  useEffect(() => {
    const element = ref.current
    if (element === null || typeof window === 'undefined') {
      return
    }

    if (reduced) {
      element.style.setProperty(name, String(restingValue))
      return
    }

    let frame = 0

    const measure = (): void => {
      frame = 0
      const rect = element.getBoundingClientRect()
      const viewport = window.innerHeight
      const span = rect.height + viewport
      const progress = span <= 0 ? 1 : (viewport - rect.top) / span
      const clamped = progress < 0 ? 0 : progress > 1 ? 1 : progress
      element.style.setProperty(name, clamped.toFixed(4))
    }

    const onScroll = (): void => {
      if (frame === 0) {
        frame = window.requestAnimationFrame(measure)
      }
    }

    measure()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll, { passive: true })
    return () => {
      if (frame !== 0) {
        window.cancelAnimationFrame(frame)
      }
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [name, reduced, restingValue])

  return ref
}

/**
 * How much of the gap to the target is closed in one second's worth of easing.
 *
 * This is the time constant of an exponential approach, in seconds: after
 * `SMOOTH_TAU` the remaining distance is down to 1/e. Small enough that the
 * figure never feels detached from the wheel, large enough to absorb the coarse
 * discrete deltas a mouse wheel and a trackpad's fling actually deliver.
 */
const SMOOTH_TAU = 0.085

/** Below this, the eased value has arrived and the loop can stop. */
const SETTLED = 0.0004

/**
 * The same, measured while an element is pinned by `position: sticky`.
 *
 * A sticky figure does not move, so its own rect cannot say how far the reader
 * has travelled. The progress that matters is the *container's*: 0 when its top
 * reaches the top of the viewport (the figure has just pinned) and 1 when its
 * bottom does (the figure is about to unpin). That is the figure's whole life
 * on screen, which is exactly the range a pinned scene animates over.
 *
 * The raw value is then eased rather than written straight through, and that is
 * the difference between a scrollytell that glides and one that stutters. A
 * mouse wheel does not deliver continuous motion: it delivers a step every few
 * frames, so a figure driven by the raw position jumps in the same steps. Here
 * the published value chases the true position exponentially, which turns those
 * steps into a continuous ramp and keeps a fling from arriving all at once.
 *
 * The easing is frame-rate independent — the per-frame factor is derived from
 * the measured delta, not assumed — so the scene moves at the same speed on a
 * 60 Hz panel, a 120 Hz one, and a throttled CPU.
 *
 * Crucially this eases the *reported* position; it never touches the reader's
 * scrolling. The page still scrolls natively, at its own speed, exactly as far
 * as the reader asked. Nothing here calls `scrollTo` or swallows an event.
 *
 * The loop runs only while the eased value is still catching up, so a still
 * page costs nothing: it settles a few frames after the last scroll event and
 * cancels itself.
 */
export function useStickyProgress<T extends HTMLElement>(
  name = '--p',
  options: { reduced?: boolean; restingValue?: number; smooth?: number } = {},
): React.RefObject<T> {
  const { reduced = false, restingValue = 1, smooth = SMOOTH_TAU } = options
  const ref = useRef<T>(null)

  useEffect(() => {
    const element = ref.current
    if (element === null || typeof window === 'undefined') {
      return
    }

    if (reduced) {
      element.style.setProperty(name, String(restingValue))
      return
    }

    let frame = 0
    let previous = 0
    let eased = 0

    /** The true position: 0 as the figure pins, 1 as it unpins. */
    const target = (): number => {
      const rect = element.getBoundingClientRect()
      // Distance the container travels while the figure stays pinned.
      const travel = rect.height - window.innerHeight
      const progress = travel <= 0 ? 1 : -rect.top / travel
      return progress < 0 ? 0 : progress > 1 ? 1 : progress
    }

    const publish = (value: number): void => {
      element.style.setProperty(name, value.toFixed(4))
    }

    const tick = (now: number): void => {
      const goal = target()

      // The first frame of a run has no previous timestamp to difference
      // against, so assume one frame at 60 Hz rather than easing by zero.
      const delta = previous === 0 ? 1 / 60 : (now - previous) / 1000
      previous = now

      // Cap the step. A backgrounded tab resumes with a delta of several
      // seconds, and without this the scene would snap on the frame it returns.
      const step = delta > 0.05 ? 0.05 : delta
      eased += (goal - eased) * (1 - Math.exp(-step / smooth))

      const remaining = goal - eased
      if (remaining > SETTLED || remaining < -SETTLED) {
        publish(eased)
        frame = window.requestAnimationFrame(tick)
        return
      }

      // Arrived. Land exactly on the target so nothing rests a fraction short
      // of its finished state, and stop burning frames until the next scroll.
      eased = goal
      publish(eased)
      frame = 0
      previous = 0
    }

    const start = (): void => {
      if (frame === 0) {
        previous = 0
        frame = window.requestAnimationFrame(tick)
      }
    }

    // The first paint must be correct, not eased in from zero: a reader who
    // loads the page half way down the chapter should see it half way through.
    eased = target()
    publish(eased)

    window.addEventListener('scroll', start, { passive: true })
    window.addEventListener('resize', start, { passive: true })
    return () => {
      if (frame !== 0) {
        window.cancelAnimationFrame(frame)
      }
      window.removeEventListener('scroll', start)
      window.removeEventListener('resize', start)
    }
  }, [name, reduced, restingValue, smooth])

  return ref
}
