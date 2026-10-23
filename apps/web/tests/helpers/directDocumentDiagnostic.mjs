/* global URL */
import { classifyReadyResource, validateReadyPhase } from './readyChainDiagnostic.mjs'

const categories = ['document', 'auth_me', 'auth_csrf', 'portal', 'catalog', 'overview', 'people_list', 'people_count', 'other_api']
const keys = (v, names) => v !== null && typeof v === 'object' && !Array.isArray(v)
  && Object.keys(v).sort().join('|') === [...names].sort().join('|')
const ms = v => v === null || typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 90000
const bounded = v => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 90000 ? v : null
const status = v => v === null || Number.isInteger(v) && v >= 100 && v <= 599
const relation = (value, reference) => value === null || reference === null ? 'unavailable'
  : value < reference ? 'before' : value > reference ? 'after' : 'equal'

export function directPlan() {
  return [['housing', 'desktop', 'throttled', 1], ['housing', 'mobile', 'throttled', 1],
    ['personnel', 'desktop', 'throttled', 1], ['personnel', 'mobile', 'throttled', 1],
    ['personnel', 'mobile', 'unthrottled', 7]].flatMap(([scenario, viewport, profile, cycles]) =>
    Array.from({ length: cycles }, (_, cycle) => ['cold', 'prime', 'warm'].map(phase => ({ scenario, viewport, profile, cycle, phase }))).flat())
}

// CDP IDs, raw URLs and headers never leave this bounded closure. All network
// times are converted from CDP's monotonic clock to the actual document timeOrigin.
export function observeDirectNetwork(cdp, origin, frameId) {
  let rows = new Map(), extra = new Map(), offset = null, overflow = false, documentId = null, document
  const emptyDocument = () => ({ status: null, extraInfoStatus: null, noCache: null })
  const listeners = {
    'Network.requestWillBeSent': event => {
      let category
      if (event.type === 'Document' && event.frameId === frameId) {
        category = 'document'; documentId = event.requestId
        if (Number.isFinite(event.wallTime) && Number.isFinite(event.timestamp)) offset = event.wallTime - event.timestamp
      } else {
        category = classifyReadyResource(event.request?.url, origin)
        if (!categories.includes(category)) {
          try { const url = new URL(event.request?.url); category = url.origin === origin && url.pathname.startsWith('/api/') ? 'other_api' : null } catch { category = null }
        }
      }
      if (!categories.includes(category)) return
      // A redirected tracked request is not silently overwritten as one success.
      if (rows.has(event.requestId) || rows.size >= 128 || [...rows.values()].filter(r => r.category === category).length >= 16) { overflow = true; return }
      rows.set(event.requestId, { category, start: event.timestamp, response: null, end: null, terminalSource: 'unavailable',
        headersEnd: null, headersTimingState: 'unavailable', status: null,
        extraInfoStatus: extra.get(event.requestId) ?? null, failed: false })
      extra.delete(event.requestId)
    },
    'Network.responseReceived': event => {
      const row = rows.get(event.requestId)
      if (!row) return
      row.response = event.timestamp
      // Only this response's detailed timing defines a headers boundary. The
      // response event timestamp is a separate marker, never a timing fallback.
      row.headersEnd = null; row.headersTimingState = 'unavailable'
      const timing = event.response?.timing
      if (timing !== undefined && timing !== null) {
        if (typeof timing !== 'object' || Array.isArray(timing) || !Number.isFinite(timing.requestTime) || timing.requestTime < 0
          || !Number.isFinite(timing.receiveHeadersEnd) || timing.receiveHeadersEnd < 0 && timing.receiveHeadersEnd !== -1) row.headersTimingState = 'invalid'
        else if (timing.receiveHeadersEnd !== -1) {
          row.headersEnd = timing.requestTime + timing.receiveHeadersEnd / 1000
          row.headersTimingState = Number.isFinite(row.headersEnd) ? 'available' : 'invalid'
        }
      }
      row.status = status(event.response?.status) ? event.response.status : null
      if (event.requestId === documentId) {
        document.status = row.status
        const cache = Object.entries(event.response?.headers ?? {}).find(([key]) => key.toLowerCase() === 'cache-control')?.[1]
        document.noCache = typeof cache === 'string' ? /(?:^|,)\s*no-cache(?:\s*(?:,|$))/.test(cache) : null
      }
    },
    'Network.responseReceivedExtraInfo': event => {
      const value = status(event.statusCode) ? event.statusCode : null
      const row = rows.get(event.requestId)
      if (row) row.extraInfoStatus = value
      else if (extra.size < 128) extra.set(event.requestId, value)
      else overflow = true
    },
    'Network.loadingFinished': event => { const row = rows.get(event.requestId); if (row) { row.end = event.timestamp; row.terminalSource = 'loading_finished' } },
    'Network.loadingFailed': event => { const row = rows.get(event.requestId); if (row) { row.end = event.timestamp; row.terminalSource = 'loading_failed'; row.failed = true } },
  }
  for (const [name, listener] of Object.entries(listeners)) cdp.on(name, listener)
  return {
    begin() { rows = new Map(); extra = new Map(); offset = null; overflow = false; documentId = null; document = emptyDocument() },
    snapshot(timeOrigin) {
      const usable = typeof timeOrigin === 'number' && Number.isFinite(timeOrigin) && offset !== null
      const time = value => usable && typeof value === 'number' ? bounded((value + offset) * 1000 - timeOrigin) : null
      const requests = [...rows.values()].map(row => {
        const startMs = time(row.start), responseEventMs = time(row.response), terminalEventMs = time(row.end), headersEndMs = time(row.headersEnd)
        const headersTimingState = row.headersTimingState === 'available'
          ? !usable ? 'unavailable' : headersEndMs === null ? 'invalid' : 'available' : row.headersTimingState
        return { category: row.category, startMs, responseEventMs, terminalEventMs, terminalSource: row.terminalSource,
          terminalResponseRelation: relation(terminalEventMs, responseEventMs), headersEndMs, headersTimingState,
          headersStartRelation: relation(headersEndMs, startMs), terminalHeadersRelation: relation(terminalEventMs, headersEndMs),
          status: row.status, extraInfoStatus: row.extraInfoStatus, failed: row.failed }
      })
      const counts = Object.fromEntries(categories.map(category => [category, {
        started: requests.filter(r => r.category === category).length,
        completed: requests.filter(r => r.category === category && r.terminalEventMs !== null && !r.failed && r.status !== null).length,
        failed: requests.filter(r => r.category === category && r.failed).length,
      }]))
      return { requests, counts, overflow, unavailable: !usable || requests.some(r => r.startMs === null),
        document: { ...(document ?? emptyDocument()), extraInfoStatus: rows.get(documentId)?.extraInfoStatus ?? null } }
    },
    dispose() { for (const [name, listener] of Object.entries(listeners)) cdp.off(name, listener); rows.clear(); extra.clear() },
  }
}

// Serialized separately; the existing nineteen-phase probe and schema stay intact.
export function installDirectWitnesses(scope = globalThis) {
  scope.__directWitnesses?.dispose()
  const dom = [], previous = {}
  let overflow = false
  const visible = element => Boolean(element && element.getClientRects().length
    && scope.getComputedStyle(element).visibility !== 'hidden' && scope.getComputedStyle(element).display !== 'none')
  const observe = () => {
    const states = { room_card: visible(scope.document.querySelector('.housingRoomCard')),
      table: visible(scope.document.querySelector('[role="table"][aria-label="人员名单"]')),
      updating: [...scope.document.querySelectorAll('p,span')].some(e => e.textContent === '正在更新…' && visible(e)) }
    for (const [kind, value] of Object.entries(states)) if (previous[kind] !== value) {
      const time = scope.performance.now()
      if (dom.length < 32) dom.push({ kind, visible: value, ms: Number.isFinite(time) && time >= 0 && time <= 90000 ? time : null })
      else overflow = true
      previous[kind] = value
    }
  }
  const observer = new scope.MutationObserver(observe)
  observer.observe(scope.document, { subtree: true, childList: true, attributes: true, characterData: true })
  scope.__directWitnesses = { snapshot() { observe(); return { dom, overflow } },
    dispose() { observer.disconnect(); delete scope.__directWitnesses } }
  observe()
}

/** @param {(reason: {validator: string, field: string, rule: string, itemIndex: number | null}) => void} [onReject] */
export function validateDirectPhase(p, onReject = () => {}) {
  const reject = (field, rule, itemIndex = null) => {
    try { onReject({ validator: 'direct', field, rule, itemIndex }) } catch { /* diagnostic only */ }
    return null
  }
  const bad = (field, rule, index) => { reject(field, rule, index); return true }
  if (!p || !keys(p, ['scenario', 'viewport', 'profile', 'cycle', 'phase', 'overflow', 'unavailable', 'resources', 'stages', 'dom',
    'fcpMs', 'document', 'cost', 'journeyCost', 'network', 'witnesses'])) return reject('root', 'exact_keys')
  if (!directPlan().some(item => ['scenario', 'viewport', 'profile', 'cycle', 'phase'].every(key => item[key] === p[key]))) return reject('identity', 'fixed_plan_identity')
  const { network, witnesses } = p
  const observation = Object.fromEntries(Object.entries(p).filter(([key]) => !['viewport', 'cycle', 'network', 'witnesses'].includes(key)))
  // Validation only: reuse the closed resource/stage projection, not its SPA
  // completeness predicate. Direct phase identity remains cold/prime/warm.
  if (!validateReadyPhase({ ...observation, phase: 'first' }, onReject)) return null
  if (!keys(network, ['requests', 'counts', 'overflow', 'unavailable', 'document'])) return reject('network.root', 'exact_keys')
  if (typeof network.overflow !== 'boolean') return reject('network.overflow', 'boolean')
  if (typeof network.unavailable !== 'boolean') return reject('network.unavailable', 'boolean')
  if (!keys(network.counts, categories)) return reject('network.counts', 'exact_keys')
  if (JSON.stringify(network.document) !== JSON.stringify(p.document)) return reject('network.document', 'document_mismatch')
  if (!Array.isArray(network.requests) || network.requests.length > 128) return reject('network.request_array', 'array_limit')
  if (network.requests.some((r, index) => {
    if (!keys(r, ['category', 'startMs', 'responseEventMs', 'terminalEventMs', 'terminalSource', 'terminalResponseRelation',
      'headersEndMs', 'headersTimingState', 'headersStartRelation', 'terminalHeadersRelation', 'status', 'extraInfoStatus', 'failed'])) return bad('network.request.keys', 'exact_keys', index)
    if (!categories.includes(r.category)) return bad('network.request.category', 'enum', index)
    for (const [key, field] of [['startMs', 'network.request.start'], ['responseEventMs', 'network.request.response_event'],
      ['terminalEventMs', 'network.request.terminal_event'], ['headersEndMs', 'network.request.headers_end']]) {
      if (!ms(r[key])) return bad(field, 'finite_time_or_null', index)
    }
    if (!status(r.status)) return bad('network.request.status', 'status_or_null', index)
    if (!status(r.extraInfoStatus)) return bad('network.request.extra_status', 'status_or_null', index)
    if (typeof r.failed !== 'boolean') return bad('network.request.failed', 'boolean', index)
    if (r.responseEventMs !== null && r.startMs !== null && r.responseEventMs < r.startMs) return bad('network.request.response_event', 'response_before_start', index)
    if (!['loading_finished', 'loading_failed', 'unavailable'].includes(r.terminalSource)) return bad('network.request.terminal_source', 'enum', index)
    if (!['available', 'unavailable', 'invalid'].includes(r.headersTimingState)) return bad('network.request.headers_timing', 'enum', index)
    if (r.failed !== (r.terminalSource === 'loading_failed') || r.terminalSource === 'unavailable' && r.terminalEventMs !== null) return bad('network.request.terminal_source', 'source_mismatch', index)
    if ((r.headersTimingState === 'available') !== (r.headersEndMs !== null)) return bad('network.request.headers_timing', 'availability_mismatch', index)
    // These are exact relations between retained markers, not physical download
    // durations. Do not clamp a raw event to another event or detailed timing.
    for (const [key, field, value, reference] of [
      ['terminalResponseRelation', 'network.request.terminal_response_relation', r.terminalEventMs, r.responseEventMs],
      ['headersStartRelation', 'network.request.headers_start_relation', r.headersEndMs, r.startMs],
      ['terminalHeadersRelation', 'network.request.terminal_headers_relation', r.terminalEventMs, r.headersEndMs],
    ]) {
      if (!['before', 'equal', 'after', 'unavailable'].includes(r[key])) return bad(field, 'enum', index)
      if (r[key] !== relation(value, reference)) return bad(field, 'relation_mismatch', index)
    }
    return false
  })) return null
  for (const [index, category] of categories.entries()) {
    const rows = network.requests.filter(r => r.category === category), count = network.counts[category]
    if (rows.length > 16) return reject('network.category_count', 'array_limit', index)
    if (!keys(count, ['started', 'completed', 'failed'])) return reject('network.count.keys', 'exact_keys', index)
    if (count.started !== rows.length) return reject('network.count.started', 'count_mismatch', index)
    if (count.completed !== rows.filter(r => r.terminalEventMs !== null && !r.failed && r.status !== null).length) return reject('network.count.completed', 'count_mismatch', index)
    if (count.failed !== rows.filter(r => r.failed).length) return reject('network.count.failed', 'count_mismatch', index)
  }
  if (!keys(witnesses, ['dom', 'overflow'])) return reject('witness.root', 'exact_keys')
  if (typeof witnesses.overflow !== 'boolean') return reject('witness.overflow', 'boolean')
  if (!Array.isArray(witnesses.dom) || witnesses.dom.length > 32) return reject('witness.dom_array', 'array_limit')
  if (witnesses.dom.some((d, index) => {
    if (!keys(d, ['kind', 'visible', 'ms'])) return bad('witness.dom.keys', 'exact_keys', index)
    if (!['room_card', 'table', 'updating'].includes(d.kind)) return bad('witness.dom.kind', 'enum', index)
    if (typeof d.visible !== 'boolean') return bad('witness.dom.visible', 'boolean', index)
    return !ms(d.ms) && bad('witness.dom.time', 'finite_time_or_null', index)
  })) return null
  return p
}

// The existing journey assembly, shared with the real-browser characterization.
// No normalization/repair: invalid observed values still reach the same validator.
export function assembleDirectPhase(cell, phase, observed, value, evidence, actionFailed) {
  const duration = value !== undefined && Number.isFinite(value) && value >= 0 && value <= 90 ? value * 1000 : null
  const snapshot = observed?.snapshot
  const current = snapshot ? Object.fromEntries(Object.entries(snapshot).filter(([key]) => key !== 'earlier'))
    : { resources: [], stages: [], dom: [], overflow: false, fcpMs: null }
  return { ...cell, phase, ...current,
    cost: { ...(snapshot?.cost ?? { requests: 0, encodedBytes: 0, transferBytes: 0 }), scriptDurationMs: duration },
    journeyCost: { ...(snapshot?.journeyCost ?? { requests: 0, encodedBytes: 0, transferBytes: 0 }), scriptDurationMs: duration },
    document: evidence.document, network: evidence, witnesses: observed?.witnesses ?? { dom: [], overflow: false },
    unavailable: actionFailed || !snapshot || !observed?.witnesses || duration === null }
}

export function directPhaseComplete(p, sourceCommit) {
  if (!validateDirectPhase(p) || p.unavailable || p.overflow || p.network.unavailable || p.network.overflow || p.witnesses.overflow) return false
  // The launcher binds an immutable product SHA. Never infer a legacy contract
  // from absent split resources: a truncated candidate must not look like baseline.
  if (typeof sourceCommit !== 'string' || !/^[a-f0-9]{40}$/.test(sourceCommit)) return false
  const staticRequired = [['document', 'none'], ['other_script', 'none'], ['other_css', 'none']]
  if (sourceCommit !== '95da4227ae831eeb4edfd75d3fc31bcdaa8882ae') staticRequired.push(
    ['shell_js', 'none'], ['leaf_js', 'housing'], ['shared_css', 'housing'],
    ['leaf_js', 'personnel'], ['shared_css', 'personnel'], ['css_owner_js', 'personnel'])
  if (!staticRequired.every(([category, target]) => p.resources.some(r => r.category === category && r.target === target))) return false
  const staticCategories = ['document', 'shell_js', 'leaf_js', 'css_owner_js', 'shared_css', 'other_script', 'other_css']
  if (p.resources.some(r => staticCategories.includes(r.category)
    && ['startMs', 'requestStartMs', 'responseStartMs', 'responseEndMs'].some(field => r[field] === null))) return false
  const required = ['goto_done', 'heading', 'ready', 'settled', ...(p.scenario === 'housing'
    ? ['room_card', 'filter_search', 'filter_apply', ...(p.viewport === 'mobile' ? ['filters_open'] : [])]
    : ['table', 'person_search', 'person_query', 'updating_absent'])]
  if (!required.every(name => p.stages.some(s => s.name === name && s.ms !== null)) || p.fcpMs === null
    || p.document.status === null || !p.dom.some(d => d.kind === 'heading' && d.visible && d.ms !== null)) return false
  const needed = ['document', 'auth_me', 'portal', ...(p.scenario === 'housing' ? ['catalog', 'overview'] : ['people_list', 'people_count'])]
  if (!needed.every(category => p.network.requests.some(r => r.category === category && r.startMs !== null && r.responseEventMs !== null
    && r.terminalEventMs !== null && r.status !== null && !r.failed))) return false
  // An unfinished/failed optional request stays explicit and cannot pass the
  // evidence gate. Zero optional-category requests is not an observed response.
  // Completion proves required evidence is present, not that raw event markers
  // delimit a physical interval. Missing detailed timing is explicitly optional;
  // malformed supplied timing remains invalid and cannot complete the evidence.
  if (p.network.requests.some(r => r.failed || r.startMs === null || r.responseEventMs === null || r.terminalEventMs === null
    || r.status === null || r.headersTimingState === 'invalid')) return false
  if (!p.witnesses.dom.some(d => d.kind === (p.scenario === 'housing' ? 'room_card' : 'table') && d.visible && d.ms !== null)) return false
  const updating = p.witnesses.dom.filter(d => d.kind === 'updating').at(-1)
  return p.scenario !== 'personnel' || updating?.visible === false && updating.ms !== null
}

export function directRunComplete(phases, sourceCommit) {
  const plan = directPlan()
  return Array.isArray(phases) && phases.length === 33 && phases.every((p, index) => directPhaseComplete(p, sourceCommit)
    && Object.keys(plan[index]).every(key => p[key] === plan[index][key]))
}

const failureKinds = ['action_exception', 'observer_exception', 'validation_rejected', 'projection_rejected', 'evidence_incomplete', 'sink_exception']
const failureStages = ['unknown', 'context', 'init', 'auth', 'page', 'cdp', 'cache', 'navigate', 'goto_mark', 'heading', 'room_card',
  'filters_open', 'filter_search', 'filter_apply', 'table', 'person_search', 'person_query', 'updating_absent', 'ready_mark',
  'settle', 'snapshot', 'metrics', 'network_snapshot', 'validation', 'completeness', 'phase_save', 'cleanup',
  'phase_projection', 'failure_projection', 'rejection_projection', 'report_projection', 'failure_overflow']
const errorNames = ['Error', 'TypeError', 'ReferenceError', 'RangeError', 'SyntaxError', 'TimeoutError', 'AssertionError', 'unknown']
const failureFiles = ['directDocumentDiagnostic.mjs', 'directDocumentJourney.ts', 'frontend-direct-document.spec.ts', 'run-direct-document.mjs']
const coordinate = (value, max) => Number.isInteger(value) && value > 0 && value <= max
const phaseIndexValid = value => value === null || Number.isInteger(value) && value >= 0 && value < 33

// Never persist message/stack or arbitrary string values, including Error.name.
// Reading hostile error getters must not replace the original action exception.
/** @param {unknown} [error] */
export function directFailure(kind, stage, phaseIndex, error = undefined) {
  let errorName = 'unknown', frames = []
  try { const name = error?.name; if (errorNames.includes(name)) errorName = name } catch { /* unknown */ }
  try {
    const stack = error?.stack
    if (typeof stack === 'string') for (const row of stack.slice(0, 16000).split('\n').slice(1, 33)) {
      if (!/^\s+at /.test(row)) continue
      const match = row.match(/\/(directDocumentDiagnostic\.mjs|directDocumentJourney\.ts|frontend-direct-document\.spec\.ts|run-direct-document\.mjs):(\d{1,6}):(\d{1,5})\)?$/)
      if (match && coordinate(Number(match[2]), 100000) && coordinate(Number(match[3]), 10000)) {
        frames.push({ file: match[1], line: Number(match[2]), column: Number(match[3]) })
        if (frames.length === 3) break
      }
    }
  } catch { frames = [] }
  return { kind: failureKinds.includes(kind) ? kind : 'projection_rejected', stage: failureStages.includes(stage) ? stage : 'unknown',
    phaseIndex: phaseIndexValid(phaseIndex) ? phaseIndex : null, errorName, frames }
}

export function validateDirectFailures(value) {
  if (!Array.isArray(value) || value.length > 8 || value.some(f => !keys(f, ['kind', 'stage', 'phaseIndex', 'errorName', 'frames'])
    || !failureKinds.includes(f.kind) || !failureStages.includes(f.stage) || !phaseIndexValid(f.phaseIndex) || !errorNames.includes(f.errorName)
    || !Array.isArray(f.frames) || f.frames.length > 3 || f.frames.some(frame => !keys(frame, ['file', 'line', 'column'])
      || !failureFiles.includes(frame.file) || !coordinate(frame.line, 100000) || !coordinate(frame.column, 10000)))) return null
  return value
}

// Closed metadata vocabulary, not a second phase validator. These names never
// come from object keys/values in the observation being rejected.
const rejectionFields = {
  direct: {
    exact_keys: ['root', 'network.root', 'network.counts', 'network.request.keys', 'network.count.keys', 'witness.root', 'witness.dom.keys'],
    fixed_plan_identity: ['identity'], boolean: ['network.overflow', 'network.unavailable', 'network.request.failed', 'witness.overflow', 'witness.dom.visible'],
    document_mismatch: ['network.document'], array_limit: ['network.request_array', 'network.category_count', 'witness.dom_array'],
    enum: ['network.request.category', 'network.request.terminal_source', 'network.request.headers_timing',
      'network.request.terminal_response_relation', 'network.request.headers_start_relation', 'network.request.terminal_headers_relation', 'witness.dom.kind'],
    finite_time_or_null: ['network.request.start', 'network.request.response_event', 'network.request.terminal_event', 'network.request.headers_end', 'witness.dom.time'],
    status_or_null: ['network.request.status', 'network.request.extra_status'], response_before_start: ['network.request.response_event'],
    source_mismatch: ['network.request.terminal_source'], availability_mismatch: ['network.request.headers_timing'],
    relation_mismatch: ['network.request.terminal_response_relation', 'network.request.headers_start_relation', 'network.request.terminal_headers_relation'],
    count_mismatch: ['network.count.started', 'network.count.completed', 'network.count.failed'], diagnostic_unavailable: ['diagnostic'],
  },
  ready_projection: {
    exact_keys: ['root', 'cost', 'journey_cost', 'document', 'resource.keys', 'dom.keys', 'stage.keys'],
    integer_bounds: ['cost.requests', 'cost.encoded_bytes', 'cost.transfer_bytes', 'journey_cost.requests', 'journey_cost.encoded_bytes', 'journey_cost.transfer_bytes', 'resource.encoded_bytes', 'resource.transfer_bytes'],
    finite_time_or_null: ['cost.script_duration', 'journey_cost.script_duration', 'fcp', 'resource.start', 'resource.request_start', 'resource.response_start', 'resource.response_end', 'dom.time', 'stage.time'],
    null_requires_unavailable: ['cost.script_duration', 'journey_cost.script_duration'],
    enum: ['scenario', 'phase', 'profile', 'resource.target', 'resource.category', 'dom.kind', 'stage.name'],
    boolean: ['overflow', 'unavailable', 'resource.cache_hit', 'dom.visible'], boolean_or_null: ['document.no_cache'],
    status_or_null: ['document.status', 'document.extra_status', 'resource.status'],
    array_limit: ['resource_array', 'dom_array', 'stage_array', 'api_count'], duplicate_stage: ['stage.names'],
  },
}
export function validateDirectRejections(value) {
  if (!Array.isArray(value) || value.length > 1 || value.some(r => !keys(r, ['schemaVersion', 'phaseIndex', 'validator', 'field', 'rule', 'itemIndex'])
    || r.schemaVersion !== 1 || !Number.isInteger(r.phaseIndex) || r.phaseIndex < 0 || r.phaseIndex >= 33
    || typeof r.validator !== 'string' || typeof r.rule !== 'string'
    || !Object.hasOwn(rejectionFields, r.validator) || !Object.hasOwn(rejectionFields[r.validator], r.rule)
    || !rejectionFields[r.validator][r.rule].includes(r.field)
    || !(r.itemIndex === null || Number.isInteger(r.itemIndex) && r.itemIndex >= 0 && r.itemIndex < 128))) return null
  return value
}

/**
 * @param {(kind: string, error?: unknown) => void} onFailure
 * @param {(reason: {validator: string, field: string, rule: string, itemIndex: number | null}) => void} [onReject]
 */
export async function captureDirect(action, observe, save, sourceCommit, onFailure = () => {}, onReject = () => {}) {
  let actionError, failed = false, result = null, observationFailed = false
  const record = (kind, error = undefined) => { try { onFailure(kind, error) } catch { observationFailed = true } }
  try { await action() } catch (error) { actionError = error; failed = true; record('action_exception', error) }
  try {
    result = validateDirectPhase(await observe(failed), onReject)
    if (result === null) record('validation_rejected')
  } catch (error) { observationFailed = true; record('observer_exception', error) }
  try { save(result) } catch (error) { observationFailed = true; record('sink_exception', error) }
  if (failed) throw actionError
  if (observationFailed || !directPhaseComplete(result, sourceCommit)) {
    if (!observationFailed && result !== null) record('evidence_incomplete')
    throw new Error('direct document evidence incomplete')
  }
}
