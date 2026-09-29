/**
 * One chapter of the narrative.
 *
 * Single responsibility: give every chapter the same landmark, heading, layout
 * and reveal behaviour, so the story reads as one document rather than a stack
 * of bespoke sections.
 *
 * The layout is a sticky rail beside a wide content column. The rail holds the
 * heading, the standfirst and the chapter's voxel motif, and stays put while
 * the content scrolls past it. That is the point twice over: it keeps the
 * reader oriented — you can always see which chapter you are in — and it puts
 * the left third of a wide screen to work instead of leaving a centred column
 * marooned in white space. Below the breakpoint it simply stacks.
 *
 * The reveal is an enhancement and never a gate. The children are always in the
 * DOM, always in the accessibility tree and always found by find-in-page;
 * scrolling to a chapter changes only how it arrives. Under reduced motion, or
 * without `IntersectionObserver`, it is simply there.
 */

import { Sprite } from './Sprite'
import type { SpriteName } from './sprites'
import { useInView } from '../lib/scroll'

export interface SceneProps {
  /** Anchor for the chapter nav; also ties the section to its heading. */
  id: string
  /** The chapter heading. Always an h2: the page has one h1, in the opening. */
  title: string
  /** Monospace kicker above the heading. */
  eyebrow?: string
  /** Optional standfirst under the heading. */
  lede?: string
  /** A sprite for the rail, giving the chapter a face. */
  motif?: SpriteName
  /** Extra class for chapter-specific layout. */
  className?: string
  children: React.ReactNode
}

export function Scene({
  id,
  title,
  eyebrow,
  lede,
  motif,
  className,
  children,
}: SceneProps): JSX.Element {
  const [ref, inView] = useInView<HTMLElement>({ threshold: 0.05, rootMargin: '0px 0px -8% 0px' })

  return (
    <section
      id={id}
      ref={ref}
      className={className ? `scene ${className}` : 'scene'}
      aria-labelledby={`${id}-title`}
      data-revealed={inView ? 'true' : 'false'}
    >
      <div className="scene__grid">
        <div className="scene__rail">
          <div className="scene__rail-inner">
            {eyebrow ? (
              <p className="scene__eyebrow">
                <span className="panel__bullet" aria-hidden="true" />
                {eyebrow}
              </p>
            ) : null}
            <h2 className="scene__title" id={`${id}-title`}>
              {title}
            </h2>
            {lede ? <p className="scene__lede">{lede}</p> : null}
            {motif ? <Sprite name={motif} width={176} className="sprite--motif" /> : null}
          </div>
        </div>

        <div className="scene__body">{children}</div>
      </div>
    </section>
  )
}
