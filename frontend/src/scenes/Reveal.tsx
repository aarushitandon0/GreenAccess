/**
 * Chapter three: the verdict.
 *
 * Single responsibility: show the three scores from MASTERSPEC §9 and the one
 * carbon figure they rest on, each with the breakdown that produced it.
 *
 * Everything here comes from the `ScanResult` the backend saved. Nothing is
 * derived in the browser, so what the reader sees is what was scored.
 */

import { Gauge } from '../components/Gauge'
import { Sapling } from '../components/Sapling'
import { Scene } from '../components/Scene'
import { ScoreExplainer } from '../components/ScoreExplainer'
import { screenshotUrl } from '../lib/api'
import { formatBytes, formatGrams, pluralise } from '../lib/format'
import { useCountUp } from '../lib/motion'
import { useInView } from '../lib/scroll'
import type { ScanResult } from '../lib/types'

export interface RevealProps {
  scanId: string
  url: string
  result: ScanResult
}

export function Reveal({ scanId, url, result }: RevealProps): JSX.Element {
  const [ref, inView] = useInView<HTMLDivElement>({ threshold: 0.3 })
  const { scores, carbon, green, a11y } = result

  // The tree stands for the combined score, which is the chapter's headline.
  const growth = useCountUp(scores.combined, inView, 1400)

  return (
    <Scene
      id="verdict"
      title="The verdict"
      lede={`Three scores for ${url}. Accessibility counts what automated rules found; carbon estimates what one page view costs; combined weighs them ${Math.round(scores.weights.a11y * 100)}/${Math.round(scores.weights.carbon * 100)}.`}
      className="scene--reveal"
    >
      <div className="reveal" ref={ref}>
        <div className="reveal__gauges">
          <Gauge
            label="Accessibility"
            value={scores.a11y}
            animate={inView}
            detail={`${pluralise(a11y.unique_rules, 'rule')} failed, ${pluralise(a11y.total_nodes, 'element')} affected`}
          />
          <Gauge
            label="Carbon"
            value={scores.carbon}
            grade={scores.carbon_grade}
            animate={inView}
            detail={`${formatGrams(carbon.grams_per_view)} CO₂ per view (estimate)`}
          />
          <Gauge
            label="Combined"
            value={scores.combined}
            animate={inView}
            detail={`${formatBytes(carbon.total_bytes)} over ${pluralise(carbon.request_count, 'request')}`}
          />
        </div>

        <div className="reveal__tree">
          <Sapling growth={growth / 100} className="sapling--verdict" />
          <p className="reveal__tree-caption">
            The tree tracks the combined score. It grows again after the fixes are applied and
            the page is scanned a second time.
          </p>
        </div>
      </div>

      <div className="reveal__facts">
        <dl className="facts">
          <div className="facts__row">
            <dt>Page weight</dt>
            <dd>{formatBytes(carbon.total_bytes)}</dd>
          </div>
          <div className="facts__row">
            <dt>Requests</dt>
            <dd>{carbon.request_count}</dd>
          </div>
          <div className="facts__row">
            <dt>Carbon per view</dt>
            <dd>
              {formatGrams(carbon.grams_per_view)} <span className="chip chip--estimate">Estimate</span>
            </dd>
          </div>
          <div className="facts__row">
            <dt>Green hosting</dt>
            <dd>
              {/*
                `unavailable` is reported as unknown, not as "not green": the
                lookup failing tells us nothing about the host.
              */}
              {green.source === 'unavailable'
                ? 'Unknown — the Green Web Foundation lookup did not answer'
                : green.green
                  ? `Yes${green.hosted_by ? ` — ${green.hosted_by}` : ''}`
                  : 'Not listed as green'}
            </dd>
          </div>
        </dl>
      </div>

      {scores.breakdown ? (
        <div className="reveal__explainers">
          <ScoreExplainer title="Accessibility" breakdown={scores.breakdown.a11y} />
          <ScoreExplainer title="Carbon" breakdown={scores.breakdown.carbon} />
          <ScoreExplainer title="Combined" breakdown={scores.breakdown.combined} />
        </div>
      ) : null}

      <figure className="reveal__shot">
        {/*
          The screenshot is a record of what was scanned. It is decorative in
          the strict sense — every finding drawn from it is stated in text — but
          it still gets a real alt, because "what the page looked like" is
          information a reader may want confirmed.
        */}
        <img
          src={screenshotUrl(scanId, 'before')}
          alt={`Full-page screenshot of ${url} as it was scanned`}
          loading="lazy"
          decoding="async"
        />
        <figcaption>The page as GreenAccess loaded it, scrolled to the bottom.</figcaption>
      </figure>
    </Scene>
  )
}
