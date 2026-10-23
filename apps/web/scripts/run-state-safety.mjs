/** Fixed state/email suite; no raw errors, credentials or mailbox contents escape. */
import { readFile, rename, writeFile } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath, URL } from 'node:url'

const file = path.resolve(process.argv[2])
const payload = JSON.parse(await readFile(file, 'utf8'))
const url = new URL(payload.baseURL)
if (payload.check !== 'state-safety' || url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port) throw new Error('Invalid owned state payload')
const web = fileURLToPath(new URL('../', import.meta.url))
const run = spawnSync(process.execPath, [path.join(web, 'node_modules/@playwright/test/cli.js'),
  'test', '--config', path.join(web, 'playwright.state-safety.config.ts')], {
  cwd: web, stdio: 'inherit', env: { ...process.env, FRONTEND_NAMED_CONFIG: file },
})
const report = JSON.parse(await readFile(path.join(path.dirname(file), 'browser-results.json'), 'utf8'))
const checks = []
function collect(suites) {
  for (const suite of suites) {
    for (const spec of suite.specs ?? []) for (const test of spec.tests) checks.push({
      name: spec.title, ok: test.status === 'expected' && test.results.length === 1 && test.results[0].status === 'passed',
      observations: test.annotations.filter(item => item.type === 'state-evidence').map(item => JSON.parse(item.description)),
      failureLines: test.results.flatMap(result => result.errors ?? []).flatMap(error =>
        [...(error.stack ?? error.message ?? '').matchAll(/frontend-state-safety\.spec\.ts:(\d+):/g)].map(match => Number(match[1]))),
    })
    collect(suite.suites ?? [])
  }
}
collect(report.suites)
const result = { schemaVersion: 1, kind: 'non-performance', check: 'state-safety',
  durationMs: Number.isFinite(report.stats?.duration) ? report.stats.duration : null,
  fingerprint: payload.fingerprint, ok: run.status === 0 && checks.length === 9 && checks.every(check => check.ok), checks }
await writeFile(`${payload.output}.tmp`, JSON.stringify(result, null, 2), { mode: 0o600 })
await rename(`${payload.output}.tmp`, payload.output)
process.exit(result.ok ? 0 : 1)
