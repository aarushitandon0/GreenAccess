/**
 * Table-driven tests for the display formatters.
 *
 * These functions decide what number a reader sees, so the cases that matter
 * are the edges: zero, sub-milligram carbon, the unit boundaries, and the
 * reductions that are undefined rather than zero.
 */

import { describe, expect, it } from 'vitest'

import {
  formatByteDelta,
  formatBytes,
  formatCountDelta,
  formatDuration,
  formatGrams,
  formatGramsDelta,
  formatReduction,
  hostOf,
  pluralise,
  stepLabel,
  truncateMiddle,
} from './format'

describe('formatBytes', () => {
  it.each([
    [0, '0 B'],
    [-5, '0 B'],
    [512, '512 B'],
    [1024, '1 KB'],
    [1536, '2 KB'],
    [1024 * 1024, '1.0 MB'],
    [2_400_000, '2.3 MB'],
    [1024 ** 3, '1.0 GB'],
  ])('formats %i as %s', (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected)
  })
})

describe('formatByteDelta', () => {
  it.each([
    [0, '0 B'],
    [-1_572_864, '-1.5 MB'],
    [2048, '+2 KB'],
  ])('formats %i as %s', (bytes, expected) => {
    expect(formatByteDelta(bytes)).toBe(expected)
  })
})

describe('formatGrams', () => {
  it.each([
    [0, '0 g'],
    [0.42, '0.42 g'],
    [1.267, '1.27 g'],
    // Below a hundredth of a gram, two decimals would read as zero emissions.
    [0.004, '0.004 g'],
  ])('formats %f as %s', (grams, expected) => {
    expect(formatGrams(grams)).toBe(expected)
  })
})

describe('formatGramsDelta', () => {
  it.each([
    [0, '0 g'],
    [-0.83, '-0.83 g'],
    [0.1, '+0.10 g'],
  ])('formats %f as %s', (grams, expected) => {
    expect(formatGramsDelta(grams)).toBe(expected)
  })
})

describe('formatCountDelta', () => {
  it.each([
    [47, '+47'],
    [-23, '-23'],
    [0, '0'],
  ])('formats %i as %s', (count, expected) => {
    expect(formatCountDelta(count)).toBe(expected)
  })
})

describe('formatReduction', () => {
  it('reports the percentage saved', () => {
    expect(formatReduction(2_000_000, 540_000)).toBe('73%')
  })

  it('returns null when there is nothing to reduce from', () => {
    // A reduction from zero is undefined, and must not be shown as 0% or 100%.
    expect(formatReduction(0, 0)).toBeNull()
    expect(formatReduction(-1, 0)).toBeNull()
  })

  it('handles a page that got heavier', () => {
    expect(formatReduction(100, 150)).toBe('-50%')
  })
})

describe('formatDuration', () => {
  it.each([
    [0, '0 ms'],
    [840, '840 ms'],
    [999, '999 ms'],
    [1000, '1.0 s'],
    [2340, '2.3 s'],
  ])('formats %i as %s', (ms, expected) => {
    expect(formatDuration(ms)).toBe(expected)
  })
})

describe('pluralise', () => {
  it.each([
    [1, 'issue', '1 issue'],
    [0, 'issue', '0 issues'],
    [12, 'issue', '12 issues'],
  ])('pluralises %i %s', (count, noun, expected) => {
    expect(pluralise(count, noun)).toBe(expected)
  })

  it('takes an irregular plural', () => {
    expect(pluralise(3, 'entry', 'entries')).toBe('3 entries')
  })
})

describe('hostOf', () => {
  it('extracts the host', () => {
    expect(hostOf('https://cdn.example.com/a/b.js?x=1')).toBe('cdn.example.com')
  })

  it('falls back to the raw value for anything unparseable', () => {
    // These strings come from the page under scan; they must not throw.
    expect(hostOf('not a url')).toBe('not a url')
  })
})

describe('truncateMiddle', () => {
  it('leaves short text alone', () => {
    expect(truncateMiddle('short.js', 48)).toBe('short.js')
  })

  it('keeps the head and the tail', () => {
    const result = truncateMiddle('https://example.com/a/very/long/path/to/asset.js', 24)
    expect(result).toHaveLength(24)
    expect(result).toContain('…')
    expect(result.endsWith('asset.js')).toBe(true)
  })
})

describe('stepLabel', () => {
  it('gives a sentence for each pipeline step', () => {
    expect(stepLabel('a11y')).toMatch(/accessibility/i)
    expect(stepLabel('rescan')).toMatch(/for real/i)
  })

  it('falls back to the raw name for an unknown step', () => {
    expect(stepLabel('something_new')).toBe('something_new')
  })
})
