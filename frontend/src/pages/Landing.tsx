/**
 * Landing screen (MASTERSPEC §13, screen 1).
 *
 * Phase 0 scope: structure, landmarks and design tokens only. The form does not
 * submit a scan yet -- the API for that does not exist until a later phase, and
 * CLAUDE.md forbids starting later phases early.
 */

import { useId, useState } from 'react'

const DEMO_URL = 'http://localhost:8081'

export function Landing(): JSX.Element {
  const urlFieldId = useId()
  const urlHintId = useId()
  const [url, setUrl] = useState('')
  const [status, setStatus] = useState('')

  function handleSubmit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    // Scanning arrives with the API phase. Announce honestly rather than
    // pretending to work (CLAUDE.md: "Never fake results").
    setStatus('Scanning is not wired up yet in this build.')
  }

  return (
    <div className="page">
      {/* Skip link (MASTERSPEC §13). First focusable element on the page. */}
      <a className="skip-link visually-hidden-focusable" href="#main">
        Skip to main content
      </a>

      <header className="site-header">
        <div className="container site-header__inner">
          <a className="brand" href="/">
            <span className="brand__mark" aria-hidden="true">
              GA
            </span>
            GreenAccess
          </a>
          <nav aria-label="Primary">
            <span className="chip chip--leaf">Automated checks</span>
          </nav>
        </div>
      </header>

      <main className="main" id="main" tabIndex={-1}>
        <div className="container">
          <div className="hero">
            <h1 className="hero__title">How inclusive and how green is your website?</h1>
            <p className="hero__lede">
              Paste a URL. Get one score for accessibility and one for carbon, with the
              overlaps and the genuine trade-offs between them explained.
            </p>

            <form className="scan-form" onSubmit={handleSubmit} noValidate>
              <div className="field">
                <label className="field__label" htmlFor={urlFieldId}>
                  Website URL
                </label>
                <input
                  className="field__input"
                  id={urlFieldId}
                  name="url"
                  type="url"
                  inputMode="url"
                  autoComplete="url"
                  placeholder="https://example.com"
                  aria-describedby={urlHintId}
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                />
                <span className="field__hint" id={urlHintId}>
                  One page per scan. Public http and https addresses only.
                </span>
              </div>
              <button className="button button--primary" type="submit">
                Scan
              </button>
              <a className="button button--secondary" href={DEMO_URL}>
                Try the Daily Herald demo
              </a>
            </form>

            {/*
              Live region for scan progress and score changes (MASTERSPEC §13).
              It is rendered unconditionally so assistive technology observes it
              from first paint; injecting it later can swallow the first message.
            */}
            <p className="status" role="status" aria-live="polite">
              {status}
            </p>

            <p className="note">
              <span className="chip chip--estimate">Estimate</span>
              Automated checks only; carbon values are estimates from the Sustainable Web
              Design model.
            </p>
          </div>

          <section className="features" aria-labelledby="features-heading">
            <h2 className="features__title" id="features-heading">
              What a scan covers
            </h2>
            {/*
              role="list" is intentional and is not redundant in practice:
              Safari/VoiceOver strips list semantics from a <ul> whose
              list-style is none, and base.css removes the markers via the
              `ul[role='list']` selector. Restoring the role keeps the list
              announced as a list.
            */}
            {/* eslint-disable-next-line jsx-a11y/no-redundant-roles */}
            <ul className="card-grid" role="list">
              <li className="card">
                <h3 className="card__title">Accessibility</h3>
                <p className="card__body">
                  axe-core rules for WCAG 2.0 and 2.1 A and AA, plus our own keyboard Tab
                  crawl for focus traps that automated rule sets miss.
                </p>
              </li>
              <li className="card">
                <h3 className="card__title">Carbon</h3>
                <p className="card__body">
                  Transfer size by type, third-party requests, image and font weight, and
                  grams of CO&#8322; per view from the Sustainable Web Design model.
                </p>
              </li>
              <li className="card">
                <h3 className="card__title">Trade-offs</h3>
                <p className="card__body">
                  Where a fix helps both at once, and where making a page greener would
                  make it less usable. Both are shown, with the numbers.
                </p>
              </li>
            </ul>
          </section>
        </div>
      </main>

      <footer className="site-footer">
        <div className="container">
          GreenAccess reports issues found by automated checks. It does not certify WCAG
          conformance.
        </div>
      </footer>
    </div>
  )
}
