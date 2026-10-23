import { execFileSync } from 'node:child_process'
import { realpathSync } from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { chromium } from '@playwright/test'

const records = new WeakMap()
export const unavailableNative = () => ({ owned_pid_unique: false, lookup_available: false,
  foreground_matches_owned: null, executable_observed: false,
  executable_matches_configured: null, executable_in_owned_cache: null })

/** Passive, exact original button only; self-contained for page.evaluate. */
export function installPcClickWitness(scope = globalThis) {
  const document = scope.document
  const buttons = [...document.querySelectorAll('[aria-label="测试场景"][data-host-phase] button')]
    .filter(button => button.textContent.trim() === '检查设备并进入试练')
  if (buttons.length !== 1 || scope.__loadingPcInitialClick) return false
  const button = buttons[0]
  const data = { pointerdown_count: 0, click_count: 0, pointerlockchange_count: 0, pointerlockerror_count: 0,
    counts_saturated: false, pointerdown_trusted: false, pointerdown_mouse: false,
    pointerdown_activation: false, pointerdown_focus: false, pointerdown_visible: false,
    click_trusted: false, click_mouse: false, click_activation: false, click_focus: false, click_visible: false }
  const increment = key => { if (data[key] < 16) data[key]++; else data.counts_saturated = true }
  const handlers = ['pointerdown', 'click', 'pointerlockchange', 'pointerlockerror'].map(type => {
    const handler = event => {
      if (type === 'pointerdown' || type === 'click') {
        if (event.target !== button && !button.contains(event.target)) return
        increment(type + '_count')
        // First event only; subsequent activity cannot overwrite the original gesture.
        if (data[type + '_count'] !== 1) return
        data[type + '_trusted'] = event.isTrusted === true
        data[type + '_mouse'] = event.pointerType === 'mouse'
        data[type + '_activation'] = scope.navigator.userActivation?.isActive === true
        data[type + '_focus'] = document.hasFocus()
        data[type + '_visible'] = document.visibilityState === 'visible'
      } else increment(type + '_count')
    }
    document.addEventListener(type, handler, { capture: true, passive: true })
    return [type, handler]
  })
  scope.__loadingPcInitialClick = { data, handlers }
  return true
}
export function readPcClickWitness(scope = globalThis, dispose = false) {
  const record = scope.__loadingPcInitialClick
  if (!record) return null
  const result = { ...record.data }
  if (dispose) {
    for (const [type, handler] of record.handlers) scope.document.removeEventListener(type, handler, true)
    delete scope.__loadingPcInitialClick
  }
  return result
}
export function disposePcClickWitness(scope = globalThis) {
  const record = scope.__loadingPcInitialClick
  if (record) for (const [type, handler] of record.handlers) scope.document.removeEventListener(type, handler, true)
  delete scope.__loadingPcInitialClick
}

const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
function validNative(value) {
  return exact(value, Object.keys(unavailableNative()))
    && ['owned_pid_unique', 'lookup_available', 'executable_observed'].every(key => typeof value[key] === 'boolean')
    && (value.lookup_available ? value.owned_pid_unique && typeof value.foreground_matches_owned === 'boolean' : value.foreground_matches_owned === null)
    && ['executable_matches_configured', 'executable_in_owned_cache'].every(key => value.executable_observed
      ? value.lookup_available && (value[key] === null || typeof value[key] === 'boolean') : value[key] === null)
}
export function validateAcquisitionDiagnostic(value) {
  const flags = ['configured_headed_valid', 'configured_headed', 'configured_custom_executable',
    'configured_executable_available', 'configured_in_owned_cache', 'fixture_chromium', 'fixture_version_valid']
  const keys = [...flags, 'fixture_version', 'before', 'failure', 'passive']
  if (!exact(value, keys) || flags.some(key => typeof value[key] !== 'boolean') || !validNative(value.before) || !validNative(value.failure)) return null
  if (value.fixture_version_valid ? !Array.isArray(value.fixture_version) || value.fixture_version.length < 2 || value.fixture_version.length > 4
    || value.fixture_version.some(part => !Number.isInteger(part) || part < 0 || part > 100000) : value.fixture_version !== null) return null
  if (value.passive !== null) {
    const counters = ['pointerdown_count', 'click_count', 'pointerlockchange_count', 'pointerlockerror_count']
    const booleans = ['counts_saturated', ...['pointerdown', 'click'].flatMap(type => ['trusted', 'mouse', 'activation', 'focus', 'visible'].map(field => type + '_' + field))]
    if (!exact(value.passive, [...counters, ...booleans]) || booleans.some(key => typeof value.passive[key] !== 'boolean')
      || counters.some(key => !Number.isInteger(value.passive[key]) || value.passive[key] < 0 || value.passive[key] > 16)) return null
  }
  return value
}

function nativeObservation(pid, configured, cache) {
  const absent = unavailableNative()
  if (!Number.isSafeInteger(pid) || pid <= 0) return absent
  absent.owned_pid_unique = true
  if (process.platform !== 'darwin') return absent
  // Fixed read-only AppKit lookup: only owned PID and foreground equality. Raw
  // executable paths stay inside this process/script and never enter evidence.
  const script = `ObjC.import('AppKit'); var app=$.NSRunningApplication.runningApplicationWithProcessIdentifier(${pid});
    var result=${JSON.stringify(absent)};
    if(app && !app.isTerminated) {
      result.lookup_available=true;
      result.foreground_matches_owned=Number($.NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier)===${pid};
      if(app.executableURL) {
        var actual=ObjC.unwrap(app.executableURL.URLByResolvingSymlinksInPath.path);
        result.executable_observed=typeof actual==='string' && actual.length>0;
        if(result.executable_observed) {
          result.executable_matches_configured=${JSON.stringify(configured)}===null ? null : actual===${JSON.stringify(configured)};
          result.executable_in_owned_cache=${JSON.stringify(cache)}===null ? null : actual.startsWith(${JSON.stringify(cache + '/')});
        }
      }
    } JSON.stringify(result);`
  try {
    const result = JSON.parse(execFileSync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script],
      { encoding: 'utf8', timeout: 2000, stdio: ['ignore', 'pipe', 'pipe'],
        env: Object.fromEntries(['PATH', 'HOME', 'LANG', 'TMPDIR'].flatMap(key => process.env[key] ? [[key, process.env[key]]] : [])) }))
    return validNative(result) ? result : absent
  } catch { return absent }
}

/** Called once immediately before the unchanged original initial-trial click. */
export async function beginPcAcquisition(page, info) {
  if (records.has(page)) return false
  const browser = page.context().browser()
  const use = info.project.use
  let configured = null, cache = null, pid = null
  try { configured = realpathSync(use.launchOptions?.executablePath ?? chromium.executablePath()) } catch { /* unavailable */ }
  try { cache = realpathSync(process.env.PLAYWRIGHT_BROWSERS_PATH) } catch { /* unavailable */ }
  let version = null
  try {
    const raw = browser.version()
    if (/^\d+(?:\.\d+){1,3}$/.test(raw)) {
      const parts = raw.split('.').map(Number)
      if (parts.every(part => Number.isInteger(part) && part <= 100000)) version = parts
    }
  } catch { /* unavailable */ }
  try {
    const cdp = await browser.newBrowserCDPSession()
    try {
      const rows = (await cdp.send('SystemInfo.getProcessInfo')).processInfo.filter(row => row.type === 'browser')
      if (rows.length === 1 && Number.isSafeInteger(rows[0].id) && rows[0].id > 0) pid = rows[0].id
    } finally { await cdp.detach() }
  } catch { /* unavailable */ }
  const data = { configured_headed_valid: typeof use.headless === 'boolean', configured_headed: use.headless === false,
    configured_custom_executable: typeof use.launchOptions?.executablePath === 'string',
    configured_executable_available: configured !== null, configured_in_owned_cache: Boolean(configured && cache && configured.startsWith(cache + path.sep)),
    fixture_chromium: browser?.browserType().name() === 'chromium', fixture_version_valid: version !== null, fixture_version: version,
    before: nativeObservation(pid, configured, cache), failure: unavailableNative(), passive: null }
  records.set(page, { data, pid, configured, cache })
  try { await page.evaluate(installPcClickWitness) } catch { /* Missing passive state stays null. */ }
  return true
}
export async function finishPcAcquisition(page, failed) {
  const record = records.get(page)
  if (!record) return null
  try {
    if (!failed) return null
    record.data.failure = nativeObservation(record.pid, record.configured, record.cache)
    record.data.passive = await page.evaluate(readPcClickWitness)
    return validateAcquisitionDiagnostic(record.data)
  } finally { await page.evaluate(disposePcClickWitness).catch(() => {}) }
}
