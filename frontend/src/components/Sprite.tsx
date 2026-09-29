/**
 * The voxel cast, as artwork.
 *
 * Single responsibility: place one sprite on the page at a known size.
 *
 * Every sprite is decorative and carries `alt=""`. That is deliberate rather
 * than lazy: each one sits beside text that already states whatever it
 * illustrates, and a screen reader announcing "voxel character holding a
 * magnifying glass" before every chapter heading would be noise, not
 * information.
 *
 * Two details are non-negotiable here, because the product detects both as
 * defects on other people's pages. Every sprite declares `width` and `height`,
 * so the browser reserves its box and the page does not shift as art loads.
 * And everything below the fold is `loading="lazy"`, so a reader who never
 * scrolls past the console never pays for the rest.
 */

import { SPRITES, type SpriteName } from './sprites'

export interface SpriteProps {
  name: SpriteName
  /** Rendered width in CSS pixels. Height follows the source aspect ratio. */
  width?: number
  className?: string
  /** Above-the-fold art loads eagerly; everything else waits. */
  eager?: boolean
}

export function Sprite({ name, width, className, eager = false }: SpriteProps): JSX.Element {
  const sprite = SPRITES[name]
  const w = width ?? sprite.width
  const h = Math.round((w / sprite.width) * sprite.height)

  return (
    <img
      className={className ? `sprite ${className}` : 'sprite'}
      src={sprite.src}
      alt=""
      width={w}
      height={h}
      loading={eager ? 'eager' : 'lazy'}
      decoding="async"
      draggable={false}
    />
  )
}
