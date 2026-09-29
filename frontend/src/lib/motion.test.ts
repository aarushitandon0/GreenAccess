/**
 * Tests for the motion primitives.
 *
 * MASTERSPEC §13 requires every animation to be disabled under
 * `prefers-reduced-motion`. For JavaScript-driven motion that means one
 * specific thing, asserted here: the component renders the *final* value
 * immediately. A figure must never be withheld because it was not animated.
 */

import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useCountUp, usePrefersReducedMotion } from './motion'

/** Install a matchMedia that reports the given preference. */
function stubMotionPreference(reduced: boolean): void {
  vi.stubGlobal(
    'matchMedia',
    (query: string): MediaQueryList =>
      ({
        matches: reduced && query.includes('prefers-reduced-motion'),
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('usePrefersReducedMotion', () => {
  it('reports the preference when it is set', () => {
    stubMotionPreference(true)
    const { result } = renderHook(() => usePrefersReducedMotion())
    expect(result.current).toBe(true)
  })

  it('reports no preference when motion is welcome', () => {
    stubMotionPreference(false)
    const { result } = renderHook(() => usePrefersReducedMotion())
    expect(result.current).toBe(false)
  })

  it('assumes reduced motion when the query cannot be evaluated', () => {
    // Not animating someone who wanted motion costs a flourish. Animating
    // someone who asked for stillness can cause harm, so stillness is default.
    vi.stubGlobal('matchMedia', undefined)
    const { result } = renderHook(() => usePrefersReducedMotion())
    expect(result.current).toBe(true)
  })
})

describe('useCountUp', () => {
  it('returns the target immediately under reduced motion', () => {
    stubMotionPreference(true)
    const { result } = renderHook(() => useCountUp(87, true))
    expect(result.current).toBe(87)
  })

  it('returns the target before the animation is triggered', () => {
    stubMotionPreference(false)
    const { result } = renderHook(() => useCountUp(87, false))
    expect(result.current).toBe(87)
  })

  it('starts from zero and lands exactly on the target', () => {
    stubMotionPreference(false)

    // Drive requestAnimationFrame by hand so the sweep is deterministic.
    let now = 0
    const frames: FrameRequestCallback[] = []
    vi.stubGlobal('performance', { now: () => now })
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      frames.push(callback)
      return frames.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => {})

    const { result } = renderHook(() => useCountUp(100, true, 1000))

    // The first render is the start of the sweep, not the finished figure.
    expect(result.current).toBe(0)

    act(() => {
      now = 500
      frames.shift()?.(now)
    })
    expect(result.current).toBeGreaterThan(0)
    expect(result.current).toBeLessThan(100)

    act(() => {
      now = 1000
      frames.shift()?.(now)
    })
    // Easing must resolve to the true figure, not to 99.7.
    expect(result.current).toBe(100)
  })
})
