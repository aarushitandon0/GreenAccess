/**
 * Chapter: where the two goals meet, and where they pull apart.
 *
 * Single responsibility: present the trade-off engine's findings, separating
 * the fixes that help both from the ones that genuinely cost one to serve the
 * other.
 *
 * This is the chapter the product exists for, and the one thing it must not do
 * is pretend the tension cases away. A synergy and a tension are given the same
 * weight and the same level of detail; the tensions are not buried below the
 * fold or softened into advice.
 *
 * Presented in the cinema scope — dark, set apart from the data chapters —
 * because it is an argument rather than a readout.
 */

import { Scene } from '../components/Scene'
import { formatByteDelta, formatGramsDelta, truncateMiddle } from '../lib/format'
import type { TradeoffFinding } from '../lib/types'

function Finding({ finding }: { finding: TradeoffFinding }): JSX.Element {
  const synergy = finding.type === 'synergy'
  return (
    <li className="finding" data-type={finding.type}>
      <p className="finding__badge">
        <span className="panel__bullet" aria-hidden="true" />
        {/*
          Type is a word, never only the card's accent: a reader who cannot
          distinguish the two colours still gets the distinction (WCAG 1.4.1).
        */}
        {synergy ? 'Helps both' : 'Genuine tension'}
      </p>

      <h3 className="finding__title">{finding.title}</h3>
      <p className="finding__explanation">{finding.explanation}</p>

      <dl className="finding__effects">
        <div>
          <dt>Accessibility</dt>
          <dd>{finding.a11y_impact}</dd>
        </div>
        <div>
          <dt>Carbon</dt>
          <dd>
            {finding.carbon_delta_bytes === 0 ? (
              'No change in transfer size'
            ) : (
              <>
                {formatByteDelta(finding.carbon_delta_bytes)} ·{' '}
                {formatGramsDelta(finding.carbon_delta_grams)} per view{' '}
                <span className="chip chip--estimate">Estimate</span>
              </>
            )}
          </dd>
        </div>
      </dl>

      {finding.evidence.length > 0 ? (
        <details className="finding__evidence">
          <summary>Where this was found</summary>
          {/* eslint-disable-next-line jsx-a11y/no-redundant-roles */}
          <ul role="list">
            {finding.evidence.map((item, index) => (
              <li key={index}>
                <code>{truncateMiddle(item, 56)}</code>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </li>
  )
}

export interface TradeoffsProps {
  tradeoffs: TradeoffFinding[]
}

export function Tradeoffs({ tradeoffs }: TradeoffsProps): JSX.Element {
  const synergies = tradeoffs.filter((finding) => finding.type === 'synergy')
  const tensions = tradeoffs.filter((finding) => finding.type === 'tension')

  return (
    <div data-scope="cinema" className="scene-wrap scene-wrap--cinema">
      <Scene
        id="tradeoffs"
        eyebrow="Trade-offs"
        motif={'char-tradeoffs'}
        title="Where the two goals meet"
        lede="Most of what makes a page lighter also makes it more usable. Not all of it. Both cases are below, with the numbers behind them."
        className="scene--tradeoffs"
      >
        {tradeoffs.length === 0 ? (
          <p className="empty">
            The trade-off engine found nothing to report for this page. That means none of its
            rules matched — not that no trade-offs exist.
          </p>
        ) : (
          <div className="findings">
            <section aria-labelledby="synergies-title">
              <h3 className="findings__heading" id="synergies-title">
                Fixes that help both
                <span className="findings__count">{synergies.length}</span>
              </h3>
              {synergies.length === 0 ? (
                <p className="empty">None matched on this page.</p>
              ) : (
                /* eslint-disable-next-line jsx-a11y/no-redundant-roles */
                <ul className="finding-list" role="list">
                  {synergies.map((finding, index) => (
                    <Finding key={`${finding.rule_id}-${index}`} finding={finding} />
                  ))}
                </ul>
              )}
            </section>

            <section aria-labelledby="tensions-title">
              <h3 className="findings__heading" id="tensions-title">
                Where they pull apart
                <span className="findings__count">{tensions.length}</span>
              </h3>
              {tensions.length === 0 ? (
                <p className="empty">
                  None matched on this page. That is not a guarantee there are none.
                </p>
              ) : (
                /* eslint-disable-next-line jsx-a11y/no-redundant-roles */
                <ul className="finding-list" role="list">
                  {tensions.map((finding, index) => (
                    <Finding key={`${finding.rule_id}-${index}`} finding={finding} />
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </Scene>
    </div>
  )
}
