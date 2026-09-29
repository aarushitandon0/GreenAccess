/**
 * Tests for the two scroll-position hooks.
 *
 * `useInView` is covered in scroll.test.tsx, which needs a real render to
 * attach the ref. These two can be exercised directly, and what matters about
 * both is their resting value: with nothing attached to measure, they must
 * report the *finished* state, so a figure driven by scroll position is never
 * stuck at its starting frame.
 */

import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { useActiveStep, useScrollProgress } from './scroll'

describe('useScrollProgress', () => {
  it('reports complete when there is no element to measure', () => {
    const { result } = renderHook(() => useScrollProgress<HTMLDivElement>())
    const [ref, progress] = result.current
    expect(ref.current).toBeNull()
    expect(progress).toBe(1)
  })
})

describe('useActiveStep', () => {
  it('starts on the first step', () => {
    const { result } = renderHook(() => useActiveStep(4))
    expect(result.current[1]).toBe(0)
  })

  it('is inert when there are no steps', () => {
    const { result } = renderHook(() => useActiveStep(0))
    expect(result.current[1]).toBe(0)
  })
})
