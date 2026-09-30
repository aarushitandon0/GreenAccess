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

import plateBoth from '../assets/scenes/beat-3-both.webp'
import plateWeight from '../assets/scenes/beat-1-weight.webp'
import { Backdrop, type BackdropScene } from '../components/Backdrop'
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

/**
 * The two landscapes, and what happens on them.
 *
 * The first is the page as it stands: a laptop is planted on the island, the
 * carbon it costs rises off it, and the machines serving it appear behind. The
 * second is the same argument answered: that cloud sinks and clears while a
 * sprout, a turbine and a tree come up on the far bank. The beats say it in
 * words; the scenery says it in the same order, at the same moments.
 *
 * The schedule is in chapter progress, where 0 is the moment the landscape
 * pins and 1 the moment it lets go. The four beats are centred at 0, 1/3, 2/3
 * and 1, so a prop keyed to 0.24 lands while the second beat is being read.
 */
const SCENES: readonly BackdropScene[] = [
  {
    id: 'weight',
    plate: plateWeight,
    from: 0,
    span: 0.55,
    fadeIn: 0,
    zoomFrom: 1.22,
    zoomBy: -0.13,
    panX: 5,
    panY: 2.5,
    ambience: {
      // Weather crosses right to left over the whole map. The three depths are
      // what stop it reading as one sheet of cloud sliding past: the low one
      // is bigger, faster and more solid, the high one small, slow and hazy.
      clouds: [
        { y: 12, w: 11, dur: 96, delay: -20, depth: 2.6, alpha: 0.92 },
        { y: 47, w: 7.5, dur: 132, delay: -74, depth: 1.5, alpha: 0.7 },
        { y: 74, w: 13, dur: 78, delay: -46, depth: 3.4, alpha: 0.96 },
      ],
      // Light on the open water down the middle of the map.
      glints: [
        { x: 42, y: 52, w: 16, dur: 13, delay: 0 },
        { x: 58, y: 72, w: 12, dur: 17, delay: -6 },
        { x: 30, y: 30, w: 10, dur: 19, delay: -11 },
      ],
      // Pollen over the islands either side of the channel.
      motes: [
        { x: 78, y: 44, size: 4, dur: 11, delay: 0 },
        { x: 85, y: 56, size: 3, dur: 14, delay: -5 },
        { x: 73, y: 62, size: 3, dur: 9, delay: -3 },
        { x: 90, y: 38, size: 4, dur: 16, delay: -8 },
        { x: 52, y: 22, size: 3, dur: 12, delay: -2 },
        { x: 62, y: 84, size: 4, dur: 15, delay: -9 },
      ],
    },
    props: [
      // Beat one: the page itself, planted on the island by the signposts.
      { sprite: 'iso-laptop', x: 82, y: 50, w: 13, enter: 0.04, driftY: -1, bob: 6.5 },
      // Beat two: the weight nobody sees, rising off it and drifting away over
      // the water, and the machines on the far bank that serve it.
      { sprite: 'iso-co2', x: 70, y: 31, w: 11, enter: 0.23, driftY: -6, bob: 5, bobDelay: 0.6 },
      { sprite: 'iso-server', x: 50, y: 15, w: 11, enter: 0.31, driftY: -1.5, bob: 7, bobDelay: 1.2 },
    ],
  },
  {
    id: 'both',
    plate: plateBoth,
    from: 0.45,
    span: 0.55,
    fadeIn: 0.55,
    zoomFrom: 1.18,
    zoomBy: -0.11,
    panX: -5,
    panY: 1.5,
    ambience: {
      clouds: [
        { y: 8, w: 9, dur: 104, delay: -58, depth: 2.2, alpha: 0.85 },
        { y: 40, w: 12, dur: 84, delay: -12, depth: 3.1, alpha: 0.95 },
        { y: 82, w: 7, dur: 140, delay: -96, depth: 1.4, alpha: 0.66 },
      ],
      glints: [
        { x: 46, y: 60, w: 15, dur: 15, delay: 0 },
        { x: 30, y: 78, w: 13, dur: 12, delay: -7 },
        { x: 60, y: 34, w: 10, dur: 18, delay: -4 },
      ],
      // Over the green bank, where the flowers are.
      motes: [
        { x: 70, y: 66, size: 4, dur: 10, delay: 0 },
        { x: 80, y: 74, size: 3, dur: 13, delay: -6 },
        { x: 88, y: 60, size: 4, dur: 16, delay: -2 },
        { x: 64, y: 48, size: 3, dur: 12, delay: -9 },
        { x: 76, y: 86, size: 3, dur: 14, delay: -4 },
        { x: 92, y: 80, size: 4, dur: 9, delay: -7 },
      ],
    },
    props: [
      // The heavy bank at the near end of the bridge, which clears as the fix
      // lands. Kept to the quay at the city's right edge rather than out over
      // its middle: the prose sits across the left of the frame, and a prop
      // that has to be read cannot be placed under it.
      { sprite: 'iso-server', x: 38, y: 23, w: 11, enter: 0.5, exit: 0.8, bob: 7 },
      {
        sprite: 'iso-co2',
        x: 46,
        y: 17,
        w: 11,
        enter: 0.52,
        exit: 0.79,
        driftY: -3,
        bob: 5,
        bobDelay: 0.4,
      },
      // The lighter bank across the water, coming up in their place: a sprout
      // beside the sign as the third beat lands, a turbine on the far headland
      // as the fourth does.
      { sprite: 'iso-sprout', x: 66, y: 56, w: 9, enter: 0.68, driftY: -1, bob: 5, bobDelay: 0.9 },
      { sprite: 'iso-turbine', x: 84, y: 76, w: 11, enter: 0.8, driftY: -1, bob: 7, bobDelay: 0.3 },
    ],
  },
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
          <Backdrop scenes={SCENES} />
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
