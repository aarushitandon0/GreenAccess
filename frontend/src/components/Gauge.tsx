/**
 * A score gauge: one 0-100 figure as a sweeping arc.
 *
 * Single responsibility: render one score, its label and its optional grade.
 *
 * Two accessibility decisions are load-bearing:
 *
 * - The arc is `aria-hidden`; the figure underneath it is real text. Assistive
 *   technology reads the number, not a description of a drawing.
 * - Colour never carries meaning on its own (WCAG 1.4.1). The band is named in
 *   text as well as coloured, and each of the three arc colours clears 3:1
 *   against the page background (WCAG 1.4.11), which is why the middle band
 *   uses `--amber-text-strong` rather than the light `--amber-fill`.
 */

import { useCountUp } from '../lib/motion'

/** A 270-degree arc, centred in a 140x140 box, opening at the bottom. */
const ARC = 'M31.82 108.18 A54 54 0 1 1 108.18 108.18'

/** The arc's colour and the word that names the band, by score. */
function band(score: number): { color: string; label: string } {
  if (score >= 70) return { color: 'var(--forest)', label: 'good' }
  if (score >= 40) return { color: 'var(--amber-text-strong)', label: 'needs work' }
  return { color: 'var(--danger)', label: 'poor' }
}

export interface GaugeProps {
  /** Accessible name, e.g. "Accessibility". */
  label: string
  /** The score, 0-100. */
  value: number
  /** Carbon's letter grade, shown beneath the figure when present. */
  grade?: string
  /** One line of context under the label, e.g. grams per view. */
  detail?: string
  /** Sweep the arc. Pass the scene's in-view state; ignored under reduced motion. */
  animate?: boolean
}

export function Gauge({ label, value, grade, detail, animate = false }: GaugeProps): JSX.Element {
  const rounded = Math.round(value)
  const displayed = useCountUp(rounded, animate, 700)
  const { color, label: bandLabel } = band(rounded)
  // The arc follows the animated value so the number and the sweep agree.
  const fraction = Math.max(0, Math.min(1, displayed / 100))

  return (
    <figure className="gauge">
      <div className="gauge__dial">
        <svg viewBox="0 0 140 140" fill="none" aria-hidden="true" focusable="false">
          <path
            d={ARC}
            stroke="var(--border)"
            strokeWidth="9"
            strokeLinecap="round"
            pathLength="1"
          />
          <path
            d={ARC}
            stroke={color}
            strokeWidth="9"
            strokeLinecap="round"
            pathLength="1"
            strokeDasharray="1"
            strokeDashoffset={1 - fraction}
          />
        </svg>
        <p className="gauge__value" aria-hidden="true">
          {Math.round(displayed)}
        </p>
      </div>

      <figcaption className="gauge__caption">
        <span className="gauge__label">{label}</span>
        {/*
          The one place the score is stated for assistive technology. The visual
          number above is hidden because it animates, and a live-updating
          number in the accessibility tree would be read over and over.
        */}
        <span className="gauge__reading">
          {rounded} out of 100
          <span className="gauge__band"> — {bandLabel}</span>
          {grade ? <span className="gauge__grade"> (grade {grade})</span> : null}
        </span>
        {detail ? <span className="gauge__detail">{detail}</span> : null}
      </figcaption>
    </figure>
  )
}
