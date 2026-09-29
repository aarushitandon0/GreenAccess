/**
 * One chapter of the narrative.
 *
 * Single responsibility: give every chapter the same landmark, heading and
 * reveal behaviour, so the story reads as one document rather than a stack of
 * bespoke sections.
 *
 * The reveal is an enhancement and never a gate. The children are always in the
 * DOM, always in the accessibility tree and always found by find-in-page;
 * scrolling to a chapter changes only how it arrives. Under reduced motion, or
 * without `IntersectionObserver`, it is simply there.
 */

import { useInView } from '../lib/scroll'

export interface SceneProps {
  /** Anchor for the chapter nav; also ties the section to its heading. */
  id: string
  /** The chapter heading. Always an h2: the page has one h1, in the prologue. */
  title: string
  /** Optional standfirst under the heading. */
  lede?: string
  /** Extra class for chapter-specific layout. */
  className?: string
  children: React.ReactNode
}

export function Scene({ id, title, lede, className, children }: SceneProps): JSX.Element {
  const [ref, inView] = useInView<HTMLElement>({ threshold: 0.1, rootMargin: '0px 0px -10% 0px' })

  return (
    <section
      id={id}
      ref={ref}
      className={className ? `scene ${className}` : 'scene'}
      aria-labelledby={`${id}-title`}
      data-revealed={inView ? 'true' : 'false'}
    >
      <div className="container scene__inner">
        <header className="scene__header">
          <h2 className="scene__title" id={`${id}-title`}>
            {title}
          </h2>
          {lede ? <p className="scene__lede">{lede}</p> : null}
        </header>
        {children}
      </div>
    </section>
  )
}
