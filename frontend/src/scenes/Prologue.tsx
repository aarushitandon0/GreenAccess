/**
 * Chapter one: the question, and the field that answers it.
 *
 * Single responsibility: state what GreenAccess measures and take a URL.
 *
 * This is MASTERSPEC §13 screen 1, told as the opening of the narrative rather
 * than as a separate page. It holds the document's only `h1`.
 */

import { useId, useState } from 'react'

import { Sapling } from '../components/Sapling'
import { useCountUp } from '../lib/motion'
import { useInView } from '../lib/scroll'
import type { ApiError } from '../lib/types'

export interface PrologueProps {
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

export function Prologue({ onScan, demoUrl, busy, error }: PrologueProps): JSX.Element {
  const fieldId = useId()
  const hintId = useId()
  const errorId = useId()
  const [url, setUrl] = useState('')
  const [ref, inView] = useInView<HTMLDivElement>({ threshold: 0.2 })
  // The opening tree is at rest, not at a score: nothing has been measured yet.
  // It grows for real, from the real figures, in the reveal chapter.
  const heroGrowth = useCountUp(100, inView, 1800)

  function handleSubmit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const trimmed = url.trim()
    if (trimmed !== '' && !busy) {
      onScan(trimmed)
    }
  }

  return (
    <section className="scene scene--prologue" aria-labelledby="prologue-title">
      <div className="container prologue">
        <div className="prologue__text">
          <p className="prologue__eyebrow">Accessibility and carbon, measured together</p>

          <h1 className="prologue__title" id="prologue-title">
            How inclusive and how green is your website?
          </h1>

          <p className="prologue__lede">
            Most tools measure one or the other. The interesting part is where they meet: the
            fixes that help both at once, and the handful that genuinely pull against each
            other. Paste a URL and scroll.
          </p>

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
                One page per scan. Public http and https addresses only.
              </span>
            </div>

            <button className="button button--primary" type="submit" disabled={busy}>
              {busy ? 'Scanning…' : 'Scan'}
            </button>

            {/*
              The demo is a scan like any other, not a link away from the app:
              it runs the same pipeline against the Daily Herald so the numbers
              it produces are real. Disabled until GET /api/demo has answered.
            */}
            <button
              className="button button--secondary"
              type="button"
              onClick={() => demoUrl && onScan(demoUrl)}
              disabled={busy || demoUrl === null}
            >
              Try the Daily Herald demo
            </button>
          </form>

          {/*
            Errors are announced. `role="alert"` is assertive on purpose: a
            failed scan means the reader is waiting for something that is not
            coming, and should not have to discover that by scrolling.
          */}
          <p className="form-error" id={errorId} role="alert">
            {error ? explain(error) : ''}
          </p>

          <p className="note">
            <span className="chip chip--estimate">Estimate</span>
            Automated checks only; carbon values are estimates from the Sustainable Web Design
            model.
          </p>
        </div>

        <div className="prologue__figure" ref={ref}>
          <Sapling growth={heroGrowth / 100} className="sapling--hero" />
        </div>
      </div>
    </section>
  )
}
