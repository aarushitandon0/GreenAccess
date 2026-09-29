/**
 * Tests for the scroll primitives.
 *
 * The rule these exist to protect: a reveal is an enhancement, never a gate.
 * If `IntersectionObserver` is missing, or an effect has not run, the content
 * must already be in its revealed state — otherwise a reader whose JavaScript
 * failed gets a blank page from a tool that audits accessibility.
 *
 * `useInView` is exercised through a component rather than `renderHook`,
 * because the behaviour under test depends on the ref being attached while the
 * effect runs, which is something only a real render does.
 */

import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useInView } from './scroll'

afterEach(() => {
  vi.unstubAllGlobals()
})

/** A component whose only output is whether it considers itself in view. */
function Probe(): JSX.Element {
  const [ref, inView] = useInView<HTMLDivElement>()
  return (
    <div ref={ref} data-testid="probe">
      {inView ? 'revealed' : 'hidden'}
    </div>
  )
}

/** Capture the observer the component constructs, so a test can drive it. */
function stubObserver(): {
  trigger: (isIntersecting: boolean) => void
  observe: ReturnType<typeof vi.fn>
  disconnect: ReturnType<typeof vi.fn>
} {
  let notify: ((entries: IntersectionObserverEntry[]) => void) | null = null
  const observe = vi.fn()
  const disconnect = vi.fn()

  class FakeObserver {
    constructor(callback: (entries: IntersectionObserverEntry[]) => void) {
      notify = callback
    }
    observe = observe
    disconnect = disconnect
    unobserve = vi.fn()
    takeRecords = vi.fn()
    root = null
    rootMargin = ''
    thresholds: number[] = []
  }

  vi.stubGlobal('IntersectionObserver', FakeObserver)

  return {
    observe,
    disconnect,
    trigger: (isIntersecting: boolean) => {
      act(() => {
        notify?.([{ isIntersecting } as IntersectionObserverEntry])
      })
    },
  }
}

describe('useInView', () => {
  it('reports in-view when IntersectionObserver is unavailable', () => {
    // jsdom has no IntersectionObserver, which is exactly the case under test.
    vi.stubGlobal('IntersectionObserver', undefined)
    render(<Probe />)
    expect(screen.getByTestId('probe')).toHaveTextContent('revealed')
  })

  it('observes the element and reveals it on intersection', () => {
    const { observe, trigger } = stubObserver()

    render(<Probe />)
    expect(observe).toHaveBeenCalledWith(screen.getByTestId('probe'))
    expect(screen.getByTestId('probe')).toHaveTextContent('hidden')

    trigger(true)
    expect(screen.getByTestId('probe')).toHaveTextContent('revealed')
  })

  it('stays revealed once it has been seen', () => {
    const { trigger } = stubObserver()
    render(<Probe />)

    trigger(true)
    trigger(false)

    // A one-way reveal: content that flickers out again as the reader scrolls
    // back up is worse than content that simply stays.
    expect(screen.getByTestId('probe')).toHaveTextContent('revealed')
  })

  it('disconnects the observer when the component unmounts', () => {
    const { disconnect } = stubObserver()
    const { unmount } = render(<Probe />)
    unmount()
    expect(disconnect).toHaveBeenCalled()
  })
})
