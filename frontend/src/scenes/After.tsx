/**
 * Chapter: the page after the fixes, measured again.
 *
 * Single responsibility: compare the before and after results and say what
 * actually changed.
 *
 * Every figure here comes from a second real scan of the patched page. That is
 * the whole point of the chapter and the one thing it must never fake: no
 * projected score, no "expected" saving, no arithmetic on the before-figures
 * dressed up as a result.
 */

import { Gauge } from '../components/Gauge'
import { Sprite } from '../components/Sprite'
import { Scene } from '../components/Scene'
import { patchZipUrl, screenshotUrl } from '../lib/api'
import {
  formatByteDelta,
  formatBytes,
  formatCountDelta,
  formatGrams,
  formatGramsDelta,
  formatReduction,
  pluralise,
} from '../lib/format'
import { useInView } from '../lib/scroll'
import type { PatchInfo, ScanResult } from '../lib/types'

/** One before/after delta, stated with its direction in words as well as sign. */
function Delta({
  label,
  value,
  improved,
}: {
  label: string
  value: string
  improved: boolean
}): JSX.Element {
  return (
    <div className="delta" data-improved={improved ? 'true' : 'false'}>
      <span className="delta__value">{value}</span>
      <span className="delta__label">{label}</span>
      {/* Direction is never carried by colour alone (WCAG 1.4.1). */}
      <span className="visually-hidden">{improved ? ' — improved' : ' — worse'}</span>
    </div>
  )
}

export interface AfterProps {
  scanId: string
  before: ScanResult
  after: ScanResult
  patch: PatchInfo | null
}

export function After({ scanId, before, after, patch }: AfterProps): JSX.Element {
  const [ref, inView] = useInView<HTMLDivElement>({ threshold: 0.3 })

  const byteDelta = after.carbon.total_bytes - before.carbon.total_bytes
  const gramDelta = after.carbon.grams_per_view - before.carbon.grams_per_view
  const nodeDelta = after.a11y.total_nodes - before.a11y.total_nodes
  const reduction = formatReduction(before.carbon.total_bytes, after.carbon.total_bytes)
  const applied = patch?.fixes.filter((fix) => fix.applied).length ?? 0

  return (
    <Scene
      id="after"
      eyebrow="After the fixes"
      title="The same page, scanned again"
      lede={`${pluralise(applied, 'fix')} applied, then the patched page was loaded and measured from scratch. These are the numbers that second scan produced.`}
      className="scene--after"
    >
      <div className="reveal" ref={ref}>
        <div className="reveal__gauges">
          <Gauge
            label="Accessibility"
            value={after.scores.a11y}
            animate={inView}
            detail={`was ${before.scores.a11y}`}
          />
          <Gauge
            label="Carbon"
            value={after.scores.carbon}
            grade={after.scores.carbon_grade}
            animate={inView}
            detail={`was ${before.scores.carbon} (grade ${before.scores.carbon_grade})`}
          />
          <Gauge
            label="Combined"
            value={after.scores.combined}
            animate={inView}
            detail={`was ${before.scores.combined}`}
          />
        </div>

        <div className="reveal__tree">
          <Sprite name={'char-build'} width={200} className="sprite--verdict" />
          <p className="reveal__tree-caption">
            The tree tracks the combined score from the re-scan.
          </p>
        </div>
      </div>

      <div className="deltas">
        <Delta
          label="Accessibility score"
          value={formatCountDelta(after.scores.a11y - before.scores.a11y)}
          improved={after.scores.a11y >= before.scores.a11y}
        />
        <Delta
          label="Page weight"
          value={formatByteDelta(byteDelta)}
          improved={byteDelta <= 0}
        />
        <Delta
          label={'CO₂ per view (estimate)'}
          value={formatGramsDelta(gramDelta)}
          improved={gramDelta <= 0}
        />
        <Delta
          label="Failing elements"
          value={formatCountDelta(nodeDelta)}
          improved={nodeDelta <= 0}
        />
      </div>

      <dl className="facts">
        <div className="facts__row">
          <dt>Page weight</dt>
          <dd>
            {formatBytes(before.carbon.total_bytes)} &rarr;{' '}
            {formatBytes(after.carbon.total_bytes)}
            {reduction ? <span className="facts__aside">{reduction} lighter</span> : null}
          </dd>
        </div>
        <div className="facts__row">
          <dt>Carbon per view</dt>
          <dd>
            {formatGrams(before.carbon.grams_per_view)} &rarr;{' '}
            {formatGrams(after.carbon.grams_per_view)}{' '}
            <span className="chip chip--estimate">Estimate</span>
          </dd>
        </div>
        <div className="facts__row">
          <dt>Rules failing</dt>
          <dd>
            {before.a11y.unique_rules} &rarr; {after.a11y.unique_rules}
          </dd>
        </div>
        <div className="facts__row">
          <dt>Requests</dt>
          <dd>
            {before.carbon.request_count} &rarr; {after.carbon.request_count}
          </dd>
        </div>
      </dl>

      {patch && patch.skipped.length > 0 ? (
        <section className="skipped" aria-labelledby="skipped-title">
          <h3 id="skipped-title">Not applied</h3>
          {/* eslint-disable-next-line jsx-a11y/no-redundant-roles */}
          <ul className="detection-list" role="list">
            {patch.skipped.map((item) => (
              <li className="detection" key={item.fix_id}>
                <h4 className="detection__title">
                  <code>{item.fix_id}</code>
                </h4>
                <p className="detection__saving">{item.reason}</p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="after__shots">
        <figure className="reveal__shot">
          <img
            src={screenshotUrl(scanId, 'before')}
            alt="Full-page screenshot of the page before any fixes were applied"
            loading="lazy"
            decoding="async"
          />
          <figcaption>Before</figcaption>
        </figure>
        <figure className="reveal__shot">
          <img
            src={screenshotUrl(scanId, 'after')}
            alt="Full-page screenshot of the patched page, as it was scanned again"
            loading="lazy"
            decoding="async"
          />
          <figcaption>After</figcaption>
        </figure>
      </div>

      {patch?.zip_path ? (
        <p className="after__download">
          <a className="button button--primary" href={patchZipUrl(scanId)} download>
            Download the patched site
          </a>
          <span className="field__hint">
            A zip of the patched page, the stylesheet GreenAccess added, any optimised images,
            and a CHANGES.md listing what was applied and what was skipped.
          </span>
        </p>
      ) : null}
    </Scene>
  )
}
