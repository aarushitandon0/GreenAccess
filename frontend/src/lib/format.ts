/**
 * Display formatting for scan figures.
 *
 * Single responsibility: turn a number from the API into the string the UI
 * shows. Pure functions only — no React, no DOM — so they are table-testable.
 *
 * Two rules run through all of it. Carbon figures are estimates and are
 * labelled as such wherever they appear (MASTERSPEC §13), and no function here
 * invents precision the model does not have.
 */

/** Binary units, matching how transfer size is reported by browser tooling. */
const BYTE_UNITS = ['B', 'KB', 'MB', 'GB'] as const

/**
 * Transfer size, e.g. `1.4 MB`.
 *
 * Bytes and kilobytes are whole numbers; megabytes and above keep one decimal,
 * which is the precision the page-weight figures actually carry.
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return '0 B'
  }

  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024
    unit += 1
  }

  const digits = unit >= 2 ? 1 : 0
  return `${value.toFixed(digits)} ${BYTE_UNITS[unit]}`
}

/**
 * A signed byte delta, e.g. `-1.4 MB`.
 *
 * The sign is the point: these appear on before/after chips where a reader
 * needs to see direction before magnitude.
 */
export function formatByteDelta(bytes: number): string {
  if (bytes === 0) {
    return '0 B'
  }
  const sign = bytes > 0 ? '+' : '-'
  return `${sign}${formatBytes(Math.abs(bytes))}`
}

/**
 * Grams of CO2 per view, e.g. `0.42 g`.
 *
 * Sub-milligram values would read as `0.00 g`, so they get three decimals
 * rather than being rounded away to a figure that looks like zero emissions.
 */
export function formatGrams(grams: number): string {
  if (!Number.isFinite(grams) || grams <= 0) {
    return '0 g'
  }
  return grams < 0.01 ? `${grams.toFixed(3)} g` : `${grams.toFixed(2)} g`
}

/** A signed grams delta for before/after chips, e.g. `-0.83 g`. */
export function formatGramsDelta(grams: number): string {
  if (grams === 0) {
    return '0 g'
  }
  const sign = grams > 0 ? '+' : '-'
  return `${sign}${formatGrams(Math.abs(grams))}`
}

/** A signed whole-number delta for scores and counts, e.g. `+47`. */
export function formatCountDelta(count: number): string {
  return count > 0 ? `+${count}` : `${count}`
}

/**
 * Percentage reduction from `before` to `after`, e.g. `73%`.
 *
 * Returns null when there is nothing to compare against: a reduction from zero
 * is undefined, not 0% and not 100%.
 */
export function formatReduction(before: number, after: number): string | null {
  if (!Number.isFinite(before) || before <= 0) {
    return null
  }
  const reduction = ((before - after) / before) * 100
  return `${Math.round(reduction)}%`
}

/** Whole milliseconds under a second, then seconds, e.g. `840 ms`, `2.3 s`. */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) {
    return '0 ms'
  }
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
}

/** A count with its noun pluralised, e.g. `1 issue`, `12 issues`. */
export function pluralise(count: number, singular: string, plural?: string): string {
  const word = count === 1 ? singular : (plural ?? `${singular}s`)
  return `${count} ${word}`
}

/**
 * The host of a URL, for display in a list of third parties.
 *
 * Falls back to the raw string: these values come from the page under scan, so
 * a malformed one must degrade rather than throw.
 */
export function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/**
 * A URL shortened to fit a table cell, keeping the end.
 *
 * The tail is what distinguishes one asset from another, so the middle is what
 * gets dropped.
 */
export function truncateMiddle(text: string, max = 48): string {
  if (text.length <= max) {
    return text
  }
  const head = Math.ceil((max - 1) / 2)
  const tail = Math.floor((max - 1) / 2)
  return `${text.slice(0, head)}…${text.slice(text.length - tail)}`
}

/** A scan's timestamp in the reader's locale, e.g. `29 Sep 2026, 14:03`. */
export function formatTimestamp(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return iso
  }
  return date.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** The human label for a pipeline step name from the SSE stream (§12). */
const STEP_LABELS: Record<string, string> = {
  validate: 'Checking the address is safe to visit',
  load: 'Loading the page in a real browser',
  a11y: 'Running the accessibility rule set',
  keyboard: 'Crawling the page with the Tab key',
  aria: 'Reading the page as a screen reader would',
  carbon: 'Weighing every byte the page requested',
  green: 'Asking whether the host runs on renewables',
  score: 'Working out the three scores',
  tradeoffs: 'Finding where the two goals meet and clash',
  persist: 'Saving the result',
  fixes: 'Drafting fixes',
  patch: 'Applying the fixes to a copy of the page',
  rescan: 'Scanning the patched page for real',
}

/** A readable sentence for a step, falling back to the raw name. */
export function stepLabel(name: string): string {
  return STEP_LABELS[name] ?? name
}
