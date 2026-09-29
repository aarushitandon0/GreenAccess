/**
 * Recorded scan data for tests.
 *
 * Single responsibility: load the committed demo-site scans so scene tests run
 * against data the real pipeline actually produced.
 *
 * These are cached runs from `backend/app/fixtures/`, labelled as such in the
 * files themselves (`_label`, `_recorded_at`). They are test input only. No
 * figure from them is ever shown to a user as a live result — CLAUDE.md
 * ("Never fake results") allows fixtures exactly here and in the backend's
 * offline demo fallback, and nowhere else.
 *
 * Read with `fs` rather than imported: the files live outside the Vite root,
 * and importing them would also make `tsc` typecheck 200 KB of JSON on every
 * build for no benefit.
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import type { Scan } from '../lib/types'

/** The wrapper the fixture files use around the scan itself. */
interface RecordedScan {
  _label: string
  _recorded_at: string
  scan: Scan
}

function load(name: 'demo_scan_before' | 'demo_scan_after'): RecordedScan {
  // Resolved from the Vite root (`frontend/`), which is vitest's working
  // directory. Deliberately not `new URL(..., import.meta.url)`: Vite treats
  // that as an asset import and refuses paths outside its root.
  const path = resolve(process.cwd(), '..', 'backend', 'app', 'fixtures', `${name}.json`)
  return JSON.parse(readFileSync(path, 'utf-8')) as RecordedScan
}

/** The Daily Herald as it is before any fix is applied. */
export function beforeScan(): Scan {
  return load('demo_scan_before').scan
}

/** The same site after fixes were applied and it was scanned again. */
export function afterScan(): Scan {
  return load('demo_scan_after').scan
}
