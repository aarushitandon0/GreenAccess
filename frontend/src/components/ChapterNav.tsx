/**
 * Sticky chapter navigation.
 *
 * Single responsibility: say where in the story the reader is, and let them
 * jump anywhere in it.
 *
 * This is what MASTERSPEC §13's tab bar becomes once the dashboard is told as
 * one continuous page: the same six destinations, but as in-page links rather
 * than tabs. That is deliberate — real links scroll, deep-link, open in a new
 * tab and work without JavaScript, none of which a tab widget does. It is a
 * `nav` landmark with a list of links, not a `tablist`, because that is what it
 * actually is; calling it a tablist would promise keyboard behaviour (arrow-key
 * roving focus) that in-page links neither have nor need.
 */

import { useEffect, useState } from 'react'

export interface Chapter {
  /** The target section's id. */
  id: string
  /** Short label for the nav. */
  label: string
}

export interface ChapterNavProps {
  chapters: Chapter[]
}

export function ChapterNav({ chapters }: ChapterNavProps): JSX.Element {
  const [activeId, setActiveId] = useState<string | null>(chapters[0]?.id ?? null)

  useEffect(() => {
    if (typeof window === 'undefined' || chapters.length === 0) {
      return
    }

    let frame = 0

    const measure = (): void => {
      frame = 0
      // The chapter the reader is reading is the last one whose top has passed
      // a line a third of the way down the viewport.
      const line = window.innerHeight / 3
      let current = chapters[0]?.id ?? null
      for (const chapter of chapters) {
        const element = document.getElementById(chapter.id)
        if (element !== null && element.getBoundingClientRect().top <= line) {
          current = chapter.id
        }
      }
      setActiveId(current)
    }

    const onScroll = (): void => {
      if (frame === 0) {
        frame = window.requestAnimationFrame(measure)
      }
    }

    measure()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll, { passive: true })
    return () => {
      if (frame !== 0) {
        window.cancelAnimationFrame(frame)
      }
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [chapters])

  return (
    <nav className="chapter-nav" aria-label="Chapters">
      <ul className="chapter-nav__list">
        {chapters.map((chapter) => {
          const current = chapter.id === activeId
          return (
            <li key={chapter.id}>
              <a
                className="chapter-nav__link"
                href={`#${chapter.id}`}
                // `aria-current="location"` is the right token for "this is the
                // part of the page you are in", as opposed to "page" for the
                // current page in a site nav.
                {...(current ? { 'aria-current': 'location' as const } : {})}
              >
                {chapter.label}
              </a>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
