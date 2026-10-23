/** Fixed passive diagnosis only; private browser reports never leave owner scratch. */
import { readFile, rename, writeFile } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath, URL } from 'node:url'
import { directFailure, directRunComplete, validateDirectFailures, validateDirectPhase, validateDirectRejections } from '../tests/helpers/directDocumentDiagnostic.mjs'

export function projectDirectReport(report, exitCode, sourceCommit) {
  const tests = []
  let shapeValid = true
  const visit = (suites, depth = 0) => {
    if (!Array.isArray(suites) || depth > 4 || suites.length > 8) { shapeValid = false; return }
    for (const suite of suites) {
      if (!suite || typeof suite !== 'object') { shapeValid = false; continue }
      const specs = suite.specs ?? []
      if (!Array.isArray(specs) || specs.length > 4) { shapeValid = false; continue }
      for (const spec of specs) {
        if (!spec || !Array.isArray(spec.tests) || spec.tests.length > 4) { shapeValid = false; continue }
        tests.push(...spec.tests)
      }
      if (suite.suites !== undefined) visit(suite.suites, depth + 1)
    }
  }
  visit(report?.suites)
  let phases = [], failures = [], rejections = [], browserVersion = null, valid = shapeValid && tests.length === 1
  const reject = (stage, index = null) => { if (failures.length < 12) failures.push(directFailure('projection_rejected', stage, index)) }
  const row = tests[0]
  if (row) {
    const annotations = Array.isArray(row.annotations) ? row.annotations.filter(a => a && typeof a === 'object') : []
    const evidence = annotations.filter(a => a.type === 'direct-document-evidence')
    const versions = annotations.filter(a => a.type === 'direct-browser-version')
    const failureEvidence = annotations.filter(a => a.type === 'direct-document-failures')
    let retained = null
    if (failureEvidence.length === 1 && typeof failureEvidence[0].description === 'string' && failureEvidence[0].description.length <= 16000) {
      try { retained = validateDirectFailures(JSON.parse(failureEvidence[0].description)) } catch { /* unavailable */ }
    }
    if (retained === null) reject('failure_projection')
    else failures = retained
    const rejectionEvidence = annotations.filter(a => a.type === 'direct-document-rejections')
    let firstRejection = null
    if (rejectionEvidence.length === 1 && typeof rejectionEvidence[0].description === 'string' && rejectionEvidence[0].description.length <= 2048) {
      try { firstRejection = validateDirectRejections(JSON.parse(rejectionEvidence[0].description)) } catch { /* unavailable */ }
    }
    if (firstRejection === null) reject('rejection_projection')
    else rejections = firstRejection
    const missingReason = failures.find(f => f.kind === 'validation_rejected' && f.phaseIndex !== null)
    if (!rejections.length && missingReason) rejections = [{ schemaVersion: 1, phaseIndex: missingReason.phaseIndex,
      validator: 'direct', field: 'diagnostic', rule: 'diagnostic_unavailable', itemIndex: null }]
    if (versions.length === 1 && /^\d+\.\d+\.\d+\.\d+$/.test(versions[0].description)) browserVersion = versions[0].description
    if (evidence.length === 1 && typeof evidence[0].description === 'string' && evidence[0].description.length <= 2_000_000) {
      try {
        const parsed = JSON.parse(evidence[0].description)
        phases = Array.isArray(parsed) && parsed.length <= 33 ? parsed.map(validateDirectPhase) : [null]
        if (!Array.isArray(parsed) || parsed.length > 33) reject('phase_projection')
        else { const index = phases.findIndex(p => p === null); if (index >= 0) reject('phase_projection', index) }
      } catch { phases = [null]; reject('phase_projection') }
    } else { valid = false; reject('phase_projection') }
    valid &&= row.status === 'expected' && Array.isArray(row.results) && row.results.length === 1 && row.results[0]?.status === 'passed'
  }
  if (exitCode !== 0 || !valid || browserVersion === null || !directRunComplete(phases, sourceCommit)) reject('report_projection')
  return { ok: exitCode === 0 && valid && browserVersion !== null && failures.length === 0 && rejections.length === 0 && directRunComplete(phases, sourceCommit),
    browserVersion, plannedPhases: 33, notRunPhases: 33 - phases.length, phases, failures, rejections }
}

async function main() {
  const configFile = path.resolve(process.argv[2])
  const payload = JSON.parse(await readFile(configFile, 'utf8'))
  const url = new URL(payload.baseURL)
  if (payload.check !== 'direct-document-diagnostic' || url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port) throw new Error('Invalid owned named payload')
  const web = fileURLToPath(new URL('../', import.meta.url))
  const run = spawnSync(process.execPath, [path.join(web, 'node_modules/@playwright/test/cli.js'),
    'test', '--config', path.join(web, 'playwright.direct-document.config.ts')], {
    cwd: web, stdio: 'inherit', env: { ...process.env, FRONTEND_NAMED_CONFIG: configFile },
  })
  let report = {}
  try { report = JSON.parse(await readFile(path.join(path.dirname(configFile), 'browser-results.json'), 'utf8')) } catch { /* failed/not run remains explicit */ }
  const result = { schemaVersion: 1, kind: 'non-performance', check: 'direct-document-diagnostic',
    fingerprint: payload.fingerprint, ...projectDirectReport(report, run.status, payload.fingerprint?.sourceCommit) }
  await writeFile(`${payload.output}.tmp`, JSON.stringify(result, null, 2), { mode: 0o600 })
  await rename(`${payload.output}.tmp`, payload.output)
  return result.ok ? 0 : 1
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exit(await main())
