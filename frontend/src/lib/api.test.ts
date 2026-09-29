/**
 * Tests for the API client.
 *
 * Two things matter here and are easy to get wrong: the exact request each
 * endpoint sends (the backend validates bodies strictly), and that every
 * failure path produces an `ApiFailure` carrying the spec's error code rather
 * than leaking a raw Response or a parse error.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiFailure,
  applyPatch,
  cancelScan,
  createScan,
  generateFixes,
  getDemo,
  getHistory,
  getScan,
  patchZipUrl,
  screenshotUrl,
} from './api'

const fetchMock = vi.fn()

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** The url and parsed body of the single call made. */
function lastCall(): { url: string; init: RequestInit; body: unknown } {
  const call = fetchMock.mock.calls[0]
  const url = String(call?.[0])
  const init = (call?.[1] ?? {}) as RequestInit
  const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
  return { url, init, body }
}

describe('createScan', () => {
  it('posts the url to /api/scans', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ scan_id: 'abc' }))

    await expect(createScan('https://example.com')).resolves.toEqual({ scan_id: 'abc' })

    const { url, init, body } = lastCall()
    expect(url).toBe('/api/scans')
    expect(init.method).toBe('POST')
    // Weights are omitted rather than sent as null: the field is optional and
    // the backend applies its own default.
    expect(body).toEqual({ url: 'https://example.com' })
  })

  it('includes weights when given', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ scan_id: 'abc' }))
    await createScan('https://example.com', { a11y: 0.7, carbon: 0.3 })
    expect(lastCall().body).toEqual({
      url: 'https://example.com',
      weights: { a11y: 0.7, carbon: 0.3 },
    })
  })
})

describe('error handling', () => {
  it('turns the error envelope into an ApiFailure with its code', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: 'URL_BLOCKED', message: 'loopback addresses are not scanned' } },
        { status: 400 },
      ),
    )

    const failure = await createScan('http://127.0.0.1').catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(ApiFailure)
    expect(failure).toMatchObject({
      code: 'URL_BLOCKED',
      status: 400,
      message: 'loopback addresses are not scanned',
    })
  })

  it('reads Retry-After on a rate-limited scan', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: 'RATE_LIMITED', message: 'try again shortly' } },
        { status: 429, headers: { 'Retry-After': '30', 'Content-Type': 'application/json' } },
      ),
    )

    const failure = (await createScan('https://example.com').catch(
      (error: unknown) => error,
    )) as ApiFailure

    expect(failure.code).toBe('RATE_LIMITED')
    expect(failure.retryAfter).toBe(30)
  })

  it('survives a non-JSON error body from a proxy', async () => {
    fetchMock.mockResolvedValue(
      new Response('<html>502 Bad Gateway</html>', { status: 502, statusText: 'Bad Gateway' }),
    )

    const failure = (await getScan('abc').catch((error: unknown) => error)) as ApiFailure

    expect(failure).toBeInstanceOf(ApiFailure)
    expect(failure.status).toBe(502)
    expect(failure.message).toContain('502')
  })

  it('reports an unreachable API rather than throwing a network error', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const failure = (await getDemo().catch((error: unknown) => error)) as ApiFailure

    expect(failure).toBeInstanceOf(ApiFailure)
    expect(failure.code).toBe('NAV_FAILED')
    expect(failure.status).toBe(0)
  })

  it('maps a 404 with no envelope to NOT_FOUND', async () => {
    fetchMock.mockResolvedValue(new Response('', { status: 404, statusText: 'Not Found' }))
    const failure = (await getScan('missing').catch((error: unknown) => error)) as ApiFailure
    expect(failure.code).toBe('NOT_FOUND')
  })
})

describe('the remaining endpoints', () => {
  it('reads one scan', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 'abc' }))
    await getScan('abc')
    expect(lastCall().url).toBe('/api/scans/abc')
  })

  it('cancels a scan', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 'abc' }))
    await cancelScan('abc')
    expect(lastCall().url).toBe('/api/scans/abc/cancel')
    expect(lastCall().init.method).toBe('POST')
  })

  it('generates fixes', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ scan_id: 'abc', fixes: [] }))
    await generateFixes('abc')
    expect(lastCall().url).toBe('/api/scans/abc/fixes')
  })

  it('posts the accepted fix ids when applying a patch', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ scan_id: 'abc', events_after: 12 }))
    await applyPatch('abc', ['fix-1', 'fix-2'])
    expect(lastCall().url).toBe('/api/scans/abc/patch')
    expect(lastCall().body).toEqual({ accepted_fix_ids: ['fix-1', 'fix-2'] })
  })

  it('filters history by host', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ host: null, scans: [], trends: {} }))
    await getHistory('example.com')
    expect(lastCall().url).toBe('/api/history?host=example.com')
  })

  it('omits the query when no host is given', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ host: null, scans: [], trends: {} }))
    await getHistory()
    expect(lastCall().url).toBe('/api/history')
  })
})

describe('resource urls', () => {
  it('builds the screenshot url for each state', () => {
    expect(screenshotUrl('abc', 'before')).toBe('/api/scans/abc/screenshot?state=before')
    expect(screenshotUrl('abc', 'after')).toBe('/api/scans/abc/screenshot?state=after')
  })

  it('builds the patch zip url', () => {
    expect(patchZipUrl('abc')).toBe('/api/scans/abc/patch.zip')
  })

  it('escapes a scan id rather than interpolating it raw', () => {
    expect(screenshotUrl('a/b', 'before')).toBe('/api/scans/a%2Fb/screenshot?state=before')
  })
})
