/**
 * The sapling: a tree that grows in proportion to a score.
 *
 * Single responsibility: draw one tree at a given stage of growth.
 *
 * It is decorative and marked `aria-hidden`. Every figure it stands for is
 * written in text beside it, so a reader who never sees the drawing loses
 * nothing — a rule that also keeps it out of the way of our own axe run.
 *
 * Growth is data, not decoration: `growth` is a 0-1 value taken from a real
 * score. The trunk is four stacked strokes of decreasing width, so it tapers
 * the way a real trunk does; branches and roots are drawn by advancing a dash
 * offset along each path; a leaf opens once growth passes its own threshold.
 * The same score always produces the same tree — nothing here is random.
 */

/** A stroked path with the growth window over which it draws. */
interface Stroke {
  d: string
  /** Growth at which it starts drawing, and at which it completes. */
  from: number
  to: number
  width: number
}

interface Leaf {
  x: number
  y: number
  rotate: number
  scale: number
  /** Growth at which this leaf is fully open. */
  at: number
}

/**
 * The trunk, in four segments from base to tip.
 *
 * Each is a separate stroke purely so the width can step down: a single path
 * would have to be one uniform thickness, which reads as a wire rather than a
 * trunk.
 */
const TRUNK: Stroke[] = [
  { d: 'M100 252 C99 232 97 218 98 204', from: 0, to: 0.12, width: 7 },
  { d: 'M98 204 C99 186 103 168 101 150', from: 0.1, to: 0.24, width: 5.4 },
  { d: 'M101 150 C100 132 103 114 100 96', from: 0.22, to: 0.34, width: 3.8 },
  { d: 'M100 96 C99 84 100 76 100 64', from: 0.32, to: 0.42, width: 2.6 },
]

const ROOTS: Stroke[] = [
  { d: 'M100 252 C88 254 78 258 68 264', from: 0.02, to: 0.16, width: 1.6 },
  { d: 'M100 252 C112 254 122 258 133 263', from: 0.04, to: 0.18, width: 1.6 },
  { d: 'M100 252 C97 258 95 263 94 270', from: 0.08, to: 0.22, width: 1.3 },
]

const BRANCHES: Stroke[] = [
  { d: 'M99 198 C86 190 76 184 60 176', from: 0.32, to: 0.48, width: 2.6 },
  { d: 'M99 186 C113 179 124 173 140 165', from: 0.36, to: 0.52, width: 2.6 },
  { d: 'M100 166 C88 158 79 152 66 144', from: 0.42, to: 0.58, width: 2.4 },
  { d: 'M101 150 C114 143 123 137 137 129', from: 0.46, to: 0.62, width: 2.4 },
  { d: 'M101 132 C91 125 84 120 73 112', from: 0.52, to: 0.68, width: 2.2 },
  { d: 'M100 116 C111 110 118 105 129 97', from: 0.56, to: 0.72, width: 2.2 },
  { d: 'M100 100 C93 95 88 91 80 85', from: 0.62, to: 0.76, width: 1.9 },
  { d: 'M100 86 C107 81 112 78 120 72', from: 0.66, to: 0.8, width: 1.9 },
]

/** Leaf clusters, one group per branch tip and a crown at the top. */
const LEAVES: Leaf[] = [
  { x: 56, y: 174, rotate: 190, scale: 1, at: 0.46 },
  { x: 64, y: 169, rotate: 232, scale: 0.9, at: 0.48 },
  { x: 52, y: 181, rotate: 160, scale: 0.85, at: 0.5 },
  { x: 66, y: 180, rotate: 205, scale: 0.8, at: 0.52 },

  { x: 143, y: 163, rotate: -12, scale: 1, at: 0.52 },
  { x: 135, y: 158, rotate: -48, scale: 0.9, at: 0.54 },
  { x: 146, y: 171, rotate: 18, scale: 0.85, at: 0.56 },
  { x: 133, y: 169, rotate: -5, scale: 0.8, at: 0.58 },

  { x: 62, y: 142, rotate: 192, scale: 0.95, at: 0.58 },
  { x: 70, y: 137, rotate: 234, scale: 0.85, at: 0.6 },
  { x: 58, y: 149, rotate: 162, scale: 0.8, at: 0.62 },

  { x: 140, y: 127, rotate: -14, scale: 0.95, at: 0.63 },
  { x: 132, y: 122, rotate: -50, scale: 0.85, at: 0.65 },
  { x: 143, y: 135, rotate: 16, scale: 0.8, at: 0.67 },

  { x: 69, y: 110, rotate: 194, scale: 0.9, at: 0.68 },
  { x: 77, y: 105, rotate: 236, scale: 0.82, at: 0.7 },
  { x: 66, y: 117, rotate: 164, scale: 0.78, at: 0.72 },

  { x: 132, y: 95, rotate: -16, scale: 0.9, at: 0.73 },
  { x: 124, y: 90, rotate: -52, scale: 0.82, at: 0.75 },
  { x: 135, y: 103, rotate: 14, scale: 0.78, at: 0.77 },

  { x: 76, y: 83, rotate: 196, scale: 0.85, at: 0.78 },
  { x: 84, y: 78, rotate: 238, scale: 0.75, at: 0.8 },

  { x: 123, y: 70, rotate: -18, scale: 0.85, at: 0.82 },
  { x: 115, y: 66, rotate: -54, scale: 0.75, at: 0.84 },

  // Crown
  { x: 100, y: 55, rotate: -90, scale: 0.9, at: 0.88 },
  { x: 91, y: 59, rotate: 205, scale: 0.82, at: 0.9 },
  { x: 109, y: 59, rotate: -25, scale: 0.82, at: 0.92 },
  { x: 94, y: 68, rotate: 215, scale: 0.75, at: 0.95 },
  { x: 106, y: 68, rotate: -35, scale: 0.75, at: 0.97 },
  { x: 100, y: 74, rotate: 270, scale: 0.7, at: 1 },
]

/** A single leaf blade, referenced by `<use>` so it ships once. */
const LEAF_PATH = 'M0 0 C5 -6.5 13.5 -6 16.5 0 C13.5 6 5 6.5 0 0 Z'

/** How long a leaf takes to open, in units of growth. */
const LEAF_WINDOW = 0.08

function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value
}

/**
 * How much of a stroke is drawn at this growth, 0-1.
 *
 * Paths carry `pathLength="1"`, so the dash offset is this figure directly and
 * does not depend on the path's real measured length.
 */
function drawn(stroke: Stroke, growth: number): number {
  const span = stroke.to - stroke.from
  if (span <= 0) {
    return growth >= stroke.to ? 1 : 0
  }
  return clamp01((growth - stroke.from) / span)
}

function StrokeGroup({ strokes, growth }: { strokes: Stroke[]; growth: number }): JSX.Element {
  return (
    <>
      {strokes.map((stroke, index) => {
        const progress = drawn(stroke, growth)
        if (progress <= 0) {
          return null
        }
        return (
          <path
            key={index}
            d={stroke.d}
            pathLength="1"
            strokeWidth={stroke.width}
            strokeDasharray="1"
            strokeDashoffset={1 - progress}
          />
        )
      })}
    </>
  )
}

export interface SaplingProps {
  /** 0-1. Typically a score divided by 100. */
  growth: number
  className?: string
}

export function Sapling({ growth, className }: SaplingProps): JSX.Element {
  const value = clamp01(growth)

  return (
    <svg
      className={className ? `sapling ${className}` : 'sapling'}
      viewBox="0 0 200 280"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <path id="ga-leaf" d={LEAF_PATH} />
      </defs>

      {/* A horizon for the tree to stand on, not a data mark. */}
      <path
        d="M24 252 C60 249 140 249 176 252"
        stroke="var(--border-strong)"
        strokeWidth="1"
        strokeLinecap="round"
        opacity="0.5"
      />

      <g stroke="var(--forest-strong)" strokeLinecap="round" opacity="0.45">
        <StrokeGroup strokes={ROOTS} growth={value} />
      </g>

      <g stroke="var(--forest-strong)" strokeLinecap="round">
        <StrokeGroup strokes={TRUNK} growth={value} />
        <StrokeGroup strokes={BRANCHES} growth={value} />
      </g>

      <g fill="var(--forest)">
        {LEAVES.map((leaf, index) => {
          const open = clamp01((value - (leaf.at - LEAF_WINDOW)) / LEAF_WINDOW)
          if (open <= 0.05) {
            return null
          }
          // Leaves open from just under half size rather than from nothing, so
          // a partly grown tree never shows a scatter of specks.
          const scale = leaf.scale * (0.45 + 0.55 * open)
          return (
            <use
              key={index}
              href="#ga-leaf"
              transform={`translate(${leaf.x} ${leaf.y}) rotate(${leaf.rotate}) scale(${scale.toFixed(3)})`}
              opacity={open.toFixed(3)}
            />
          )
        })}
      </g>
    </svg>
  )
}
