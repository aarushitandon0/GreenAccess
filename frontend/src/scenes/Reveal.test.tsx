/**
 * The verdict chapter, rendered from a real recorded scan.
 *
 * The fixture is a cached run of the Daily Herald from
 * `backend/app/fixtures/`, so these assertions are against numbers the
 * pipeline actually produced rather than numbers invented for a test.
 *
 * What is pinned here is the honesty of the chapter: the scores it shows are
 * the scores the backend computed, carbon is labelled an estimate, a failed
 * green-hosting lookup reads as unknown rather than as "not green", and the
 * animated figure is not the one assistive technology reads.
 */

import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Reveal } from './Reveal'
import { beforeScan } from '../test/fixtures'
import type { Scan, ScanResult } from '../lib/types'

const scan: Scan = beforeScan()

/**
 * Run the chapter under `prefers-reduced-motion: reduce`.
 *
 * That is the honest default for a test environment — jsdom has no
 * IntersectionObserver, so every scene reports itself in view at once and the
 * count-up would otherwise be caught mid-sweep. It also asserts the rule
 * MASTERSPEC §13 sets: with motion reduced, the final figure is rendered
 * immediately rather than animated towards.
 */
beforeEach(() => {
  vi.stubGlobal(
    'matchMedia',
    (query: string): MediaQueryList =>
      ({
        matches: query.includes('prefers-reduced-motion'),
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function result(): ScanResult {
  const before = scan.before
  if (before === null) {
    throw new Error('the recorded fixture has no before result')
  }
  return before
}

function renderReveal(override?: Partial<ScanResult>): ScanResult {
  const data = { ...result(), ...override }
  render(<Reveal scanId={scan.id} url={scan.url} result={data} />)
  return data
}

describe('Reveal', () => {
  it('shows each score exactly as the backend computed it', () => {
    const data = renderReveal()

    // The spoken reading is the assertion target: the big number is aria-hidden
    // because it animates, and a live-updating figure would be read repeatedly.
    expect(screen.getByText(`${data.scores.a11y} out of 100`)).toBeInTheDocument()
    expect(screen.getByText(`${data.scores.carbon} out of 100`)).toBeInTheDocument()
    expect(screen.getByText(`${data.scores.combined} out of 100`)).toBeInTheDocument()
  })

  it('names the carbon grade', () => {
    const data = renderReveal()
    expect(screen.getByText(`(grade ${data.scores.carbon_grade})`)).toBeInTheDocument()
  })

  it('labels carbon as an estimate', () => {
    renderReveal()
    expect(screen.getAllByText('Estimate').length).toBeGreaterThan(0)
    expect(screen.getByText(/CO₂ per view \(estimate\)/i)).toBeInTheDocument()
  })

  it('never states a score without the breakdown behind it', () => {
    const data = renderReveal()
    if (data.scores.breakdown === null) {
      return
    }
    // Asserted on the <summary> text rather than a role: the disclosure is a
    // native <details>, which jsdom does not expose as a named group.
    expect(screen.getByText(/How is Accessibility calculated\?/i)).toBeInTheDocument()
    expect(screen.getByText(/How is Carbon calculated\?/i)).toBeInTheDocument()
    expect(screen.getByText(/How is Combined calculated\?/i)).toBeInTheDocument()

    // The formula behind each score is shown, not just the number.
    expect(screen.getByText(data.scores.breakdown.a11y.formula)).toBeInTheDocument()
  })

  it('reports an unavailable green lookup as unknown, not as not-green', () => {
    renderReveal({
      green: { host: 'example.com', green: false, hosted_by: null, source: 'unavailable' },
    })
    expect(screen.getByText(/unknown/i)).toBeInTheDocument()
    expect(screen.queryByText('Not listed as green')).not.toBeInTheDocument()
  })

  it('reports a genuinely non-green host as not listed', () => {
    renderReveal({
      green: { host: 'example.com', green: false, hosted_by: null, source: 'greenweb' },
    })
    expect(screen.getByText('Not listed as green')).toBeInTheDocument()
  })

  it('credits the host when the lookup found one', () => {
    renderReveal({
      green: { host: 'example.com', green: true, hosted_by: 'Some Host', source: 'greenweb' },
    })
    expect(screen.getByText(/Yes — Some Host/)).toBeInTheDocument()
  })

  it('gives the screenshot a real alternative text', () => {
    renderReveal()
    const image = screen.getByRole('img', { name: new RegExp(scan.url, 'i') })
    expect(image).toHaveAttribute('alt', expect.stringContaining('screenshot'))
    // A full-page capture is heavy and sits well below the fold.
    expect(image).toHaveAttribute('loading', 'lazy')
  })

  it('renders the final figure immediately under reduced motion', () => {
    const data = renderReveal()
    const gauges = screen.getAllByRole('figure')
    expect(gauges[0]).toBeDefined()
    // Not zero, and not part-way through a sweep: the score itself.
    expect(within(gauges[0]!).getByText(String(data.scores.a11y), { selector: '.gauge__value' }))
      .toBeInTheDocument()
  })

  it('hides the animated number from assistive technology', () => {
    const data = renderReveal()
    const gauges = screen.getAllByRole('figure')
    const first = gauges[0]
    expect(first).toBeDefined()
    const animated = within(first!).getByText(String(data.scores.a11y), {
      selector: '.gauge__value',
    })
    expect(animated).toHaveAttribute('aria-hidden', 'true')
  })

  it('states the page weight and request count', () => {
    const data = renderReveal()
    expect(screen.getByText('Page weight')).toBeInTheDocument()
    expect(screen.getByText(String(data.carbon.request_count))).toBeInTheDocument()
  })
})
