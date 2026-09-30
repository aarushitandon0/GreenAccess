/**
 * Chapter one: the opening, pinned.
 *
 * Single responsibility: state the argument GreenAccess exists to make, over a
 * landscape that changes under it as the reader scrolls.
 *
 * The mechanic is the ordinary pinned scrollytell used by the rest of the
 * narrative: the figure is held by `position: sticky` while four beats of prose
 * scroll past it, and `useStickyProgress` reads — never controls — how far the
 * chapter has travelled. The page scrolls natively the whole time. Nothing here
 * calls `scrollTo`, swallows a wheel event, or changes scroll speed, so a
 * reader keeps every habit and every assistive technology they arrived with.
 *
 * Each beat is a real block in normal flow with a real heading, so with the
 * script gone the chapter is simply four headed passages over a still
 * landscape. Under reduced motion `--p` rests at 1 and every beat is active, so
 * it reads as that same plain stack.
 *
 * Nothing focusable lives in the pinned area. Beats dim as the reader moves
 * past them, and a control that is invisible but still tabbable would be a real
 * defect (WCAG 2.4.7), so the scan form is its own section immediately below.
 */

import beatBarrier from '../assets/scenes/beat-2-barrier.webp'
import beatBoth from '../assets/scenes/beat-3-both.webp'
import beatTradeoff from '../assets/scenes/beat-4-tradeoff.webp'
import beatWeight from '../assets/scenes/beat-1-weight.webp'
import { Backdrop, type BackdropPlate } from '../components/Backdrop'
import { HudFrame, ScanGrid, SceneGrade, ScrollCue } from '../components/Hud'
import { usePrefersReducedMotion } from '../lib/motion'
import { useInView, useStickyProgress } from '../lib/scroll'

interface Beat {
  /** Monospace kicker above the heading. */
  eyebrow: string
  /** The heading, up to the accented tail. */
  title: string
  /** The tail of the heading, set in the accent colour. */
  accent: string
  /** The standfirst under the heading. */
  lede: string
}

/**
 * The argument, in four beats.
 *
 * Deliberately claim-free about numbers: these beats set up the measurement,
 * they do not report one. Every figure in this product comes from a scan, and a
 * plausible-looking weight or score in the opening would be exactly the kind of
 * invented result the rest of the codebase refuses to print.
 */
const BEATS: readonly Beat[] = [
  {
    eyebrow: 'Accessibility and carbon, measured together',
    title: 'Pages cost more than they',
    accent: 'look.',
    lede: 'GreenAccess scans one page for both what it weighs and who it shuts out, then shows where those two things are the same problem.',
  },
  {
    eyebrow: 'One problem, not two',
    title: 'The same page can be hard to use and',
    accent: 'heavy to load.',
    lede: 'Images with no alternative text, video with no captions, third-party scripts nobody asked for, controls a keyboard cannot reach. One page fails a person and the grid in the same breath.',
  },
  {
    eyebrow: 'Same fix, two impacts',
    title: 'One change can help both',
    accent: 'sides.',
    lede: 'Stand a hero video down from autoplay, or lift the text back out of a banner image, and the page gets easier to use and lighter to load at once. GreenAccess measures both sides of every change it proposes.',
  },
  {
    eyebrow: 'Trade-offs, made clear',
    title: 'Every choice has a',
    accent: 'trade-off.',
    lede: 'Not every fix pulls the same way. Captions add bytes. A dark theme saves energy on some screens and none on others. Where the two goals disagree, GreenAccess says so instead of quietly choosing for you.',
  },
]

/** The landscape plates, one per beat, in the order the reader meets them. */
const PLATES: readonly BackdropPlate[] = [
  { id: 'weight', src: beatWeight },
  { id: 'barrier', src: beatBarrier },
  { id: 'both', src: beatBoth },
  { id: 'tradeoff', src: beatTradeoff },
]

/**
 * One beat of the opening.
 *
 * Active only while it sits in the middle band of the viewport, which is what
 * makes the beats hand over to each other rather than pile up. The first beat
 * carries the document's `h1`; the rest are `h2`s of the same chapter.
 */
function OpeningBeat({
  beat,
  index,
  reduced,
}: {
  beat: Beat
  index: number
  reduced: boolean
}): JSX.Element {
  const [ref, inView] = useInView<HTMLDivElement>({
    threshold: 0,
    rootMargin: '-34% 0px -34% 0px',
    repeat: true,
  })

  const Heading = index === 0 ? 'h1' : 'h2'

  return (
    <div className="beat" ref={ref} data-active={reduced || inView ? 'true' : 'false'}>
      <div className="beat__inner">
        <p className="beat__eyebrow">
          <span className="panel__bullet" aria-hidden="true" />
          {beat.eyebrow}
        </p>
        <Heading className="beat__title" id={index === 0 ? 'opening-title' : undefined}>
          {beat.title} <span className="beat__accent">{beat.accent}</span>
        </Heading>
        <p className="beat__lede">{beat.lede}</p>
      </div>
    </div>
  )
}

export function Opening(): JSX.Element {
  const reduced = usePrefersReducedMotion()
  const ref = useStickyProgress<HTMLElement>('--p', { reduced, restingValue: 1 })

  return (
    <section
      id="opening"
      ref={ref}
      data-scope="cinema"
      className="sticky-scene sticky-scene--opening"
      aria-labelledby="opening-title"
      style={{ '--beats': BEATS.length } as React.CSSProperties}
    >
      <div className="sticky-scene__pin">
        <div className="sticky-scene__figure">
          <Backdrop plates={PLATES} />
        </div>

        <SceneGrade />

        {/* The blocky aperture that draws in over the landscape and retreats. */}
        <ScanGrid />

        <HudFrame />

        <ScrollCue />
      </div>

      <div className="sticky-scene__steps">
        {BEATS.map((beat, index) => (
          <OpeningBeat key={beat.accent} beat={beat} index={index} reduced={reduced} />
        ))}
      </div>
    </section>
  )
}
