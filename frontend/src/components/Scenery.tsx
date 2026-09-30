/**
 * Scenery: the props that move inside a landscape, and where they belong.
 *
 * Single responsibility: describe what is planted on each landscape, when it
 * arrives, and how it drifts — and hand the stylesheet the numbers to animate
 * it from.
 *
 * This is what makes the opening a scrollytell rather than a slideshow. The
 * landscape plates are still pictures; the argument happens in what moves over
 * them. A laptop is planted on the island, the carbon it costs rises off it and
 * hangs there, and then on the far side of the chapter that same cloud sinks
 * away while a sprout and a turbine come up in its place. The reader scrolls
 * the argument rather than reading a caption about it.
 *
 * Every prop is decorative. Each one illustrates a sentence that is already
 * written as text in the beat beside it, so a reader who never sees any of this
 * loses nothing — which is why `Sprite` gives them all an empty `alt`.
 *
 * Nothing here re-renders while the reader scrolls. A prop is handed its
 * schedule once, as custom properties, and the stylesheet resolves its opacity
 * and transform from the chapter's `--p`. The idle bob is a keyframe on an
 * inner element so it composes with the scroll-driven transform instead of
 * fighting it.
 *
 * Positions are percentages of the scene box, tuned against the plates at
 * desktop width. They are deliberately forgiving: props sit on open water,
 * open grass and open sky, never pinned to a feature that has to line up
 * exactly, because `object-fit: cover` re-crops the plate at every aspect
 * ratio and a prop that had to hit a doorway would miss it.
 */

import { Sprite } from './Sprite'
import type { SpriteName } from './sprites'

export interface SceneProp {
  sprite: SpriteName
  /** Centre of the prop, as a percentage of the scene box. */
  x: number
  y: number
  /** Width, as a percentage of the scene box's width. */
  w: number
  /** Chapter progress by which it has fully arrived. */
  enter: number
  /** Chapter progress by which it has fully gone. Omit to let it stay. */
  exit?: number
  /** How far it travels across the whole chapter, in vw / vh. */
  driftX?: number
  driftY?: number
  /** Seconds for one idle bob. */
  bob?: number
  /** Seconds of delay on the bob, so a group does not move in lockstep. */
  bobDelay?: number
}

/** A prop's schedule, as the custom properties the stylesheet reads. */
function propVars(prop: SceneProp): React.CSSProperties {
  return {
    '--x': `${prop.x}%`,
    '--y': `${prop.y}%`,
    '--w': `${prop.w}%`,
    '--enter': prop.enter,
    // Past the end of the chapter, so a prop with no exit never starts leaving.
    '--exit': prop.exit ?? 9,
    '--drift-x': prop.driftX ?? 0,
    '--drift-y': prop.driftY ?? 0,
    '--bob': `${prop.bob ?? 6}s`,
    '--bob-delay': `${prop.bobDelay ?? 0}s`,
  } as React.CSSProperties
}

export function Scenery({
  props: sceneProps,
  eager = false,
}: {
  props: readonly SceneProp[]
  eager?: boolean
}): JSX.Element {
  return (
    <div className="scenery" aria-hidden="true">
      {sceneProps.map((prop, index) => (
        <span className="prop" key={`${prop.sprite}-${index}`} style={propVars(prop)}>
          <Sprite name={prop.sprite} className="prop__art" eager={eager} />
        </span>
      ))}
    </div>
  )
}
