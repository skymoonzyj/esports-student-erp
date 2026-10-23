// One fixed two-case discriminator. Never send requests, await settlement or retain raw data.
const cases = ['desktop', 'nonphone']
const suites = ['unknown', 'pc-fps', 'pc-moba', 'mobile-fps', 'mobile-moba']
const tests = ['unknown', 'precision', 'tracking', 'switching', 'recognition', 'move-fire', 'last-hit', 'orb-walk', 'dodge', 'map-awareness', 'skill-aim', 'move-attack']
const states = ['unknown', 'running', 'awaiting_submission', 'interrupted', 'expired', 'submitted']
const modes = ['unknown', 'formal', 'practice']
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
export const fixedCapabilityDiagnostic = value => value?.entry === 3 && value.mode === 'capability-diagnostic'

export function projectCapabilityResponse(category, body) {
  const result = { active: null, mode: 'unknown', suite: 'unknown', test: 'unknown', state: 'unknown', body_valid: false }
  if (category === 'context') {
    if (body?.active_run === null) return { ...result, active: false, body_valid: true }
    const active = body?.active_run
    if (active && typeof active === 'object' && !Array.isArray(active)) {
      result.active = true
      result.mode = modes.slice(1).includes(active.mode) ? active.mode : 'unknown'
      result.suite = suites.slice(1).includes(active.suite_id) ? active.suite_id : 'unknown'
      result.test = tests.slice(1).includes(active.test_id) ? active.test_id : 'unknown'
      result.body_valid = [result.mode, result.suite, result.test].every(value => value !== 'unknown')
    }
  } else {
    const state = category === 'reconcile' ? body?.attempt?.state : body?.state
    if (states.slice(1).includes(state)) { result.state = state; result.body_valid = true }
  }
  return result
}
function categoryOf(request) {
  try {
    const path = new globalThis.URL(request.url()).pathname
    if (request.method() === 'GET' && path === '/api/v1/esports-assessments/context') return 'context'
    if (request.method() === 'GET' && /^\/api\/v1\/esports-assessments\/practice-runs\/[0-9a-f-]{36}$/.test(path)) return 'reconcile'
    if (request.method() === 'POST' && /^\/api\/v1\/esports-assessments\/practice-runs\/[0-9a-f-]{36}\/interrupt$/.test(path)) return 'interrupt'
  } catch { /* No raw URL retained. */ }
  return null
}
const blank = { category: 'none', status: null, ...projectCapabilityResponse('context', null),
  selection: 'none', outcome: 'none', count: null, visible: null, enabled: null, aria_disabled: null }
export function createCapabilityDiagnostic(clock = () => globalThis.performance.now()) {
  const origin = clock(), events = [], pages = new WeakMap(), requests = new WeakMap()
  let overflow = false
  const push = (page, kind, data = {}) => {
    const name = pages.get(page)
    if (!name) return
    const ms = clock() - origin
    if (events.length >= 96 || !Number.isFinite(ms) || ms < 0 || ms > 180000) { overflow = true; return }
    events.push({ case: name, kind, ms: Math.round(ms), ...blank, ...data })
  }
  return {
    begin(page, name) {
      if (!cases.includes(name) || pages.has(page)) { overflow = true; return }
      pages.set(page, name); push(page, 'begin')
      page.on('request', request => {
        const category = categoryOf(request)
        if (category) { requests.set(request, { category, status: null }); push(page, 'start', { category }) }
      })
      page.on('response', response => {
        const observed = requests.get(response.request())
        if (observed) { observed.status = response.status(); push(page, 'response', observed) }
      })
      page.on('requestfailed', request => {
        const observed = requests.get(request)
        if (observed) push(page, 'failed', observed)
      })
      page.on('requestfinished', request => {
        const observed = requests.get(request)
        if (!observed) return
        push(page, 'finished', observed)
        // Intentionally not awaited by the test/teardown: observer must not repair the race.
        void (async () => {
          let body = null
          try { body = await (await request.response()).json() } catch { /* Closed/invalid body stays unavailable. */ }
          push(page, 'complete', { ...observed, ...projectCapabilityResponse(observed.category, body) })
        })()
      })
      page.on('close', () => push(page, 'closed'))
    },
    mark: push,
    selection(page, selection, outcome, control = {}) { push(page, 'selection', { selection, outcome, ...control }) },
    snapshot(name) { return { case: name, overflow, events: events.map(event => ({ ...event })) } },
  }
}

export function validateCapabilityDiagnostic(value) {
  if (!exact(value, ['case', 'overflow', 'events']) || !cases.includes(value.case) || value.overflow !== false
    || !Array.isArray(value.events) || value.events.length < 2 || value.events.length > 96) return null
  let previous = -1
  for (const event of value.events) {
    if (!exact(event, ['case', 'kind', 'ms', ...Object.keys(blank)]) || !cases.includes(event.case)
      || !['begin', 'teardown', 'closed', 'interrupted_ui', 'start', 'response', 'finished', 'complete', 'failed', 'selection'].includes(event.kind)
      || !Number.isInteger(event.ms) || event.ms < previous || event.ms > 180000
      || !['none', 'context', 'reconcile', 'interrupt'].includes(event.category)
      || !(event.status === null || Number.isInteger(event.status) && event.status >= 100 && event.status <= 599)
      || !modes.includes(event.mode) || !suites.includes(event.suite) || !tests.includes(event.test) || !states.includes(event.state)
      || typeof event.body_valid !== 'boolean' || !['none', 'suite', 'project'].includes(event.selection)
      || !['none', 'begin', 'passed', 'failed'].includes(event.outcome)
      || !(event.count === null || Number.isInteger(event.count) && event.count >= 0 && event.count <= 16)
      || [event.active, event.visible, event.enabled, event.aria_disabled].some(flag => flag !== null && typeof flag !== 'boolean')) return null
    previous = event.ms
  }
  if (!['begin', 'teardown'].every(kind => value.events.some(event => event.case === value.case && event.kind === kind))) return null
  return value
}
export function capabilityPairValid(checks) {
  if (checks.length !== 2) return false
  const pair = checks.map(check => check.capabilityDiagnostics)
  if (pair.some(items => !Array.isArray(items) || items.length !== 1 || !validateCapabilityDiagnostic(items[0]))) return false
  if (pair[0][0].case !== 'desktop' || pair[1][0].case !== 'nonphone') return false
  const events = pair[1][0].events
  const selections = events.filter(event => event.case === 'nonphone' && event.kind === 'selection')
  const selectionObserved = selections.some(event => event.outcome === 'failed' && event.count !== null
    && (event.count !== 1 || typeof event.visible === 'boolean' && typeof event.enabled === 'boolean'))
    || ['suite', 'project'].every(phase => selections.some(event => event.selection === phase && event.outcome === 'passed'))
  return cases.every(name => events.some(event => event.case === name && event.category === 'context'
    && event.kind === 'complete' && event.body_valid))
    && events.some(event => event.case === 'desktop' && event.kind === 'interrupted_ui')
    && events.some(event => event.case === 'desktop' && event.kind === 'closed')
    && selectionObserved
}

let current = null
const ownedPages = new WeakMap()
export const hasCapabilityDiagnostic = page => ownedPages.has(page)
export function beginCapabilityCase(page, name) {
  if (name === 'desktop') current = createCapabilityDiagnostic()
  if (!current) return
  current.begin(page, name); ownedPages.set(page, { record: current, name })
}
export function markCapabilityCase(page, kind) { ownedPages.get(page)?.record.mark(page, kind) }
export function finishCapabilityCase(page, info) {
  const owned = ownedPages.get(page)
  if (!owned) return
  owned.record.mark(page, 'teardown')
  info.annotations.push({ type: 'esports.capability.v1', description: JSON.stringify(owned.record.snapshot(owned.name)) })
}
export async function observeCapabilitySelection(locator, phase, outcome) {
  const owned = ownedPages.get(locator.page())
  if (!owned) return
  let control = {}
  if (outcome === 'failed') {
    try {
      control = await locator.evaluateAll(elements => ({ count: Math.min(elements.length, 17),
        visible: elements.length === 1 ? Boolean(elements[0].getClientRects().length && elements[0].ownerDocument.defaultView.getComputedStyle(elements[0]).visibility !== 'hidden') : null,
        enabled: elements.length === 1 ? !elements[0].matches(':disabled,[aria-disabled="true"]') : null,
        aria_disabled: elements.length === 1 ? elements[0].getAttribute('aria-disabled') === 'true' : null }))
    } catch { /* Missing control remains explicitly null; original error is preserved. */ }
  }
  owned.record.selection(locator.page(), phase, outcome, control)
}
