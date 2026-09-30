/**
 * The backdrop: the landscapes behind the pinned opening, and the camera on
 * them.
 *
 * Single responsibility: stack the scenes, and hand the stylesheet the numbers
 * it needs to move a camera over each one and cross-fade between them.
 *
 * A scene is a landscape plate plus the scenery that moves on it, held in one
 * box so that a single camera transform carries both. That is why the props
 * stay put on the water and the grass as the view pushes in: they are being
 * moved by the same transform as the ground under them, not animated to chase
 * it.
 *
 * There are two scenes, not one per beat. Four stills cross-fading is a
 * slideshow; two landscapes that are actually travelled over, with the argument
 * being built out of props on top of them, is a scrollytell. Each scene owns
 * half the chapter and gets a real camera move across it — a pan of several
 * viewport percent and a fifth of its scale — rather than the token push-in a
 * still can get away with.
 *
 * The second scene fades in over the first and then stays. Two layers
 * cross-fading on opposite opacity ramps let the background show through at
 * the midpoint, which reads as a dip in brightness; fading in over a layer
 * that is still fully opaque cannot dip.
 *
 * Nothing here re-renders while the reader scrolls. Each scene is given its
 * schedule once and the stylesheet resolves everything from `--p`.
 */

import { Ambience, type AmbienceProps } from './Ambience'
import { Scenery, type SceneProp } from './Scenery'
import { Terrain } from './Terrain'

export interface BackdropScene {
  /** Stable id, also used to hang the per-plate crop off in CSS. */
  id: string
  /** The landscape plate. */
  plate: string
  /** Chapter progress at which this scene's camera move starts. */
  from: number
  /** How much of the chapter that move spans. */
  span: number
  /** Chapter progress at which the scene has finished fading in. */
  fadeIn: number
  /** Scale at the start of the move, and how much it changes by the end. */
  zoomFrom: number
  zoomBy: number
  /** Total pan across the move, in vw / vh. */
  panX: number
  panY: number
  /** What moves on this landscape with the scroll. */
  props: readonly SceneProp[]
  /** What moves on it regardless of the scroll: weather, light, pollen. */
  ambience?: AmbienceProps
}

export interface BackdropProps {
  scenes: readonly BackdropScene[]
}

/**
 * How long a scene takes to fade in, as a fraction of chapter progress.
 *
 * Kept short on purpose. The two landscapes are different geometry, so a long
 * dissolve puts two unrelated coastlines on screen at once and reads as a
 * muddle rather than as a transition.
 */
const FADE = 0.09

export function Backdrop({ scenes }: BackdropProps): JSX.Element {
  return (
    <div className="backdrop" aria-hidden="true">
      {/* The drawn landscape underneath: if a plate never arrives, the chapter
          still has a coherent backdrop rather than a flat rectangle. */}
      <Terrain className="backdrop__base" />

      {scenes.map((scene, index) => (
        <div
          className="backdrop__scene"
          key={scene.id}
          data-scene={scene.id}
          style={
            {
              '--from': scene.from,
              '--span': scene.span,
              '--zoom-from': scene.zoomFrom,
              '--zoom-by': scene.zoomBy,
              '--pan-x': scene.panX,
              '--pan-y': scene.panY,
              // The first scene is the opening frame and is opaque from the
              // start; the rest fade in over whatever is already there.
              '--scene-in': index === 0 ? -1 : scene.fadeIn - FADE,
            } as React.CSSProperties
          }
        >
          <img
            className="backdrop__plate"
            src={scene.plate}
            alt=""
            decoding="async"
            /*
             * The opening plate blocks nothing behind it, so it loads at normal
             * priority. The second is wanted before the reader reaches it but
             * never ahead of the text and fonts, which is what a low fetch
             * priority asks for. Both are inside the pinned viewport from the
             * first paint, so `lazy` would not defer either — it would only
             * risk one arriving mid cross-fade.
             */
            fetchPriority={index === 0 ? 'auto' : 'low'}
          />

          {scene.ambience ? <Ambience {...scene.ambience} /> : null}

          <Scenery props={scene.props} eager={index === 0} />
        </div>
      ))}
    </div>
  )
}
