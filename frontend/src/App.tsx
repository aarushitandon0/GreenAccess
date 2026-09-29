/**
 * Application root: the story, in order.
 *
 * Single responsibility: own the scan and decide which chapters exist yet.
 *
 * The dashboard of MASTERSPEC §13 is told here as one continuous page. Its six
 * tabs become chapters of the same document over the same `ScanResult`, and
 * its tab bar becomes the sticky chapter nav. No score, model or endpoint
 * changes; only the arrangement does.
 *
 * Chapters appear as their data does. Before a scan there is the pinned
 * opening and the console; the later chapters are added as the pipeline
 * produces what they describe, which is why nothing here renders a placeholder
 * score.
 */

import { useEffect, useMemo, useState } from 'react'

import { ChapterNav, type Chapter } from './components/ChapterNav'
import { Accessibility } from './scenes/Accessibility'
import { After } from './scenes/After'
import { Begin } from './scenes/Begin'
import { Carbon } from './scenes/Carbon'
import { Fixes } from './scenes/Fixes'
import { Opening } from './scenes/Opening'
import { Reveal } from './scenes/Reveal'
import { Scanning } from './scenes/Scanning'
import { Tradeoffs } from './scenes/Tradeoffs'
import { getDemo } from './lib/api'
import { useFixes } from './lib/useFixes'
import { useScan } from './lib/useScan'

export function App(): JSX.Element {
  const scan = useScan()
  // The fix pipeline hands the re-scanned scan back; the scan hook owns it, so
  // there is only ever one copy of the result in the tree.
  const fixes = useFixes(scan.setScan)
  const [demoUrl, setDemoUrl] = useState<string | null>(null)

  // Where the demo lives is the backend's business, not a constant in the UI.
  // A failure here only disables the demo button; the URL field still works.
  useEffect(() => {
    let live = true
    getDemo()
      .then((info) => {
        if (live) setDemoUrl(info.url)
      })
      .catch(() => {
        if (live) setDemoUrl(null)
      })
    return () => {
      live = false
    }
  }, [])

  const busy = scan.phase === 'starting' || scan.phase === 'running' || scan.phase === 'loading'
  const started = scan.phase !== 'idle'
  const result = scan.scan?.before ?? null
  // A scan can finish with a placeholder score if the pipeline degraded; the
  // verdict chapter is only truthful with real figures behind it.
  const scored = result !== null && !result.scores.is_placeholder
  const afterResult = scan.scan?.after ?? null
  const hasAfter = afterResult !== null && !afterResult.scores.is_placeholder

  const chapters = useMemo<Chapter[]>(() => {
    const list: Chapter[] = [
      { id: 'opening', label: 'Opening' },
      { id: 'begin', label: 'Scan' },
    ]
    if (started) list.push({ id: 'scanning-title', label: 'Progress' })
    if (scored) {
      list.push(
        { id: 'verdict', label: 'Verdict' },
        { id: 'accessibility', label: 'Access' },
        { id: 'carbon', label: 'Carbon' },
        { id: 'tradeoffs', label: 'Trade-offs' },
        { id: 'fixes', label: 'Fixes' },
      )
    }
    if (hasAfter) list.push({ id: 'after', label: 'After' })
    return list
  }, [started, scored, hasAfter])

  return (
    <div className="page">
      <a className="skip-link visually-hidden-focusable" href="#main">
        Skip to main content
      </a>

      {/*
        The header floats over the pinned opening rather than pushing it down:
        the first chapter is full-bleed, and a bar above it would break that.
      */}
      {/*
        Scoped to the cinema palette because it sits on the dark opening: the
        tokens resolve to the values the contrast gate verifies for that scope,
        rather than to light-theme ink on a dark landscape.
      */}
      <header className="site-header site-header--float" data-scope="cinema">
        <div className="site-header__inner">
          <a className="brand" href="#opening">
            <span className="brand__mark" aria-hidden="true">
              GA
            </span>
            GreenAccess
          </a>
          <nav aria-label="Primary">
            {/*
              A real anchor, so the reader can reach the form without scrolling
              the whole opening. The chapter is a story, not a toll gate.
            */}
            <a className="site-header__action" href="#begin">
              Scan a site
            </a>
          </nav>
        </div>
      </header>

      <main className="main main--story" id="main" tabIndex={-1}>
        <Opening />

        {chapters.length > 2 ? <ChapterNav chapters={chapters} /> : null}

        <Begin
          onScan={scan.start}
          demoUrl={demoUrl}
          busy={busy}
          error={scan.phase === 'error' ? scan.error : null}
        />

        {started && scan.scanId !== null ? (
          <Scanning
            url={scan.url ?? ''}
            steps={scan.steps}
            running={busy}
            onCancel={scan.cancel}
          />
        ) : null}

        {scored && result !== null && scan.scanId !== null ? (
          <>
            <Reveal scanId={scan.scanId} url={scan.scan?.url ?? scan.url ?? ''} result={result} />
            <Accessibility a11y={result.a11y} keyboard={result.keyboard} />
            <Carbon carbon={result.carbon} />
            <Tradeoffs tradeoffs={result.tradeoffs} />
            <Fixes
              phase={fixes.phase}
              fixes={fixes.fixes}
              aiUsage={fixes.aiUsage}
              steps={fixes.steps}
              error={fixes.error}
              onGenerate={() => scan.scanId && fixes.generate(scan.scanId)}
              onApply={(ids) => scan.scanId && fixes.apply(scan.scanId, ids)}
            />
          </>
        ) : null}

        {hasAfter && afterResult !== null && result !== null && scan.scanId !== null ? (
          <After
            scanId={scan.scanId}
            before={result}
            after={afterResult}
            patch={scan.scan?.patch ?? null}
          />
        ) : null}
      </main>

      <footer className="site-footer">
        <div className="container">
          GreenAccess reports issues found by automated checks. It does not certify WCAG
          conformance. Carbon figures are estimates from the Sustainable Web Design model.
        </div>
      </footer>
    </div>
  )
}
