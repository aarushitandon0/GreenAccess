/**
 * The heads-up layer: the instrument framing around the narrative.
 *
 * Single responsibility: the non-content furniture of a pinned chapter —
 * corner brackets, the travel rail, the scroll cue and the analysis grid.
 *
 * All of it is `aria-hidden`. None of it carries information that is not also
 * written as text somewhere in the chapter, so a reader who never sees it
 * misses nothing. It exists to make the page feel like an instrument reading a
 * subject, which is what GreenAccess actually is.
 *
 * Everything animates by reading the `--p` custom property that
 * `useStickyProgress` writes on the chapter container. Nothing here subscribes
 * to scroll itself, and nothing re-renders as the reader moves.
 */

/** Corner brackets and the vertical travel rail. */
export function HudFrame(): JSX.Element {
  return (
    <div className="hud" aria-hidden="true">
      <span className="hud__corner hud__corner--tl" />
      <span className="hud__corner hud__corner--tr" />
      <span className="hud__corner hud__corner--bl" />
      <span className="hud__corner hud__corner--br" />

      {/* Travel rail: how far through this chapter the reader is. */}
      <span className="hud__rail">
        <span className="hud__rail-fill" />
      </span>

      {/* Registration ticks down both edges. */}
      <span className="hud__ticks hud__ticks--left">
        {Array.from({ length: 7 }, (_, index) => (
          <span className="hud__tick" key={index} />
        ))}
      </span>
      <span className="hud__ticks hud__ticks--right">
        {Array.from({ length: 7 }, (_, index) => (
          <span className="hud__tick" key={index} />
        ))}
      </span>
    </div>
  )
}

/**
 * The circular scroll cue, with an arc that fills as the chapter advances.
 *
 * The arc uses `pathLength="1"`, so the dash offset is the progress value
 * directly and needs no arithmetic about the circle's real circumference.
 */
export function ScrollCue({ label = 'SCROLL' }: { label?: string }): JSX.Element {
  return (
    <div className="cue" aria-hidden="true">
      <svg viewBox="0 0 100 100" className="cue__ring" fill="none">
        <circle cx="50" cy="50" r="46" stroke="var(--cue-track)" strokeWidth="1.5" />
        <circle
          className="cue__arc"
          cx="50"
          cy="50"
          r="46"
          stroke="var(--forest)"
          strokeWidth="2"
          pathLength="1"
          strokeDasharray="1"
          strokeLinecap="round"
        />
      </svg>
      <span className="cue__label">{label}</span>
    </div>
  )
}

/** Columns and rows in the analysis grid. */
const COLUMNS = 18
const ROWS = 10

/**
 * The analysis grid: a blocky frame that closes in from the edges and retreats.
 *
 * It is an aperture, not a blackout. The stylesheet derives a radius from the
 * chapter's progress; a cell is filled when it lies outside that radius. The
 * radius starts beyond the corners (nothing filled), draws in to leave only the
 * middle of the landscape showing, then opens back out — so the subject stays
 * visible throughout and what changes is how much of it is being masked off.
 *
 * Each cell carries only its distance from the centre, `--d`, fixed at build
 * time. All the animation is one radius in CSS, which means one custom-property
 * write per frame drives a hundred and eighty cells and React never re-renders.
 */
export function ScanGrid(): JSX.Element {
  const cells = []
  const centreX = (COLUMNS - 1) / 2
  const centreY = (ROWS - 1) / 2
  const maxDistance = Math.hypot(centreX, centreY)

  for (let row = 0; row < ROWS; row += 1) {
    for (let column = 0; column < COLUMNS; column += 1) {
      // Distance from the centre, 0 at the middle and 1 at the corners. The
      // stylesheet compares it against a radius that closes in and retreats.
      const distance = Math.hypot(column - centreX, row - centreY) / maxDistance
      cells.push(
        <span
          className="scan-grid__cell"
          key={`${row}-${column}`}
          style={{ '--d': distance.toFixed(3) } as React.CSSProperties}
        />,
      )
    }
  }

  return (
    <div
      className="scan-grid"
      aria-hidden="true"
      style={{ '--cols': COLUMNS, '--rows': ROWS } as React.CSSProperties}
    >
      {cells}
    </div>
  )
}
