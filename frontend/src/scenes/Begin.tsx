/**
 * Chapter two: the console. Where the reader hands over a URL.
 *
 * Single responsibility: take a URL and start a scan.
 *
 * This is MASTERSPEC §13 screen 1's form, lifted out of the pinned opening.
 * Interactive controls do not belong in a chapter whose content dims as the
 * reader scrolls past it, so the form gets a section of its own that is always
 * fully visible and fully operable.
 */

import { useId, useState } from 'react'

import { Sapling } from '../components/Sapling'
import { useCountUp } from '../lib/motion'
import { useInView } from '../lib/scroll'
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

export function Begin({ onScan, demoUrl, busy, error }: BeginProps): JSX.Element {
  const fieldId = useId()
  const hintId = useId()
  const errorId = useId()
  const [url, setUrl] = useState('')
  const [ref, inView] = useInView<HTMLDivElement>({ threshold: 0.2 })
  const growth = useCountUp(100, inView, 1800)

  function handleSubmit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const trimmed = url.trim()
    if (trimmed !== '' && !busy) {
      onScan(trimmed)
    }
  }

  return (
    <section className="scene scene--begin" id="begin" aria-labelledby="begin-title">
      <div className="container begin">
        <div className="begin__text">
          <p className="scene__eyebrow">
            <span className="panel__bullet" aria-hidden="true" />
            Start a scan
          </p>

          <h2 className="begin__title" id="begin-title">
            How inclusive and how green is your website?
          </h2>

          <p className="scene__lede">
            One page per scan. GreenAccess loads it in a real browser, runs the accessibility
            rule set, crawls it with the Tab key, and weighs every byte it requests.
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
          </form>

          {/*
            `role="alert"` is assertive on purpose: a failed scan means the
            reader is waiting for something that is not coming, and should not
            have to discover that by scrolling.
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

        <div className="begin__figure" ref={ref}>
          <Sapling growth={growth / 100} className="sapling--hero" />
        </div>
      </div>
    </section>
  )
}
