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
 * The same, measured while an element is pinned by `position: sticky`.
 *
 * A sticky figure does not move, so its own rect cannot say how far the reader
 * has travelled. The progress that matters is the *container's*: 0 when its top
 * reaches the top of the viewport (the figure has just pinned) and 1 when its
 * bottom does (the figure is about to unpin). That is the figure's whole life
 * on screen, which is exactly the range a pinned scene animates over.
 */
export function useStickyProgress<T extends HTMLElement>(
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
      // Distance the container travels while the figure stays pinned.
      const travel = rect.height - window.innerHeight
      const progress = travel <= 0 ? 1 : -rect.top / travel
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
