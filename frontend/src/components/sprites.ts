/**
 * The sprite manifest.
 *
 * Single responsibility: name every piece of artwork and record its intrinsic
 * size, so a caller never has to guess an aspect ratio.
 *
 * The sizes are baked in at build time rather than measured at runtime,
 * because they exist to reserve the element box *before* the image arrives.
 * Measuring them late would defeat the point and reintroduce layout shift --
 * one of the very defects this product reports on other pages.
 *
 * Imports go through Vite, so each file is content-hashed and cache-busted.
 */

import CHAR_ANALYSE from '../assets/voxel/char-analyse.webp'
import CHAR_BUILD from '../assets/voxel/char-build.webp'
import CHAR_FIX from '../assets/voxel/char-fix.webp'
import CHAR_MEASURE from '../assets/voxel/char-measure.webp'
import CHAR_SCAN from '../assets/voxel/char-scan.webp'
import CHAR_TRADEOFFS from '../assets/voxel/char-tradeoffs.webp'
import ISO_CLOUD from '../assets/voxel/iso-cloud.webp'
import ISO_CO2 from '../assets/voxel/iso-co2.webp'
import ISO_GLOBE from '../assets/voxel/iso-globe.webp'
import ISO_LAPTOP from '../assets/voxel/iso-laptop.webp'
import ISO_SERVER from '../assets/voxel/iso-server.webp'
import ISO_SPROUT from '../assets/voxel/iso-sprout.webp'
import ISO_SUN from '../assets/voxel/iso-sun.webp'
import ISO_TREE from '../assets/voxel/iso-tree.webp'
import ISO_TURBINE from '../assets/voxel/iso-turbine.webp'

export interface SpriteAsset {
  src: string
  /** Intrinsic pixel size, used to reserve the element's box. */
  width: number
  height: number
}

export const SPRITES = {
  'char-analyse': { src: CHAR_ANALYSE, width: 210, height: 207 },
  'char-build': { src: CHAR_BUILD, width: 380, height: 231 },
  'char-fix': { src: CHAR_FIX, width: 309, height: 216 },
  'char-measure': { src: CHAR_MEASURE, width: 253, height: 202 },
  'char-scan': { src: CHAR_SCAN, width: 191, height: 211 },
  'char-tradeoffs': { src: CHAR_TRADEOFFS, width: 222, height: 208 },
  'iso-cloud': { src: ISO_CLOUD, width: 146, height: 139 },
  'iso-co2': { src: ISO_CO2, width: 132, height: 155 },
  'iso-globe': { src: ISO_GLOBE, width: 199, height: 139 },
  'iso-laptop': { src: ISO_LAPTOP, width: 143, height: 139 },
  'iso-server': { src: ISO_SERVER, width: 164, height: 177 },
  'iso-sprout': { src: ISO_SPROUT, width: 108, height: 155 },
  'iso-sun': { src: ISO_SUN, width: 113, height: 114 },
  'iso-tree': { src: ISO_TREE, width: 154, height: 160 },
  'iso-turbine': { src: ISO_TURBINE, width: 128, height: 164 },
} as const satisfies Record<string, SpriteAsset>

export type SpriteName = keyof typeof SPRITES
