/**
 * Structural accessibility guarantees for the landing screen (MASTERSPEC §13).
 *
 * These assert the things the spec names explicitly -- skip link, landmarks, a
 * single h1, an ordered heading structure and a live region -- so a refactor
 * cannot quietly drop them.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Landing } from './Landing'

describe('Landing', () => {
  it('renders exactly one h1', () => {
    render(<Landing />)
    const headings = screen.getAllByRole('heading', { level: 1 })
    expect(headings).toHaveLength(1)
    expect(headings[0]).toHaveTextContent('How inclusive and how green is your website?')
  })

  it('has a skip link that targets the main landmark', () => {
    render(<Landing />)
    const skipLink = screen.getByRole('link', { name: /skip to main content/i })
    expect(skipLink).toHaveAttribute('href', '#main')
    expect(document.querySelector('#main')).not.toBeNull()
  })

  it('exposes banner, main and contentinfo landmarks', () => {
    render(<Landing />)
    expect(screen.getByRole('banner')).toBeInTheDocument()
    expect(screen.getByRole('main')).toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toBeInTheDocument()
  })

  it('gives the navigation landmark an accessible name', () => {
    render(<Landing />)
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument()
  })

  it('labels the URL input and wires up its hint', () => {
    render(<Landing />)
    const input = screen.getByLabelText('Website URL')
    expect(input).toBeInTheDocument()
    expect(input).toHaveAccessibleDescription(/public http and https addresses only/i)
  })

  it('renders a polite live region for scan progress', () => {
    render(<Landing />)
    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-live', 'polite')
  })

  it('does not skip heading levels', () => {
    render(<Landing />)
    const levels = screen
      .getAllByRole('heading')
      .map((heading) => Number(heading.tagName.slice(1)))

    expect(levels[0]).toBe(1)
    levels.forEach((level, index) => {
      if (index === 0) return
      const previous = levels[index - 1] ?? 1
      // A heading may go deeper by at most one level at a time.
      expect(level).toBeLessThanOrEqual(previous + 1)
    })
  })

  it('states that carbon figures are estimates', () => {
    render(<Landing />)
    expect(screen.getByText(/carbon values are estimates/i)).toBeInTheDocument()
  })
})
