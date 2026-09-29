/**
 * Chapter: the fixes, and applying them.
 *
 * Single responsibility: list what can be changed, let the reader choose, and
 * run the patch.
 *
 * Two of MASTERSPEC's accuracy rules live here. Anything the model wrote is
 * labelled "AI-generated, review before use" — not as a disclaimer in the
 * footer but on the row itself. And anything the patcher cannot do reliably is
 * listed as needing a manual fix rather than quietly dropped, so the count of
 * what was found never silently exceeds the count of what was offered.
 */

import { useMemo, useState } from 'react'

import { Scene } from '../components/Scene'
import { formatDuration, pluralise, stepLabel, truncateMiddle } from '../lib/format'
import type { AiUsage, ApiError, Fix, StepEvent } from '../lib/types'
import type { FixPhase } from '../lib/useFixes'

function AiCost({ usage }: { usage: AiUsage }): JSX.Element {
  if (usage.unavailable_reason) {
    return (
      <p className="note note--block">
        The language model was not available: {usage.unavailable_reason}. Fixes below fall back
        to values read from the page, or are marked as needing a manual fix.
      </p>
    )
  }
  return (
    <p className="note note--block">
      {/* MASTERSPEC §8.2: report the AI's own cost honestly. */}
      Model {usage.model} — {pluralise(usage.live_calls, 'live call')},{' '}
      {usage.cached_calls} from cache, {usage.vision_calls} with vision.{' '}
      {(usage.input_tokens + usage.output_tokens).toLocaleString()} tokens in total.
      {usage.offline ? ' Run offline, from the committed cache.' : ''}
    </p>
  )
}

function FixRow({
  fix,
  checked,
  disabled,
  onToggle,
}: {
  fix: Fix
  checked: boolean
  disabled: boolean
  onToggle: (id: string, next: boolean) => void
}): JSX.Element {
  return (
    <li className="fix" data-manual={fix.manual_review ? 'true' : 'false'}>
      <div className="fix__head">
        {fix.manual_review ? (
          <span className="fix__manual">Manual fix needed</span>
        ) : (
          <label className="fix__accept">
            <input
              type="checkbox"
              checked={checked}
              disabled={disabled}
              onChange={(event) => onToggle(fix.id, event.target.checked)}
            />
            <span>
              Apply
              {/* The kind gives the checkbox a unique accessible name. */}
              <span className="visually-hidden"> the {fix.kind} fix for {fix.target}</span>
            </span>
          </label>
        )}

        <span className="fix__kind">
          <code>{fix.kind}</code>
        </span>

        {fix.ai_generated ? (
          <span className="chip chip--estimate">AI-generated, review before use</span>
        ) : null}
      </div>

      <p className="fix__description">{fix.description}</p>
      <p className="fix__target">
        <code>{truncateMiddle(fix.target, 56)}</code>
      </p>

      {fix.diff.before || fix.diff.after ? (
        <details className="fix__diff">
          <summary>Show the change</summary>
          <div className="diff">
            <div className="diff__side">
              <h5>Before</h5>
              <pre>
                <code>{fix.diff.before || '(nothing)'}</code>
              </pre>
            </div>
            <div className="diff__side">
              <h5>After</h5>
              <pre>
                <code>{fix.diff.after || '(nothing)'}</code>
              </pre>
            </div>
          </div>
        </details>
      ) : null}
    </li>
  )
}

export interface FixesProps {
  phase: FixPhase
  fixes: Fix[]
  aiUsage: AiUsage | null
  steps: StepEvent[]
  error: ApiError | null
  onGenerate: () => void
  onApply: (acceptedFixIds: string[]) => void
}

export function Fixes({
  phase,
  fixes,
  aiUsage,
  steps,
  error,
  onGenerate,
  onApply,
}: FixesProps): JSX.Element {
  const automatic = useMemo(() => fixes.filter((fix) => !fix.manual_review), [fixes])
  const manual = useMemo(() => fixes.filter((fix) => fix.manual_review), [fixes])

  // Everything applicable starts accepted; the reader opts out, not in.
  const [declined, setDeclined] = useState<Set<string>>(new Set())
  const accepted = automatic.filter((fix) => !declined.has(fix.id)).map((fix) => fix.id)

  const busy = phase === 'generating' || phase === 'applying'

  function toggle(id: string, next: boolean): void {
    setDeclined((previous) => {
      const updated = new Set(previous)
      if (next) {
        updated.delete(id)
      } else {
        updated.add(id)
      }
      return updated
    })
  }

  return (
    <Scene
      id="fixes"
      title="What can be fixed"
      lede="GreenAccess can apply the changes below to a copy of the page, then scan that copy again. The after figures come from that second scan, not from an estimate of what the fixes should have achieved."
      className="scene--data"
    >
      {phase === 'idle' ? (
        <button className="button button--primary" type="button" onClick={onGenerate}>
          Generate fixes
        </button>
      ) : null}

      {phase === 'generating' ? <p className="status">Drafting fixes&#8230;</p> : null}

      {error ? (
        <p className="form-error" role="alert">
          {error.message}
        </p>
      ) : null}

      {aiUsage ? <AiCost usage={aiUsage} /> : null}

      {fixes.length > 0 ? (
        <>
          <p className="scene__sub">
            {pluralise(automatic.length, 'fix')} can be applied automatically
            {manual.length > 0 ? `; ${pluralise(manual.length, 'more')} need a person` : ''}.
          </p>

          {/* eslint-disable-next-line jsx-a11y/no-redundant-roles */}
          <ul className="fix-list" role="list">
            {automatic.map((fix) => (
              <FixRow
                key={fix.id}
                fix={fix}
                checked={!declined.has(fix.id)}
                disabled={busy || phase === 'applied'}
                onToggle={toggle}
              />
            ))}
          </ul>

          {phase === 'ready' ? (
            <button
              className="button button--primary"
              type="button"
              disabled={accepted.length === 0}
              onClick={() => onApply(accepted)}
            >
              Apply {pluralise(accepted.length, 'fix')} and re-scan
            </button>
          ) : null}

          {manual.length > 0 ? (
            <section className="fixes-manual" aria-labelledby="manual-title">
              <h3 id="manual-title">Needs a person</h3>
              <p className="scene__sub">
                These were detected but cannot be patched reliably, so GreenAccess reports them
                rather than guessing.
              </p>
              {/* eslint-disable-next-line jsx-a11y/no-redundant-roles */}
              <ul className="fix-list" role="list">
                {manual.map((fix) => (
                  <FixRow
                    key={fix.id}
                    fix={fix}
                    checked={false}
                    disabled
                    onToggle={toggle}
                  />
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}

      {steps.length > 0 ? (
        <>
          <p className="visually-hidden" role="status" aria-live="polite">
            {steps.filter((step) => step.status !== 'running').length} of {steps.length} fix
            steps complete.
          </p>
          <ol className="steps">
            {steps.map((step) => (
              <li className="steps__item" key={step.name} data-status={step.status}>
                <span className="steps__mark" aria-hidden="true" />
                <span className="steps__label">{stepLabel(step.name)}</span>
                <span className="steps__status">
                  {step.status === 'running' ? 'running' : step.status}
                  {step.status !== 'running' && step.ms > 0 ? (
                    <span className="steps__time"> · {formatDuration(step.ms)}</span>
                  ) : null}
                </span>
              </li>
            ))}
          </ol>
        </>
      ) : null}
    </Scene>
  )
}
