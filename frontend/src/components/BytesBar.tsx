/**
 * What a page is made of, by transfer size.
 *
 * Single responsibility: compare byte totals across the resource types the
 * scanner reports.
 *
 * Form note. The obvious choice is a stacked bar, and it is the wrong one here.
 * Seven resource types need a categorical palette, but MASTERSPEC §13 pins a
 * single-hue palette; a seven-step green ramp would put adjacent segments below
 * any usable colour-vision separation, and colouring the segments by size
 * instead would make colour mean rank rather than identity. Horizontal bars in
 * one hue, with the type named on each row, carry identity in the label where
 * it belongs and drop the problem entirely.
 *
 * It is built as a table so the figures are readable as data — the bar is a
 * visual reading of the number in the next cell, never the only copy of it.
 */

import { formatBytes } from '../lib/format'
import type { ResourceType } from '../lib/types'

/** Fixed display order, coarsest-to-finest by typical weight. */
const ORDER: ResourceType[] = ['img', 'media', 'js', 'font', 'css', 'html', 'other']

const LABELS: Record<ResourceType, string> = {
  img: 'Images',
  media: 'Video and audio',
  js: 'Scripts',
  font: 'Fonts',
  css: 'Stylesheets',
  html: 'HTML',
  other: 'Other',
}

export interface BytesBarProps {
  byType: Partial<Record<ResourceType, number>>
  total: number
  caption: string
}

export function BytesBar({ byType, total, caption }: BytesBarProps): JSX.Element {
  const rows = ORDER.map((type) => ({ type, bytes: byType[type] ?? 0 })).filter(
    (row) => row.bytes > 0,
  )
  // Bars are scaled to the largest row, not to the total: at page-weight
  // distributions the smaller types would otherwise be invisible slivers.
  const largest = rows.reduce((max, row) => Math.max(max, row.bytes), 0)

  return (
    <table className="bytes">
      <caption className="bytes__caption">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Resource type</th>
          <th scope="col">Share of page weight</th>
          <th scope="col">Transfer size</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const share = total > 0 ? (row.bytes / total) * 100 : 0
          const width = largest > 0 ? (row.bytes / largest) * 100 : 0
          return (
            <tr key={row.type}>
              <th scope="row">{LABELS[row.type]}</th>
              <td className="bytes__plot">
                {/*
                  The bar is a reading of the cell beside it, so it carries no
                  information of its own and is hidden from assistive tech.
                */}
                <span className="bytes__track" aria-hidden="true">
                  <span className="bytes__fill" style={{ width: `${width.toFixed(2)}%` }} />
                </span>
                <span className="bytes__share">{share.toFixed(0)}%</span>
              </td>
              <td className="bytes__value">{formatBytes(row.bytes)}</td>
            </tr>
          )
        })}
      </tbody>
      <tfoot>
        <tr>
          <th scope="row">Total</th>
          <td />
          <td className="bytes__value">{formatBytes(total)}</td>
        </tr>
      </tfoot>
    </table>
  )
}
