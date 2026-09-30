/**
 * Ambience: the weather and light that keep a landscape alive.
 *
 * Single responsibility: the motion that is *not* driven by scrolling — clouds
 * crossing, light moving on the water, motes drifting over the land.
 *
 * The scroll-driven layers say what the chapter is arguing. This layer says the
 * place is real. Without it a landscape holds perfectly still between one
 * scroll and the next, which reads as a photograph however well the camera
 * moves over it; with it the scene keeps breathing while the reader stops to
 * read a beat.
 *
 * Every piece of it is on a loop rather than tied to `--p`, so it does not
 * compete with the argument for the reader's attention — nothing here arrives,
 * lands, or resolves. It drifts.
 *
 * The clouds are drawn rather than cut from the plates. The plates' own clouds
 * are 100 pixels across, so lifting one and scaling it up gives a grey box; the
 * same shape drawn as isometric faces stays crisp at any size, costs no network
 * request at all on a product that scores transfer weight, and lets the shading
 * be matched to the art exactly. The geometry is the artwork's own: a 2:1
 * isometric cube, a rhombus lid over two parallelogram walls.
 *
 * All of it is decorative and `aria-hidden`. None of it is animated under
 * reduced motion — ambient movement with no meaning attached is exactly what
 * that preference is asking to be spared.
 */

/** Half-width, lid height and wall depth of one cube, in SVG units. */
const CUBE = { w: 17, h: 9, d: 13 }

/** The faces of the artwork's white voxel cloud, lit from the upper left. */
const FACE = { top: '#eef2ef', left: '#ccd6d6', right: '#a9b5b7' }

/** One isometric cube, as its three visible faces. */
function Cube({ x, y, s }: { x: number; y: number; s: number }): JSX.Element {
  const w = CUBE.w * s
  const h = CUBE.h * s
  const d = CUBE.d * s

  return (
    <g transform={`translate(${x} ${y})`}>
      {/* Lid */}
      <polygon points={`0,${-h} ${w},0 0,${h} ${-w},0`} fill={FACE.top} />
      {/* Left wall */}
      <polygon points={`${-w},0 0,${h} 0,${h + d} ${-w},${d}`} fill={FACE.left} />
      {/* Right wall */}
      <polygon points={`${w},0 0,${h} 0,${h + d} ${w},${d}`} fill={FACE.right} />
    </g>
  )
}

/**
 * A voxel cloud: four cubes in a cluster.
 *
 * Drawn back to front by hand rather than sorted. A cube resting on top of
 * another has the smaller y but belongs in front of it, so ordering by depth
 * alone would put the stack back to front.
 */
export function VoxelCloud(): JSX.Element {
  return (
    <svg className="cloud__art" viewBox="-46 -30 92 56" aria-hidden="true" focusable="false">
      <Cube x={0} y={-2} s={1} />
      <Cube x={4} y={-15} s={0.62} />
      <Cube x={-22} y={7} s={0.8} />
      <Cube x={23} y={9} s={0.72} />
    </svg>
  )
}

export interface CloudDrift {
  /** Height in the frame, as a percentage of the scene. */
  y: number
  /** Width, as a percentage of the scene. */
  w: number
  /** Seconds for one crossing. */
  dur: number
  /**
   * Seconds of delay. Negative starts the cloud part-way across, which is what
   * keeps them from setting off in convoy on the first frame.
   */
  delay: number
  /** How far it also moves with the scroll, in vw. Nearer clouds move more. */
  depth: number
  /** How solid it is. Distance reads as haze. */
  alpha: number
}

export interface Glint {
  x: number
  y: number
  /** Width, as a percentage of the scene. */
  w: number
  dur: number
  delay: number
}

export interface Mote {
  x: number
  y: number
  /** Diameter, in pixels. */
  size: number
  dur: number
  delay: number
}

export interface AmbienceProps {
  clouds?: readonly CloudDrift[]
  /** Light moving on the water. */
  glints?: readonly Glint[]
  /** Pollen over the land. */
  motes?: readonly Mote[]
}

export function Ambience({
  clouds = [],
  glints = [],
  motes = [],
}: AmbienceProps): JSX.Element {
  return (
    <div className="ambience" aria-hidden="true">
      {glints.map((glint, index) => (
        <span
          className="glint"
          key={`glint-${index}`}
          style={
            {
              '--x': `${glint.x}%`,
              '--y': `${glint.y}%`,
              '--w': `${glint.w}%`,
              '--dur': `${glint.dur}s`,
              '--delay': `${glint.delay}s`,
            } as React.CSSProperties
          }
        />
      ))}

      {motes.map((mote, index) => (
        <span
          className="mote"
          key={`mote-${index}`}
          style={
            {
              '--x': `${mote.x}%`,
              '--y': `${mote.y}%`,
              '--s': `${mote.size}px`,
              '--dur': `${mote.dur}s`,
              '--delay': `${mote.delay}s`,
            } as React.CSSProperties
          }
        />
      ))}

      {clouds.map((cloud, index) => (
        <div
          className="cloud"
          key={`cloud-${index}`}
          style={
            {
              '--y': `${cloud.y}%`,
              '--w': `${cloud.w}%`,
              '--dur': `${cloud.dur}s`,
              '--delay': `${cloud.delay}s`,
              '--depth': cloud.depth,
              '--alpha': cloud.alpha,
            } as React.CSSProperties
          }
        >
          {/*
            The crossing lives on its own element. The cloud's parallax is a
            scroll-driven transform on the wrapper, and a keyframe on the same
            element would replace it rather than add to it.
          */}
          <div className="cloud__drift">
            <span className="cloud__shadow" />
            <VoxelCloud />
          </div>
        </div>
      ))}
    </div>
  )
}
