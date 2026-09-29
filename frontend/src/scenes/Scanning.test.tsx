/**
 * The scanning chapter.
 *
 * What is pinned here is honesty about progress: a step the backend skipped is
 * shown as skipped rather than ticked, a failed step says so, and the state of
 * each step is carried by text rather than by the colour of its mark alone
 * (WCAG 1.4.1). The cancel control only exists while there is something to
 * cancel.
 */

import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Scanning } from './Scanning'
import type { StepEvent } from '../lib/types'

function step(
  name: string,
  status: StepEvent['status'],
  ms = 0,
  detail = '',
): StepEvent {
  return { name, status, ms, detail }
}

describe('Scanning', () => {
  it('lists every step with its state in words', () => {
    render(
      <Scanning
        url="https://example.com"
        steps={[
          step('validate', 'ok', 4),
          step('load', 'running'),
          step('green', 'skipped'),
          step('a11y', 'error', 120),
        ]}
        running
        onCancel={vi.fn()}
      />,
    )

    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(4)
    expect(within(items[0]!).getByText('done')).toBeInTheDocument()
    expect(within(items[1]!).getByText('running')).toBeInTheDocument()
    expect(within(items[2]!).getByText('skipped')).toBeInTheDocument()
    expect(within(items[3]!).getByText('failed')).toBeInTheDocument()
  })

  it('describes each step in plain language rather than by its code name', () => {
    render(
      <Scanning url="https://example.com" steps={[step('a11y', 'ok', 900)]} running onCancel={vi.fn()} />,
    )
    // Scoped to the list: the same sentence also appears in the live region,
    // which announces the step that just finished.
    const item = screen.getAllByRole('listitem')[0]
    expect(within(item!).getByText(/Running the accessibility rule set/i)).toBeInTheDocument()
  })

  it('shows how long a finished step took, and not for a running one', () => {
    render(
      <Scanning
        url="https://example.com"
        steps={[step('validate', 'ok', 4), step('load', 'running', 0)]}
        running
        onCancel={vi.fn()}
      />,
    )
    expect(screen.getByText(/4 ms/)).toBeInTheDocument()
    const running = screen.getAllByRole('listitem')[1]
    expect(within(running!).queryByText(/ms|s$/)).not.toBeInTheDocument()
  })

  it('announces progress politely', () => {
    render(
      <Scanning url="https://example.com" steps={[step('validate', 'ok', 4)]} running onCancel={vi.fn()} />,
    )
    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-live', 'polite')
    expect(status).toHaveTextContent(/1 of 1 steps complete/i)
  })

  it('offers cancel only while the scan is running', () => {
    const onCancel = vi.fn()
    const { rerender } = render(
      <Scanning url="https://example.com" steps={[step('load', 'running')]} running onCancel={onCancel} />,
    )

    fireEvent.click(screen.getByRole('button', { name: /cancel scan/i }))
    expect(onCancel).toHaveBeenCalledTimes(1)

    rerender(
      <Scanning
        url="https://example.com"
        steps={[step('load', 'ok', 800)]}
        running={false}
        onCancel={onCancel}
      />,
    )
    expect(screen.queryByRole('button', { name: /cancel scan/i })).not.toBeInTheDocument()
  })

  it('names the page being scanned', () => {
    render(<Scanning url="https://example.com" steps={[]} running onCancel={vi.fn()} />)
    expect(screen.getByText('https://example.com')).toBeInTheDocument()
  })
})
