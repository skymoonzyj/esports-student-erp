import { readFile, writeFile, rename } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath, URL } from 'node:url'
import { validatePcPreflight } from '../tests/helpers/esportsPreflightDiagnostic.mjs'
import { validateAcquisitionDiagnostic } from '../tests/helpers/esportsAcquisitionDiagnostic.mjs'
import { fixedCapabilityDiagnostic, validateCapabilityDiagnostic, capabilityPairValid } from '../tests/helpers/esportsCapabilityDiagnostic.mjs'

const file = path.resolve(process.argv[2])
const payload = JSON.parse(await readFile(file, 'utf8'))
const url = new URL(payload.baseURL)
if (payload.check !== 'esports-games' || url.protocol !== 'http:' || url.hostname !== '127.0.0.1'
  || !url.port || url.pathname !== '/' || url.username || url.password || url.search || url.hash
  || !Number.isInteger(payload.entry) || payload.entry < 0 || payload.entry > 5
  || (!['existing', 'audio'].includes(payload.mode) && !fixedCapabilityDiagnostic(payload))
  || (payload.mode === 'audio' && payload.entry !== 5)) throw new Error('Fixed esports payload required')
const web = fileURLToPath(new URL('../', import.meta.url))
const run = spawnSync(process.execPath, [path.join(web, 'node_modules/@playwright/test/cli.js'), 'test',
  '--config', path.join(web, 'playwright.esports-games.config.ts')], { cwd: web, stdio: 'inherit',
  env: { ...process.env, FRONTEND_NAMED_CONFIG: file, ESPORTS_E2E_ARTIFACT_DIR: path.dirname(file) } })
const report = JSON.parse(await readFile(path.join(path.dirname(file), 'browser-results.json'), 'utf8'))
const checks = []
function collect(suites) {
  for (const suite of suites) {
    for (const spec of suite.specs ?? []) for (const test of spec.tests) checks.push({
      ok: test.status === 'expected' && test.results.length === 1 && test.results[0].status === 'passed',
      observations: test.annotations.filter(item => item.type === 'loading-evidence').map(item => JSON.parse(item.description)),
      preflightDiagnostics: test.annotations.filter(item => item.type === 'esports.preflight.v1').map(item => {
        try { return validatePcPreflight(JSON.parse(item.description)) } catch { return null }
      }),
      acquisitionDiagnostics: test.annotations.filter(item => item.type === 'esports.acquisition.v1').map(item => {
        try { return validateAcquisitionDiagnostic(JSON.parse(item.description)) } catch { return null }
      }),
      ...(fixedCapabilityDiagnostic(payload) ? { capabilityDiagnostics: test.annotations.filter(item => item.type === 'esports.capability.v1').map(item => {
        try { return validateCapabilityDiagnostic(JSON.parse(item.description)) } catch { return null }
      }) } : {}),
      failureLines: test.results.flatMap(result => result.errors ?? []).flatMap(error =>
        [...(error.stack ?? error.message ?? '').matchAll(/(?:esports-(?:pc|touch|recovery|capabilities)|frontend-loading)\.spec\.ts:(\d+):/g)].map(match => Number(match[1]))),
    })
    collect(suite.suites ?? [])
  }
}
collect(report.suites)
const expected = payload.mode === 'audio' ? 2 : [2, 2, 3, 2, 2, 1][payload.entry]
const diagnosticEvidenceValid = !fixedCapabilityDiagnostic(payload) || capabilityPairValid(checks)
const ok = run.status === 0 && checks.length === expected && checks.every(item => item.ok) && diagnosticEvidenceValid
const output = path.join(path.dirname(file), payload.mode === 'audio' ? 'safe-audio.json' : 'safe-browser.json')
await writeFile(output + '.tmp', JSON.stringify({ schemaVersion: 1, check: 'esports-games', mode: payload.mode,
  fingerprint: payload.fingerprint, durationMs: report.stats?.duration ?? null, ok, checks,
  ...(fixedCapabilityDiagnostic(payload) ? { diagnosticEvidenceValid } : {}) }, null, 2), { mode: 0o600 })
await rename(output + '.tmp', output)
process.exit(ok ? 0 : 1)
