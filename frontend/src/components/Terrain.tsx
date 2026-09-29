/**
 * The terrain: a procedurally drawn aerial landscape.
 *
 * Single responsibility: draw the backdrop the narrative's pinned chapters
 * transform as the reader scrolls.
 *
 * It is drawn rather than photographed, and that is the point three times over.
 * A product that scores pages on transfer weight should not open with a
 * megabyte of hero photography. It is deterministic — one seed, one landscape,
 * every reload — so the demo never shifts underfoot. And because it is live SVG
 * rather than pixels, the scan chapter can actually operate on it: desaturate
 * it, tile it, mark it up.
 *
 * The organic coastlines come from `feTurbulence` displacing clean geometry,
 * so the whole landmass costs a few hundred bytes of path data and no network
 * request at all. It is decorative and `aria-hidden`; every fact the narrative
 * states around it is text.
 */

/**
 * A small deterministic PRNG (mulberry32).
 *
 * Seeded and self-contained so the landscape is identical on every render, on
 * every machine. `Math.random` would reshape the coastline on each reload and
 * make a screenshot test meaningless.
 */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * A closed organic blob as a cubic path.
 *
 * Built from points on a wobbling circle, joined with a Catmull-Rom-style
 * smoothing so the outline has no corners. `feTurbulence` roughens it further
 * at render time; starting from a smooth shape keeps that roughness looking
 * like a coastline rather than like noise.
 */
function blob(
  cx: number,
  cy: number,
  radius: number,
  points: number,
  wobble: number,
  rand: () => number,
): string {
  const nodes: Array<[number, number]> = []
  for (let i = 0; i < points; i += 1) {
    const angle = (i / points) * Math.PI * 2
    const r = radius * (1 - wobble / 2 + rand() * wobble)
    nodes.push([cx + Math.cos(angle) * r, cy + Math.sin(angle) * r * 0.82])
  }

  let path = `M${nodes[0]![0].toFixed(1)} ${nodes[0]![1].toFixed(1)}`
  for (let i = 0; i < nodes.length; i += 1) {
    const current = nodes[i]!
    const next = nodes[(i + 1) % nodes.length]!
    const after = nodes[(i + 2) % nodes.length]!
    // Control points from the neighbouring nodes: a cheap smooth interpolation.
    const c1x = current[0] + (next[0] - current[0]) * 0.55
    const c1y = current[1] + (next[1] - current[1]) * 0.55
    const c2x = next[0] - (after[0] - current[0]) * 0.16
    const c2y = next[1] - (after[1] - current[1]) * 0.16
    path += ` C${c1x.toFixed(1)} ${c1y.toFixed(1)} ${c2x.toFixed(1)} ${c2y.toFixed(1)} ${next[0].toFixed(1)} ${next[1].toFixed(1)}`
  }
  return `${path} Z`
}

/** A winding channel across the map, as a smooth open path. */
function channel(
  start: [number, number],
  end: [number, number],
  segments: number,
  drift: number,
  rand: () => number,
): string {
  const nodes: Array<[number, number]> = [start]
  for (let i = 1; i < segments; i += 1) {
    const t = i / segments
    const x = start[0] + (end[0] - start[0]) * t + (rand() - 0.5) * drift
    const y = start[1] + (end[1] - start[1]) * t + (rand() - 0.5) * drift
    nodes.push([x, y])
  }
  nodes.push(end)

  let path = `M${nodes[0]![0].toFixed(1)} ${nodes[0]![1].toFixed(1)}`
  for (let i = 1; i < nodes.length; i += 1) {
    const previous = nodes[i - 1]!
    const current = nodes[i]!
    const midX = (previous[0] + current[0]) / 2
    const midY = (previous[1] + current[1]) / 2
    path += ` Q${previous[0].toFixed(1)} ${previous[1].toFixed(1)} ${midX.toFixed(1)} ${midY.toFixed(1)}`
  }
  return path
}

/** The landscape, built once at module load because the seed never changes. */
const LANDSCAPE = (() => {
  const rand = mulberry32(20260930)

  const islands = [
    blob(980, 250, 330, 13, 0.42, rand),
    blob(1180, 640, 300, 12, 0.46, rand),
    blob(700, 720, 260, 12, 0.5, rand),
    blob(1340, 120, 240, 11, 0.44, rand),
    blob(860, 480, 190, 11, 0.52, rand),
  ]

  const channels = [
    channel([620, 60], [1180, 880], 9, 190, rand),
    channel([900, 0], [1440, 520], 7, 150, rand),
    channel([1080, 380], [1440, 760], 6, 120, rand),
  ]

  const creeks = Array.from({ length: 14 }, () => {
    const x = 560 + rand() * 880
    const y = rand() * 900
    return channel([x, y], [x + (rand() - 0.5) * 300, y + (rand() - 0.5) * 300], 4, 60, rand)
  })

  return { islands, channels, creeks }
})()

export interface TerrainProps {
  className?: string
}

export function Terrain({ className }: TerrainProps): JSX.Element {
  return (
    <svg
      className={className ? `terrain ${className}` : 'terrain'}
      viewBox="0 0 1440 900"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        {/*
          The coastline roughener. One turbulence field displaces the whole
          landmass group at once, so shore, canopy and creeks all distort
          together and stay registered with each other.
        */}
        {/*
          Octave counts are kept low on purpose. Each extra octave is another
          full pass of noise over the whole viewport, and the coastline reads
          the same at two as at four -- it is being displaced, not inspected.
        */}
        <filter id="ga-coast" x="-15%" y="-15%" width="130%" height="130%">
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.005 0.008"
            numOctaves="2"
            seed="11"
            result="noise"
          />
          <feDisplacementMap
            in="SourceGraphic"
            in2="noise"
            scale="86"
            xChannelSelector="R"
            yChannelSelector="G"
          />
        </filter>

        {/* Fine mottling, used as a canopy texture over the land fill. */}
        <filter id="ga-canopy" x="0%" y="0%" width="100%" height="100%">
          <feTurbulence type="fractalNoise" baseFrequency="0.7" numOctaves="2" seed="5" />
          <feColorMatrix
            type="matrix"
            values="0 0 0 0 0.05
                    0 0 0 0 0.16
                    0 0 0 0 0.09
                    0 0 0 -1.1 0.85"
          />
        </filter>

        {/* Broad haze, to keep the water from reading as flat colour. */}
        <filter id="ga-haze" x="0%" y="0%" width="100%" height="100%">
          <feTurbulence type="fractalNoise" baseFrequency="0.004" numOctaves="2" seed="19" />
          <feColorMatrix
            type="matrix"
            values="0 0 0 0 0.42
                    0 0 0 0 0.56
                    0 0 0 0 0.55
                    0 0 0 -0.6 0.34"
          />
        </filter>

        <clipPath id="ga-land-clip">
          {LANDSCAPE.islands.map((d, index) => (
            <path key={index} d={d} />
          ))}
        </clipPath>
      </defs>

      {/* Water */}
      <rect width="1440" height="900" fill="var(--terrain-water)" />
      <rect width="1440" height="900" filter="url(#ga-haze)" />

      <g filter="url(#ga-coast)">
        {/* Sandy shoreline: the same islands, stroked wide beneath the land. */}
        <g stroke="var(--terrain-sand)" strokeWidth="34" fill="var(--terrain-sand)">
          {LANDSCAPE.islands.map((d, index) => (
            <path key={index} d={d} />
          ))}
        </g>

        {/* Vegetation */}
        <g fill="var(--terrain-land)">
          {LANDSCAPE.islands.map((d, index) => (
            <path key={index} d={d} />
          ))}
        </g>

        <g clipPath="url(#ga-land-clip)">
          <rect width="1440" height="900" filter="url(#ga-canopy)" />
          {/* Creeks cutting into the canopy, clipped so they stay on land. */}
          <g
            stroke="var(--terrain-sand)"
            strokeWidth="5"
            fill="none"
            opacity="0.5"
            strokeLinecap="round"
          >
            {LANDSCAPE.creeks.map((d, index) => (
              <path key={index} d={d} />
            ))}
          </g>
        </g>

        {/* The main channels, cut through everything. */}
        <g fill="none" strokeLinecap="round">
          {LANDSCAPE.channels.map((d, index) => (
            <g key={index}>
              <path d={d} stroke="var(--terrain-sand)" strokeWidth="58" opacity="0.85" />
              <path d={d} stroke="var(--terrain-water)" strokeWidth="38" />
            </g>
          ))}
        </g>
      </g>
    </svg>
  )
}
