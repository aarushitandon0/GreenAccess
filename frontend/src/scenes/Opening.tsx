/**
 * Chapter one: the opening, pinned.
 *
 * Single responsibility: state the argument GreenAccess exists to make, over a
 * landscape that is examined and then restored as the reader scrolls.
 *
 * It is its own component rather than a `StickyScene` because it carries the
 * document's `h1` in the pinned area, where the generic chapter puts an `h2`
 * among its panels. It uses the same primitives underneath.
 *
 * Nothing focusable lives in the pinned area. The headline dims as the analysis
 * begins, and a control that is invisible but still tabbable would be a real
 * defect, so the scan form is its own section immediately below.
 */

import { HudFrame, ScanGrid, SceneGrade, ScrollCue } from '../components/Hud'
import { StickyStep, type StickyStepContent } from '../components/StickyScene'
import { Terrain } from '../components/Terrain'
import { usePrefersReducedMotion } from '../lib/motion'
import { useStickyProgress } from '../lib/scroll'

/**
 * The argument, in three beats.
 *
 * Deliberately claim-free about numbers: these panels set up the measurement,
 * they do not report one. Every figure in this product comes from a scan.
 */
const STEPS: StickyStepContent[] = [
  {
    eyebrow: 'The weight',
    body: 'Every page view moves bytes, and moving bytes costs energy. Heavy images, autoplaying video and third-party scripts are paid for twice: once by the person waiting, and once by the grid.',
  },
  {
    eyebrow: 'The barrier',
    body: 'The same pages routinely fail the basics. Images without alternative text, inputs without labels, colour no one can read, keyboard traps that strand anyone not using a mouse.',
  },
  {
    eyebrow: 'One problem, not two',
    body: 'These are usually the same defect seen from two sides. Text baked into a banner image is both unreadable to a screen reader and far heavier than the sentence it hides. Fix it once and both scores move.',
  },
]

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
    >
      <div className="sticky-scene__pin">
        <div className="sticky-scene__figure">
          <Terrain />
        </div>

        <SceneGrade />

        {/* The blocky mask that clears outward as the scan runs. */}
        <ScanGrid />

        <HudFrame />

        <div className="hero-cine">
          <div className="hero-cine__inner">
            <p className="hero-cine__eyebrow">
              <span className="panel__bullet" aria-hidden="true" />
              Accessibility and carbon, measured together
            </p>
            <h1 className="hero-cine__title" id="opening-title">
              Pages cost more than they look.
            </h1>
            <p className="hero-cine__lede">
              GreenAccess scans one page for both what it weighs and who it shuts out, then shows
              where those two things are the same problem.
            </p>
          </div>
        </div>

        <ScrollCue />
      </div>

      <div className="sticky-scene__steps">
        {/*
          The headline gets the first screen to itself. Without this the first
          panel is already in the middle band on load and the two compete.
        */}
        <div className="scrolly-step scrolly-step--spacer" aria-hidden="true" />
        {STEPS.map((step, index) => (
          <StickyStep
            key={step.eyebrow}
            step={step}
            align={index % 2 === 0 ? 'right' : 'left'}
            reduced={reduced}
          />
        ))}
      </div>
    </section>
  )
}
