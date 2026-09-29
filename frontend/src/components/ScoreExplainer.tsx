/**
 * The "How is this calculated?" disclosure for one score (MASTERSPEC §9).
 *
 * Single responsibility: show the real breakdown the backend computed — the
 * formula, the starting points, and every item that moved the number.
 *
 * Built on `<details>`/`<summary>` rather than a custom popover. Native
 * disclosure is keyboard operable, announced correctly, survives find-in-page
 * and needs no focus management, which is a better trade than a bespoke widget
 * in a product that audits accessibility.
 */

import type { ScoreBreakdown } from '../lib/types'

export interface ScoreExplainerProps {
  /** Which score this explains, e.g. "Accessibility". */
  title: string
  breakdown: ScoreBreakdown
}

export function ScoreExplainer({ title, breakdown }: ScoreExplainerProps): JSX.Element {
  return (
    <details className="explainer">
      <summary className="explainer__summary">How is {title} calculated?</summary>

      <div className="explainer__body">
        <p className="explainer__formula">
          <code>{breakdown.formula}</code>
        </p>
        <p className="explainer__ref">{breakdown.spec_ref}</p>

        {breakdown.items.length === 0 ? (
          <p className="explainer__empty">
            Nothing counted against this score. It starts at {breakdown.starting_points} and stays
            there.
          </p>
        ) : (
          // A scrollable region, so a narrow screen scrolls the table rather
          // than the whole page (WCAG 1.4.10 Reflow).
          <div
            className="table-scroll"
            tabIndex={0}
            role="region"
            aria-label={`Every item contributing to the ${title} score`}
          >
            <table className="explainer__table">
              <caption className="visually-hidden">
                Every item contributing to the {title} score
              </caption>
              <thead>
                <tr>
                  <th scope="col">What</th>
                  <th scope="col">Points</th>
                  <th scope="col">Detail</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">Starting points</th>
                  <td className="explainer__points">{breakdown.starting_points}</td>
                  <td />
                </tr>
                {breakdown.items.map((item, index) => (
                  <tr key={`${item.label}-${index}`}>
                    <th scope="row">{item.label}</th>
                    <td className="explainer__points">
                      {item.points > 0 ? `+${item.points}` : item.points}
                    </td>
                    <td>{item.detail}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row">Total</th>
                  <td className="explainer__points">{breakdown.total}</td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </div>
    </details>
  )
}
