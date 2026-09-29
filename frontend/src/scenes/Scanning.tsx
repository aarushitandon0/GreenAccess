/**
 * Chapter two: the scan, as it happens.
 *
 * Single responsibility: show the pipeline's progress honestly — what has run,
 * what is running, and how long each step took.
 *
 * This is MASTERSPEC §13 screen 2. The steps arrive on the SSE stream and are
 * shown in arrival order; nothing is predicted or filled in ahead of time, so a
 * step the backend skipped is shown as skipped rather than silently ticked.
 */

import { formatDuration, stepLabel } from '../lib/format'
import type { StepEvent } from '../lib/types'

export interface ScanningProps {
  /** The URL being scanned, for the heading. */
  url: string
  /** Steps so far, one entry per step name. */
  steps: StepEvent[]
  /** True while the pipeline is still running. */
  running: boolean
  onCancel: () => void
}

/** A short status word for each step state, used as text beside the mark. */
const STATUS_TEXT: Record<StepEvent['status'], string> = {
  running: 'running',
  ok: 'done',
  error: 'failed',
  skipped: 'skipped',
}

export function Scanning({ url, steps, running, onCancel }: ScanningProps): JSX.Element {
  const finished = steps.filter((step) => step.status !== 'running').length

  return (
    <section className="scene scene--scanning" aria-labelledby="scanning-title">
      <div className="container scanning">
        <header className="scene__header">
          <h2 className="scene__title" id="scanning-title">
            Reading the page
          </h2>
          <p className="scene__lede">
            A real Chromium browser is loading <span className="scanning__url">{url}</span>,
            scrolling it to the bottom to trigger anything lazy, and recording every byte it
            asks for.
          </p>
        </header>

        {/*
          One live region for the whole phase, announcing completed steps only.
          Announcing every `running` transition as well would double the
          messages and interrupt a screen-reader user mid-sentence.
        */}
        <p className="visually-hidden" role="status" aria-live="polite">
          {finished === 0
            ? 'Scan started.'
            : `${finished} of ${steps.length} steps complete. ${
                steps[steps.length - 1] ? stepLabel(steps[steps.length - 1]!.name) : ''
              }`}
        </p>

        <ol className="steps">
          {steps.map((step) => (
            <li className="steps__item" key={step.name} data-status={step.status}>
              <span className="steps__mark" aria-hidden="true" />
              <span className="steps__label">{stepLabel(step.name)}</span>
              <span className="steps__status">
                {/*
                  The status word is text, not only a coloured mark: the mark is
                  decorative and colour alone never carries the state.
                */}
                {STATUS_TEXT[step.status]}
                {step.status !== 'running' && step.ms > 0 ? (
                  <span className="steps__time"> · {formatDuration(step.ms)}</span>
                ) : null}
              </span>
              {step.detail ? <span className="steps__detail">{step.detail}</span> : null}
            </li>
          ))}
        </ol>

        {running ? (
          <button className="button button--secondary" type="button" onClick={onCancel}>
            Cancel scan
          </button>
        ) : null}
      </div>
    </section>
  )
}
