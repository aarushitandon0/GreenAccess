/**
 * Structural accessibility guarantees for the story page (MASTERSPEC §13).
 *
 * These assert the things the spec names explicitly — skip link, landmarks, a
 * single h1, an ordered heading structure, a live region — so that a refactor
 * cannot quietly drop them. A product that audits accessibility has to pass its
 * own audit, and `make dogfood` will hold this page to axe with no violations.
 *
 * They also cover the wiring the narrative depends on: the demo button waits
 * for `GET /api/demo`, and submitting the form starts a real scan.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'

const fetchMock = vi.fn()

/** jsdom has no EventSource; the scan hook opens one as soon as a scan starts. */
class StubEventSource {
  addEventListener(): void {}
  close(): void {}
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Answer `GET /api/demo`, and anything else with a 404 envelope. */
function route(url: string): Response {
  if (url === '/api/demo') {
    return jsonResponse({ url: 'http://localhost:8081' })
  }
  return jsonResponse({ error: { code: 'NOT_FOUND', message: url } }, 404)
}

beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockImplementation((url: string) => Promise.resolve(route(String(url))))
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('EventSource', StubEventSource)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** Render, and let the `GET /api/demo` effect settle. */
async function renderApp(): Promise<void> {
  render(<App />)
  await act(async () => {
    await Promise.resolve()
  })
}

describe('App', () => {
  it('renders exactly one h1', async () => {
    await renderApp()
    const headings = screen.getAllByRole('heading', { level: 1 })
    expect(headings).toHaveLength(1)
    expect(headings[0]).toHaveTextContent('Pages cost more than they look.')
  })

  it('has a skip link that targets the main landmark', async () => {
    await renderApp()
    const skipLink = screen.getByRole('link', { name: /skip to main content/i })
    expect(skipLink).toHaveAttribute('href', '#main')
    expect(document.querySelector('#main')).not.toBeNull()
  })

  it('exposes banner, main and contentinfo landmarks', async () => {
    await renderApp()
    expect(screen.getByRole('banner')).toBeInTheDocument()
    expect(screen.getByRole('main')).toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toBeInTheDocument()
  })

  it('gives the navigation landmark an accessible name', async () => {
    await renderApp()
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument()
  })

  it('labels the URL input and wires up its hint', async () => {
    await renderApp()
    const input = screen.getByLabelText('Website URL')
    expect(input).toBeInTheDocument()
    expect(input).toHaveAccessibleDescription(/public http and https addresses only/i)
  })

  it('renders an alert region for scan failures', async () => {
    await renderApp()
    // Present from first paint, and empty. Injecting a live region only when
    // it has something to say can swallow the first message.
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('')
  })

  it('does not skip heading levels', async () => {
    await renderApp()
    const levels = screen.getAllByRole('heading').map((heading) => Number(heading.tagName.slice(1)))

    expect(levels[0]).toBe(1)
    levels.forEach((level, index) => {
      if (index === 0) return
      const previous = levels[index - 1] ?? 1
      expect(level).toBeLessThanOrEqual(previous + 1)
    })
  })

  it('states that carbon figures are estimates', async () => {
    await renderApp()
    expect(screen.getAllByText(/carbon values are estimates/i).length).toBeGreaterThan(0)
  })

  it('enables the demo button only once the API says where the demo is', async () => {
    render(<App />)
    const demo = screen.getByRole('button', { name: /daily herald demo/i })
    expect(demo).toBeDisabled()
    await waitFor(() => expect(demo).toBeEnabled())
  })

  it('starts a scan for the URL that was typed', async () => {
    await renderApp()

    fireEvent.change(screen.getByLabelText('Website URL'), {
      target: { value: 'https://example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Scan' }))

    await waitFor(() => {
      const scanCall = fetchMock.mock.calls.find(([url]) => String(url) === '/api/scans')
      expect(scanCall).toBeDefined()
      expect(JSON.parse(String(scanCall?.[1]?.body))).toEqual({ url: 'https://example.com' })
    })
  })

  it('does not submit an empty URL', async () => {
    await renderApp()

    fireEvent.click(screen.getByRole('button', { name: 'Scan' }))

    expect(fetchMock.mock.calls.some(([url]) => String(url) === '/api/scans')).toBe(false)
  })

  it('shows no chapter nav until a scan has produced chapters to navigate', async () => {
    await renderApp()
    expect(screen.queryByRole('navigation', { name: 'Chapters' })).not.toBeInTheDocument()
  })

  it('lets the reader reach the scan form without scrolling the opening', async () => {
    await renderApp()
    // The opening is a story, not a toll gate: a real anchor jumps past it.
    expect(screen.getByRole('link', { name: /scan a site/i })).toHaveAttribute('href', '#begin')
    expect(document.querySelector('#begin')).not.toBeNull()
  })

  it('keeps no focusable control inside the pinned opening', async () => {
    await renderApp()
    const opening = document.querySelector('#opening')
    expect(opening).not.toBeNull()
    // Panels in a pinned chapter dim as the reader passes them. A control that
    // is invisible but still tabbable would be a real defect (WCAG 2.4.7).
    const focusable = opening!.querySelectorAll(
      'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )
    expect(focusable).toHaveLength(0)
  })
})
