/**
 * The backdrop: the voxel landscape behind the pinned opening.
 *
 * Single responsibility: hold a stack of landscape plates and hand the
 * stylesheet the two numbers it needs to cross-fade between them as the reader
 * scrolls.
 *
 * The plates are stacked, never swapped. Each one above the base fades *in*
 * over the one below and then stays opaque; none of them ever fades out. That
 * asymmetry is the whole trick: two layers cross-fading by opposite opacity
 * ramps let the background show through at the midpoint, which reads as a dip
 * in brightness on every transition. Fading in over an opaque layer cannot dip,
 * because the plate underneath is still at full strength the entire time.
 *
 * Nothing here re-renders while the reader scrolls. Each plate is given its
 * fade window once, as two custom properties, and the stylesheet resolves the
 * opacity from the chapter's `--p`. One property write per frame moves the
 * whole stack.
 *
 * The plates are decorative and `aria-hidden` by way of an empty `alt`: every
 * claim the opening makes is written as text beside them. The `Terrain` drawing
 * sits underneath as the base, so a plate that fails to load leaves a coherent
 * landscape rather than a black rectangle.
 */

import { Terrain } from './Terrain'

/** How much of the chapter a plate takes to fade in, as a fraction of `--p`. */
const FADE = 0.15

export interface BackdropPlate {
  /** The imported image URL. */
  src: string
  /** A stable key, also used in the DOM for debugging. */
  id: string
}

export interface BackdropProps {
  /** The plates, in the order the reader meets them. */
  plates: readonly BackdropPlate[]
}

export function Backdrop({ plates }: BackdropProps): JSX.Element {
  // Plate k is the one on screen when the k-th beat is centred, which happens
  // at p = k / (n - 1). Deriving the schedule here rather than hard-coding it
  // keeps the fades aligned with the beats if a beat is ever added or removed.
  const last = Math.max(plates.length - 1, 1)

  return (
    <div className="backdrop" aria-hidden="true">
      <Terrain className="backdrop__base" />

      {plates.map((plate, index) => {
        const centre = index / last
        return (
          <img
            key={plate.id}
            className="backdrop__plate"
            data-plate={plate.id}
            src={plate.src}
            alt=""
            decoding="async"
            /*
             * The first plate is the opening frame and blocks nothing behind
             * it, so it loads at normal priority. The rest are wanted before
             * the reader reaches them but never ahead of the text and fonts,
             * which is exactly what a low fetch priority asks for. They are
             * inside the pinned viewport from the first paint, so `lazy` would
             * not defer them anyway — it would only risk a plate arriving mid
             * cross-fade.
             */
            fetchPriority={index === 0 ? 'auto' : 'low'}
            style={
              {
                // Where this plate is fully on screen, and where its fade began.
                '--plate-in': (centre - FADE).toFixed(4),
                '--plate-full': centre.toFixed(4),
              } as React.CSSProperties
            }
          />
        )
      })}
    </div>
  )
}
