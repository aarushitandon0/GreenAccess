/**
 * Chapter: what the accessibility checks found.
 *
 * Single responsibility: present the axe-core violations and our own keyboard
 * crawl, grouped so the worst is read first.
 *
 * Wording is held to MASTERSPEC's accuracy rules: these are issues detected by
 * automated checks, never a claim of conformance, and the keyboard findings are
 * labelled as coming from our own Tab crawl rather than from axe.
 */

import { Scene } from '../components/Scene'
import { pluralise, truncateMiddle } from '../lib/format'
import type { A11yResult, Impact, KeyboardResult, Violation } from '../lib/types'

/** Most severe first. */
const IMPACTS: Impact[] = ['critical', 'serious', 'moderate', 'minor']

const IMPACT_LABELS: Record<Impact, string> = {
  critical: 'Critical',
  serious: 'Serious',
  moderate: 'Moderate',
  minor: 'Minor',
}

function ViolationCard({ violation }: { violation: Violation }): JSX.Element {
  return (
    <li className="violation" data-impact={violation.impact}>
      <div className="violation__head">
        {/*
          The impact is a word, not only a colour on the card's edge: severity
          must survive being read in greyscale (WCAG 1.4.1).
        */}
        <span className="violation__impact">{IMPACT_LABELS[violation.impact]}</span>
        <span className="violation__count">{pluralise(violation.nodes.length, 'element')}</span>
      </div>

      <h4 className="violation__title">{violation.help}</h4>
      <p className="violation__rule">
        <code>{violation.rule_id}</code>
      </p>

      <details className="violation__nodes">
        <summary>
          Show the {violation.nodes.length === 1 ? 'element' : `${violation.nodes.length} elements`}
        </summary>
        <ul className="violation__list">
          {violation.nodes.map((node, index) => (
            <li key={`${node.selector}-${index}`}>
              <p className="violation__selector">
                <code>{truncateMiddle(node.selector, 64)}</code>
              </p>
              {node.failure_summary ? (
                <p className="violation__why">{node.failure_summary}</p>
              ) : null}
            </li>
          ))}
        </ul>
      </details>

      <p className="violation__link">
        <a href={violation.help_url} target="_blank" rel="noreferrer noopener">
          How to fix this
          {/* The destination and the new tab are both announced, not implied. */}
          <span className="visually-hidden"> (opens in a new tab, deque.com)</span>
        </a>
      </p>
    </li>
  )
}

function KeyboardCard({ keyboard }: { keyboard: KeyboardResult }): JSX.Element {
  return (
    <div className="keyboard-card">
      <h3 className="keyboard-card__title">The keyboard crawl</h3>
      <p className="keyboard-card__note">
        Detected by our own Tab crawl, not by axe. GreenAccess pressed Tab{' '}
        {keyboard.tabs_pressed} times and recorded where focus went.
      </p>

      <dl className="facts facts--inline">
        <div className="facts__row">
          <dt>Focus trap</dt>
          <dd>
            {keyboard.trap_detected ? (
              <>
                Detected
                {keyboard.trap_container ? (
                  <>
                    {' '}
                    in <code>{truncateMiddle(keyboard.trap_container, 40)}</code>
                  </>
                ) : null}
              </>
            ) : (
              'None detected'
            )}
          </dd>
        </div>
        <div className="facts__row">
          <dt>Elements reached</dt>
          <dd>{keyboard.reached_count}</dd>
        </div>
        <div className="facts__row">
          <dt>Interactive but unreachable</dt>
          <dd>{keyboard.unreachable_interactive_count}</dd>
        </div>
        <div className="facts__row">
          <dt>Focused with no visible indicator</dt>
          <dd>{keyboard.focus_visible_missing_count}</dd>
        </div>
      </dl>
    </div>
  )
}

export interface AccessibilityProps {
  a11y: A11yResult
  keyboard: KeyboardResult
}

export function Accessibility({ a11y, keyboard }: AccessibilityProps): JSX.Element {
  const grouped = IMPACTS.map((impact) => ({
    impact,
    violations: a11y.violations.filter((violation) => violation.impact === impact),
  })).filter((group) => group.violations.length > 0)

  return (
    <Scene
      id="accessibility"
      title="Who the page shuts out"
      lede={`Automated checks found ${pluralise(a11y.unique_rules, 'rule')} failing across ${pluralise(a11y.total_nodes, 'element')}. These are issues detected by a rule set — passing them all is not the same as being accessible.`}
      className="scene--data"
    >
      {grouped.length === 0 ? (
        <p className="empty">
          No violations were detected by the automated rule set. That is a good sign, not a
          guarantee: many accessibility barriers can only be found by a person.
        </p>
      ) : (
        <div className="violation-groups">
          {grouped.map((group) => (
            <section className="violation-group" key={group.impact} aria-labelledby={`impact-${group.impact}`}>
              <h3 className="violation-group__title" id={`impact-${group.impact}`}>
                {IMPACT_LABELS[group.impact]}
                <span className="violation-group__count">
                  {pluralise(group.violations.length, 'rule')}
                </span>
              </h3>
              {/* eslint-disable-next-line jsx-a11y/no-redundant-roles */}
              <ul className="violation-list" role="list">
                {group.violations.map((violation) => (
                  <ViolationCard key={violation.rule_id} violation={violation} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      <KeyboardCard keyboard={keyboard} />

      {a11y.incomplete_count > 0 ? (
        <p className="note note--block">
          {pluralise(a11y.incomplete_count, 'check')} could not be decided automatically and
          {a11y.incomplete_count === 1 ? ' needs' : ' need'} review by a person. These are not
          counted in the score either way.
        </p>
      ) : null}
    </Scene>
  )
}
