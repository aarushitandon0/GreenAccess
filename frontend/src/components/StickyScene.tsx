/**
 * A pinned chapter: one figure held on screen while its text scrolls past.
 *
 * Single responsibility: the scrollytelling mechanic itself — pin a figure,
 * advance a progress variable across the chapter, and bring each text panel
 * forward as the reader reaches it.
 *
 * It does not scroll-jack. The page scrolls natively; `position: sticky` holds
 * the figure, and `useStickyProgress` only *reads* how far the container has
 * travelled. Nothing here calls `scrollTo`, intercepts the wheel, or changes
 * scroll speed — a reader keeps every habit and every assistive technology they
 * arrived with.
 *
 * Under reduced motion the progress variable rests at its finished value and
 * every panel sits at full opacity, so the chapter reads as a plain stack of
 * text over a still figure.
 *
 * Panels must not contain focusable elements. They dim as the reader moves past
 * them, and a control that is invisible but still tabbable is a genuine defect
 * (WCAG 2.4.7). Interactive content belongs in the chapters that are not pinned.
 */

import { HudFrame, ScrollCue } from './Hud'
import { usePrefersReducedMotion } from '../lib/motion'
import { useInView, useStickyProgress } from '../lib/scroll'

export interface StickyStepContent {
  /** Monospace kicker above the panel, e.g. "DAMAGED ECOSYSTEMS". */
  eyebrow: string
  /** The panel's prose. */
  body: string
}

/**
 * One text panel.
 *
 * Active only while it sits in the middle band of the viewport, which is what
 * makes panels hand over to each other rather than pile up.
 */
export function StickyStep({
  step,
  align,
  reduced,
}: {
  step: StickyStepContent
  align: 'left' | 'right'
  reduced: boolean
}): JSX.Element {
  const [ref, inView] = useInView<HTMLDivElement>({
    threshold: 0,
    rootMargin: '-38% 0px -38% 0px',
    repeat: true,
  })

  return (
    <div className="scrolly-step" data-scrolly-step ref={ref} data-align={align}>
      <div className="panel" data-active={reduced || inView ? 'true' : 'false'}>
        <p className="panel__eyebrow">
          <span className="panel__bullet" aria-hidden="true" />
          {step.eyebrow}
        </p>
        <p className="panel__body">{step.body}</p>
      </div>
    </div>
  )
}

export interface StickySceneProps {
  /** Anchor id, and the base for the heading's id. */
  id: string
  /** The chapter heading. Visually hidden when the figure carries the title. */
  title: string
  /** Hide the heading visually, for chapters whose figure is the headline. */
  hideTitle?: boolean
  /** What stays pinned. */
  figure: React.ReactNode
  /** The text panels, in reading order. */
  steps: StickyStepContent[]
  /** Extra class for chapter-specific styling. */
  className?: string
  /** Rendered inside the pinned area, above the figure. */
  overlay?: React.ReactNode
}

export function StickyScene({
  id,
  title,
  hideTitle = false,
  figure,
  steps,
  className,
  overlay,
}: StickySceneProps): JSX.Element {
  const reduced = usePrefersReducedMotion()
  const ref = useStickyProgress<HTMLElement>('--p', { reduced, restingValue: 1 })

  return (
    <section
      id={id}
      ref={ref}
      data-scope="cinema"
      className={className ? `sticky-scene ${className}` : 'sticky-scene'}
      aria-labelledby={`${id}-title`}
    >
      <div className="sticky-scene__pin">
        <div className="sticky-scene__figure">{figure}</div>
        <HudFrame />
        {overlay}
        <ScrollCue />
      </div>

      <div className="sticky-scene__steps">
        <h2 className={hideTitle ? 'visually-hidden' : 'sticky-scene__title'} id={`${id}-title`}>
          {title}
        </h2>
        {steps.map((step, index) => (
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
