/**
 * Chapter two: the console. Where the reader hands over a URL.
 *
 * Single responsibility: take a URL and start a scan.
 *
 * This is MASTERSPEC §13 screen 1's form, lifted out of the pinned opening.
 * Interactive controls do not belong in a chapter whose content dims as the
 * reader scrolls past it, so the form gets a section of its own that is always
 * fully visible and fully operable.
 *
 * It is laid out as a full-bleed split rather than a centred card: the argument
 * and the field on one side, a voxel diorama on the other. A lone input box in
 * the middle of a wide screen reads as a form to be filled in; the same input
 * beside the thing it acts on reads as the start of a story.
 */

import { useId, useState } from 'react'

import { Sprite } from '../components/Sprite'
import type { ApiError } from '../lib/types'

export interface BeginProps {
  /** Start a scan for this URL. */
  onScan: (url: string) => void
  /** Where the Daily Herald demo is served, once `GET /api/demo` has answered. */
  demoUrl: string | null
  /** True while a scan is starting or running; the form is disabled. */
  busy: boolean
  /** The last failure, shown in the form's error region. */
  error: ApiError | null
}

/** A plain-language explanation for each error code the form can surface. */
function explain(error: ApiError): string {
  switch (error.code) {
    case 'URL_BLOCKED':
      return `That address cannot be scanned: ${error.message}. GreenAccess only visits public http and https pages, never private or internal addresses.`
    case 'RATE_LIMITED':
      return `Too many scans just now. ${error.message}`
    case 'NAV_FAILED':
      return `The page could not be reached. ${error.message}`
    case 'TIMEOUT':
      return `The page took too long to load, so the scan was stopped. ${error.message}`
    case 'PAGE_TOO_LARGE':
      return `The page exceeded the size GreenAccess will download. ${error.message}`
    case 'CANCELLED':
      return 'Scan cancelled.'
    default:
      return error.message
  }
}

/** What one scan actually does, as three short claims beside the form. */
const STEPS = [
  { n: '01', label: 'Load', body: 'A real Chromium browser opens the page and scrolls it to the bottom.' },
  { n: '02', label: 'Measure', body: 'Accessibility rules, a Tab-key crawl, and every byte the page requests.' },
  { n: '03', label: 'Compare', body: 'Where a fix helps both goals, and where it cannot.' },
]

export function Begin({ onScan, demoUrl, busy, error }: BeginProps): JSX.Element {
  const fieldId = useId()
  const hintId = useId()
  const errorId = useId()
  const [url, setUrl] = useState('')

  function handleSubmit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const trimmed = url.trim()
    if (trimmed !== '' && !busy) {
      onScan(trimmed)
    }
  }

  return (
    <section className="begin" id="begin" aria-labelledby="begin-title">
      <div className="begin__inner">
        <div className="begin__text">
          <p className="scene__eyebrow">
            <span className="panel__bullet" aria-hidden="true" />
            Start a scan
          </p>

          <h2 className="begin__title" id="begin-title">
            Give it one page.
            <span className="begin__title-soft">It will tell you what that page costs.</span>
          </h2>

          <form className="scan-form" onSubmit={handleSubmit} noValidate>
            <div className="field">
              <label className="field__label" htmlFor={fieldId}>
                Website URL
              </label>
              <input
                className="field__input"
                id={fieldId}
                name="url"
                type="url"
                inputMode="url"
                autoComplete="url"
                placeholder="https://example.com"
                aria-describedby={error ? `${hintId} ${errorId}` : hintId}
                {...(error ? { 'aria-invalid': true as const } : {})}
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                disabled={busy}
              />
              <span className="field__hint" id={hintId}>
                Public http and https addresses only.
              </span>
            </div>

            <div className="scan-form__actions">
              <button className="button button--primary" type="submit" disabled={busy}>
                {busy ? 'Scanning…' : 'Scan'}
              </button>

              {/*
                The demo runs the same pipeline against the Daily Herald, so the
                numbers it produces are real. Disabled until GET /api/demo answers.
              */}
              <button
                className="button button--secondary"
                type="button"
                onClick={() => demoUrl && onScan(demoUrl)}
                disabled={busy || demoUrl === null}
              >
                Try the Daily Herald demo
              </button>
            </div>
          </form>

          {/*
            `role="alert"` is assertive on purpose: a failed scan means the
            reader is waiting for something that is not coming, and should not
            have to discover that by scrolling.
          */}
          <p className="form-error" id={errorId} role="alert">
            {error ? explain(error) : ''}
          </p>

          <ol className="begin__steps">
            {STEPS.map((step) => (
              <li key={step.n}>
                <span className="begin__step-n" aria-hidden="true">
                  {step.n}
                </span>
                <h3 className="begin__step-label">{step.label}</h3>
                <p className="begin__step-body">{step.body}</p>
              </li>
            ))}
          </ol>

          <p className="note">
            <span className="chip chip--estimate">Estimate</span>
            Automated checks only; carbon values are estimates from the Sustainable Web Design
            model.
          </p>
        </div>

        {/*
          The diorama. Every piece is decorative and stated in the text beside
          it, so all of it carries an empty alt. The console is the one place
          art loads eagerly: it is the first thing below the opening and the
          reader is looking straight at it.
        */}
        <div className="begin__scene">
          <div className="diorama">
            <Sprite name="iso-cloud" width={150} className="diorama__cloud" />
            <Sprite name="iso-tree" width={200} className="diorama__tree" />
            <Sprite name="char-scan" width={280} className="diorama__character" eager />
            <Sprite name="iso-sprout" width={110} className="diorama__sprout" />
          </div>
        </div>
      </div>
    </section>
  )
}
