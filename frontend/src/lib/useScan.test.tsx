/**
 * Tests for the scan state machine.
 *
 * The sequence under test is the one MASTERSPEC §12 forces: `POST /scans`
 * returns only an id, progress arrives on SSE, and the result has to be fetched
 * once `done` is seen. The cases worth pinning are the ones a user can actually
 * reach — a second scan started over a first, a cancel, and a failure — because
 * each of them can leave a stale response racing a live one.
 *
 * jsdom has no EventSource, so one is supplied here. It doubles as the handle
 * the tests use to emit events.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useScan } from './useScan'

// --------------------------------------------------------------------------
// A fake EventSource
// --------------------------------------------------------------------------

class FakeEventSource {
  static instances: FakeEventSource[] = []

  readonly url: string
  closed = false
  private readonly listeners = new Map<string, ((event: Event) => void)[]>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    const existing = this.listeners.get(type) ?? []
    this.listeners.set(type, [...existing, listener])
  }

  close(): void {
    this.closed = true
  }

  /** Deliver one server-sent event to whoever is listening. */
  emit(type: string, data: unknown): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data) })
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event)
    }
  }

  /** A transport hiccup: an `error` event with no data. Must not fail a scan. */
  emitTransportError(): void {
    for (const listener of this.listeners.get('error') ?? []) {
      listener(new Event('error'))
    }
  }

  static latest(): FakeEventSource {
    const found = FakeEventSource.instances.at(-1)
    if (!found) throw new Error('no EventSource was opened')
    return found
  }
}

const fetchMock = vi.fn()

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** A finished scan, trimmed to the fields the hook itself touches. */
function finishedScan(id: string, url: string): unknown {
  return { id, url, host: 'example.com', status: 'done', before: {}, after: null, patch: null }
}

beforeEach(() => {
  FakeEventSource.instances = []
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('EventSource', FakeEventSource)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// --------------------------------------------------------------------------

describe('useScan', () => {
  it('starts idle', () => {
    const { result } = renderHook(() => useScan())
    expect(result.current.phase).toBe('idle')
    expect(result.current.scanId).toBeNull()
    expect(result.current.steps).toEqual([])
  })

  it('runs a scan from start to result', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ scan_id: 'scan-1' }))
      .mockResolvedValueOnce(jsonResponse(finishedScan('scan-1', 'https://example.com')))

    const { result } = renderHook(() => useScan())

    act(() => result.current.start('https://example.com'))

    // The URL is held from the moment the scan is asked for, before any
    // response arrives, because the scanning chapter needs it immediately.
    expect(result.current.url).toBe('https://example.com')

    await waitFor(() => expect(result.current.scanId).toBe('scan-1'))
    expect(FakeEventSource.latest().url).toBe('/api/scans/scan-1/events')

    act(() => {
      FakeEventSource.latest().emit('step', { name: 'load', status: 'running', ms: 0, detail: '' })
    })
    expect(result.current.phase).toBe('running')
    expect(result.current.steps).toHaveLength(1)

    // The same step reported again replaces the first entry rather than
    // appending: the UI shows one row per step, updated in place.
    act(() => {
      FakeEventSource.latest().emit('step', { name: 'load', status: 'ok', ms: 812, detail: '' })
    })
    expect(result.current.steps).toHaveLength(1)
    expect(result.current.steps[0]).toMatchObject({ status: 'ok', ms: 812 })

    act(() => {
      FakeEventSource.latest().emit('done', { scan_id: 'scan-1', status: 'done' })
    })

    await waitFor(() => expect(result.current.phase).toBe('done'))
    expect(result.current.scan).toMatchObject({ id: 'scan-1' })
    // The stream is closed on the terminal event; leaving it open would make
    // the browser reconnect to a finished scan indefinitely.
    expect(FakeEventSource.latest().closed).toBe(true)
  })

  it('keeps steps in arrival order', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ scan_id: 'scan-1' }))
    const { result } = renderHook(() => useScan())
    act(() => result.current.start('https://example.com'))
    await waitFor(() => expect(result.current.scanId).toBe('scan-1'))

    const source = FakeEventSource.latest()
    act(() => {
      source.emit('step', { name: 'validate', status: 'ok', ms: 4, detail: '' })
      source.emit('step', { name: 'load', status: 'running', ms: 0, detail: '' })
      source.emit('step', { name: 'validate', status: 'ok', ms: 4, detail: '' })
    })

    expect(result.current.steps.map((step) => step.name)).toEqual(['validate', 'load'])
  })

  it('reports a scan that failed', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ scan_id: 'scan-1' }))
    const { result } = renderHook(() => useScan())
    act(() => result.current.start('https://example.com'))
    await waitFor(() => expect(result.current.scanId).toBe('scan-1'))

    act(() => {
      FakeEventSource.latest().emit('error', { code: 'NAV_FAILED', message: 'page never loaded' })
    })

    expect(result.current.phase).toBe('error')
    expect(result.current.error).toEqual({ code: 'NAV_FAILED', message: 'page never loaded' })
  })

  it('ignores a transport error, which EventSource retries on its own', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ scan_id: 'scan-1' }))
    const { result } = renderHook(() => useScan())
    act(() => result.current.start('https://example.com'))
    await waitFor(() => expect(result.current.scanId).toBe('scan-1'))

    act(() => FakeEventSource.latest().emitTransportError())

    expect(result.current.phase).toBe('running')
    expect(result.current.error).toBeNull()
  })

  it('reports a rejected URL without opening a stream', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'URL_BLOCKED', message: 'loopback' } }, 400),
    )

    const { result } = renderHook(() => useScan())
    act(() => result.current.start('http://127.0.0.1'))

    await waitFor(() => expect(result.current.phase).toBe('error'))
    expect(result.current.error?.code).toBe('URL_BLOCKED')
    expect(FakeEventSource.instances).toHaveLength(0)
  })

  it('drops the first scan when a second is started', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ scan_id: 'scan-1' }))
      .mockResolvedValueOnce(jsonResponse({ scan_id: 'scan-2' }))

    const { result } = renderHook(() => useScan())
    act(() => result.current.start('https://first.example'))
    await waitFor(() => expect(result.current.scanId).toBe('scan-1'))
    const first = FakeEventSource.latest()

    act(() => result.current.start('https://second.example'))
    await waitFor(() => expect(result.current.scanId).toBe('scan-2'))

    expect(first.closed).toBe(true)
    expect(result.current.steps).toEqual([])

    // A late event from the abandoned scan must not reach the new one's state.
    act(() => {
      first.emit('step', { name: 'load', status: 'ok', ms: 900, detail: '' })
    })
    expect(result.current.steps).toEqual([])
  })

  it('treats a cancel as a cancellation, not a failure of the page', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ scan_id: 'scan-1' }))
      .mockResolvedValueOnce(jsonResponse({ id: 'scan-1', status: 'error' }))

    const { result } = renderHook(() => useScan())
    act(() => result.current.start('https://example.com'))
    await waitFor(() => expect(result.current.scanId).toBe('scan-1'))
    const source = FakeEventSource.latest()

    act(() => result.current.cancel())

    // The stream is dropped before the request, so the backend's own error
    // event for a cancelled scan cannot arrive and overwrite the reason.
    expect(source.closed).toBe(true)
    await waitFor(() => expect(result.current.error?.code).toBe('CANCELLED'))
  })

  it('returns to idle on reset', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ scan_id: 'scan-1' }))
    const { result } = renderHook(() => useScan())
    act(() => result.current.start('https://example.com'))
    await waitFor(() => expect(result.current.scanId).toBe('scan-1'))

    act(() => result.current.reset())

    expect(result.current.phase).toBe('idle')
    expect(result.current.scanId).toBeNull()
    expect(FakeEventSource.latest().closed).toBe(true)
  })

  it('closes the stream when the component unmounts', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ scan_id: 'scan-1' }))
    const { result, unmount } = renderHook(() => useScan())
    act(() => result.current.start('https://example.com'))
    await waitFor(() => expect(result.current.scanId).toBe('scan-1'))

    unmount()

    expect(FakeEventSource.latest().closed).toBe(true)
  })
})
