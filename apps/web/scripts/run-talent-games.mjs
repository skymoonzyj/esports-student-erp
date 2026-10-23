/** The Python owner selects one closed entry; no DB environment reaches this child. */
import { readFile, writeFile, rename } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath, URL } from 'node:url'

const configFile = path.resolve(process.argv[2])
const payload = JSON.parse(await readFile(configFile, 'utf8'))
const url = new URL(payload.baseURL)
if (payload.check !== 'talent-games' || url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.pathname !== '/' || url.username || url.password || url.search || url.hash
  || !Number.isInteger(payload.entry) || payload.entry < 0 || payload.entry > 6 || !['existing', 'resources', 'history'].includes(payload.mode) || (payload.mode !== 'existing' && payload.entry !== 0)) throw new Error('Invalid owned talent payload')
const web = fileURLToPath(new URL('../', import.meta.url))
const run = spawnSync(process.execPath, [path.join(web, 'node_modules/@playwright/test/cli.js'),
  'test', '--config', path.join(web, 'playwright.talent-games.config.ts')], {
  cwd: web, stdio: 'inherit', env: { ...process.env, FRONTEND_NAMED_CONFIG: configFile, TALENT_E2E_ARTIFACT_DIR: path.dirname(configFile) },
})
const report = JSON.parse(await readFile(path.join(path.dirname(configFile), 'browser-results.json'), 'utf8'))
const checks = []
function collect(suites) {
  for (const suite of suites) {
    for (const spec of suite.specs ?? []) for (const test of spec.tests) checks.push({
      ok: test.status === 'expected' && test.results.every(result => result.status === 'passed'),
      observations: test.annotations.filter(annotation => annotation.type === 'talent-resource').map(annotation => JSON.parse(annotation.description)),
      failureLines: test.results.flatMap(result => result.errors ?? []).flatMap(error =>
        [...(error.stack ?? error.message ?? '').matchAll(/(?:talent-tests|talent-touch|talent-dynamic-vision|frontend-talent-resources)\.spec\.ts:(\d+):/g)].map(match => Number(match[1]))),
    })
    collect(suite.suites ?? [])
  }
}
collect(report.suites)
const projects = ['desktop-1440', 'touch-mobile-390', 'desktop-1440', 'desktop-1024', 'tablet-768', 'mobile-360', 'touch-mobile-390']
const project = projects[payload.entry]
const diagnosticKind = payload.entry === 0 ? 'desktop' : payload.entry === 1 ? 'touch' : 'dynamic'
const diagnostics = {}
if (payload.mode === 'existing') {
  try {
    const raw = JSON.parse(await readFile(path.join(path.dirname(configFile), `${project}-${diagnosticKind}-browser-events.json`), 'utf8'))
    const nonnegative = value => Number.isInteger(value) && value >= 0 ? value : null
    diagnostics.events = Object.fromEntries(['consoleErrors', 'consoleWarnings', 'pageErrors'].map(key => [key, nonnegative(raw.events?.[key])]))
    diagnostics.requestFailures = Object.fromEntries(['talent_result_save', 'talent_api', 'static_asset', 'other'].map(key => [key, nonnegative(raw.events?.requestFailures?.[key])]))
  } catch { diagnostics.eventsUnavailable = true }
  if (diagnosticKind === 'dynamic') {
    try {
      const phase = JSON.parse(await readFile(path.join(path.dirname(configFile), `${project}-dynamic-safe-phase.json`), 'utf8'))
      // These are controlled fixture phase/control metadata, never stimuli or answers.
      diagnostics.phase = phase.phase
      diagnostics.keypad = phase.keypad
      diagnostics.controls = phase.controls
    } catch { diagnostics.phaseUnavailable = true }
  }
}
const ok = run.status === 0 && checks.length === (payload.mode === 'resources' ? 8 : payload.mode === 'history' ? 2 : 1) && checks.every(check => check.ok)
const output = payload.mode !== 'existing' ? payload.output : path.join(path.dirname(configFile), 'safe-browser.json')
await writeFile(output + '.tmp', JSON.stringify({ schemaVersion: 1, check: 'talent-games', mode: payload.mode, fingerprint: payload.fingerprint, ok, checks, diagnostics }, null, 2), { mode: 0o600 })
await rename(output + '.tmp', output)
process.exit(ok ? 0 : 1)
