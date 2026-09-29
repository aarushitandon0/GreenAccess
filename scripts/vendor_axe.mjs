/**
 * Vendor axe-core into backend/app/scanner/vendor (MASTERSPEC §6.3).
 *
 * Scans must not depend on a CDN, and the rule set has to be reproducible: a
 * silent axe upgrade would change violation counts and therefore scores. So the
 * file is committed and its version is reported in ScanResult.engine_versions.
 *
 *   node scripts/vendor_axe.mjs 4.13.0
 *
 * Writes axe.min.js and axe-version.json (version, sha256, source, date).
 */

import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { copyFileSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const VENDOR_DIR = resolve(HERE, '..', 'backend', 'app', 'scanner', 'vendor')

const version = process.argv[2] ?? '4.13.0'
const staging = mkdtempSync(join(tmpdir(), 'axe-vendor-'))

try {
  execFileSync('npm', ['init', '-y'], { cwd: staging, stdio: 'ignore', shell: true })
  execFileSync(
    'npm',
    ['install', '--no-fund', '--no-audit', '--no-save', `axe-core@${version}`],
    { cwd: staging, stdio: 'inherit', shell: true },
  )

  const source = join(staging, 'node_modules', 'axe-core', 'axe.min.js')
  const installed = JSON.parse(
    readFileSync(join(staging, 'node_modules', 'axe-core', 'package.json'), 'utf8'),
  )
  if (installed.version !== version) {
    throw new Error(`asked for axe-core ${version} but got ${installed.version}`)
  }

  mkdirSync(VENDOR_DIR, { recursive: true })
  copyFileSync(source, join(VENDOR_DIR, 'axe.min.js'))

  const bytes = readFileSync(source)
  const meta = {
    name: 'axe-core',
    version,
    source: `https://registry.npmjs.org/axe-core/-/axe-core-${version}.tgz`,
    file: 'axe.min.js',
    bytes: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex'),
    vendored_on: new Date().toISOString().slice(0, 10),
    license: installed.license ?? 'MPL-2.0',
    note:
      'Vendored per MASTERSPEC 6.3 so scans do not depend on a CDN and the rule ' +
      'set is reproducible. Update with scripts/vendor_axe.mjs; the version is ' +
      'reported in ScanResult.engine_versions.',
  }
  writeFileSync(join(VENDOR_DIR, 'axe-version.json'), JSON.stringify(meta, null, 2) + '\n', 'utf8')

  console.log(`vendored axe-core ${version} (${bytes.length.toLocaleString()} bytes)`)
  console.log(`sha256 ${meta.sha256}`)
} finally {
  rmSync(staging, { recursive: true, force: true })
}
