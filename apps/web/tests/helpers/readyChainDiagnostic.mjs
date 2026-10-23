/** Fixed test-only projection. Never retain resource names or DOM text. */
/* global URL */
export function classifyReadyResource(value, origin) {
  try {
    const url = new URL(value)
    if (url.origin !== origin) return null
    const api = { '/api/v1/auth/me': 'auth_me', '/api/v1/auth/csrf': 'auth_csrf',
      '/api/v1/portal/context': 'portal', '/api/v1/housing/catalog': 'catalog',
      '/api/v1/housing/overview': 'overview', '/api/v1/people': 'people_list', '/api/v1/people/counts': 'people_count' }
    if (Object.hasOwn(api, url.pathname)) return api[url.pathname]
    if (/\/AppShell-[^/]+\.js$/.test(url.pathname)) return 'shell_js'
    if (/\/(HousingOverviewPage|PeoplePage)-[^/]+\.js$/.test(url.pathname)) return 'leaf_js'
    if (/^\/assets\/people-[A-Za-z0-9_-]+\.js$/.test(url.pathname)) return 'css_owner_js'
    if (/\/(housing|people)-[^/]+\.css$/.test(url.pathname)) return 'shared_css'
    return url.pathname.endsWith('.js') ? 'other_script' : url.pathname.endsWith('.css') ? 'other_css' : 'other'
  } catch { return null }
}

const apis = ['auth_me', 'auth_csrf', 'portal', 'catalog', 'overview', 'people_list', 'people_count']
const categories = [...apis, 'shell_js', 'leaf_js', 'css_owner_js', 'shared_css', 'other_script', 'other_css', 'document', 'other']
const stageNames = ['goto_done', 'heading', 'identifier', 'password', 'submit', 'room_card', 'filters_open',
  'filter_search', 'filter_apply', 'table', 'person_search', 'person_query', 'updating_absent',
  'nav_filter_checked', 'nav_menu_checked', 'nav_clicked', 'nav_close_checked', 'ready', 'settled', 'workbench']
const keys = (value, expected) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join('|') === [...expected].sort().join('|')
const ms = value => value === null || typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 90000
const status = value => value === null || Number.isInteger(value) && value >= 100 && value <= 599
const bytes = value => Number.isInteger(value) && value >= 0 && value <= 64 * 1024 * 1024
export function readyJourneyComplete(phases) {
  const expected = ['login/cold/throttled', 'login/prime/throttled', 'login/warm/throttled',
    'housing/setup/unthrottled', 'housing/first/unthrottled', 'housing/back/unthrottled', 'housing/again/unthrottled',
    'personnel/setup/unthrottled', 'personnel/first/unthrottled', 'personnel/back/unthrottled', 'personnel/again/unthrottled',
    'housing/setup/throttled', 'housing/first/throttled', 'housing/back/throttled', 'housing/again/throttled',
    'personnel/setup/throttled', 'personnel/first/throttled', 'personnel/back/throttled', 'personnel/again/throttled']
  return Array.isArray(phases) && phases.length === expected.length
    && phases.every((phase, index) => readyPhaseComplete(phase) && `${phase.scenario}/${phase.phase}/${phase.profile}` === expected[index])
    && [3, 7, 11, 15].every(index => {
      // Approved preparation moves static work into setup, not out of the journey.
      const resources = [...phases[index].resources, ...phases[index + 1].resources]
      return !phases[index].resources.some(r => ['catalog', 'overview', 'people_list', 'people_count'].includes(r.category))
        && ['housing', 'personnel'].every(target => ['leaf_js', 'shared_css', ...(target === 'personnel' ? ['css_owner_js'] : [])]
        .every(category => resources.some(r => r.target === target && r.category === category && r.responseEndMs !== null)))
    })
}

/** @param {(reason: {validator: string, field: string, rule: string, itemIndex: number | null}) => void} [onReject] */
export function validateReadyPhase(p, onReject = () => {}) {
  const reject = (field, rule, itemIndex = null) => {
    try { onReject({ validator: 'ready_projection', field, rule, itemIndex }) } catch { /* diagnostic only */ }
    return null
  }
  const bad = (field, rule, index) => { reject(field, rule, index); return true }
  const cost = (value, field) => {
    if (!keys(value, ['requests', 'encodedBytes', 'transferBytes', 'scriptDurationMs'])) return reject(field, 'exact_keys')
    if (!Number.isInteger(value.requests) || value.requests < 0 || value.requests > 512) return reject(`${field}.requests`, 'integer_bounds')
    if (!bytes(value.encodedBytes)) return reject(`${field}.encoded_bytes`, 'integer_bounds')
    if (!bytes(value.transferBytes)) return reject(`${field}.transfer_bytes`, 'integer_bounds')
    if (!ms(value.scriptDurationMs)) return reject(`${field}.script_duration`, 'finite_time_or_null')
    if (value.scriptDurationMs === null && p.unavailable !== true) return reject(`${field}.script_duration`, 'null_requires_unavailable')
    return true
  }
  if (!keys(p, ['scenario', 'phase', 'profile', 'overflow', 'resources', 'stages', 'dom', 'fcpMs', 'document', 'unavailable', 'cost', 'journeyCost'])) return reject('root', 'exact_keys')
  if (!cost(p.cost, 'cost') || !cost(p.journeyCost, 'journey_cost')) return null
  if (!['login', 'housing', 'personnel'].includes(p.scenario)) return reject('scenario', 'enum')
  if (!(p.scenario === 'login' ? ['cold', 'prime', 'warm'] : ['setup', 'first', 'back', 'again']).includes(p.phase)) return reject('phase', 'enum')
  if (!(p.scenario === 'login' ? ['throttled'] : ['unthrottled', 'throttled']).includes(p.profile)) return reject('profile', 'enum')
  if (typeof p.overflow !== 'boolean') return reject('overflow', 'boolean')
  if (typeof p.unavailable !== 'boolean') return reject('unavailable', 'boolean')
  if (!ms(p.fcpMs)) return reject('fcp', 'finite_time_or_null')
  if (!Array.isArray(p.resources) || p.resources.length > 128) return reject('resource_array', 'array_limit')
  if (!Array.isArray(p.dom) || p.dom.length > 32) return reject('dom_array', 'array_limit')
  if (!Array.isArray(p.stages) || p.stages.length > 32) return reject('stage_array', 'array_limit')
  if (!keys(p.document, ['status', 'extraInfoStatus', 'noCache'])) return reject('document', 'exact_keys')
  if (!status(p.document.status)) return reject('document.status', 'status_or_null')
  if (!status(p.document.extraInfoStatus)) return reject('document.extra_status', 'status_or_null')
  if (![null, true, false].includes(p.document.noCache)) return reject('document.no_cache', 'boolean_or_null')
  if (p.resources.some((r, index) => {
    if (!keys(r, ['category', 'target', 'encodedBytes', 'transferBytes', 'startMs', 'requestStartMs', 'responseStartMs', 'responseEndMs', 'cacheHit', 'status'])) return bad('resource.keys', 'exact_keys', index)
    if (!['none', 'housing', 'personnel'].includes(r.target)) return bad('resource.target', 'enum', index)
    if (!bytes(r.encodedBytes)) return bad('resource.encoded_bytes', 'integer_bounds', index)
    if (!bytes(r.transferBytes)) return bad('resource.transfer_bytes', 'integer_bounds', index)
    if (!categories.includes(r.category)) return bad('resource.category', 'enum', index)
    if (typeof r.cacheHit !== 'boolean') return bad('resource.cache_hit', 'boolean', index)
    if (!status(r.status)) return bad('resource.status', 'status_or_null', index)
    return [['startMs', 'resource.start'], ['requestStartMs', 'resource.request_start'], ['responseStartMs', 'resource.response_start'], ['responseEndMs', 'resource.response_end']]
      .some(([key, field]) => !ms(r[key]) && bad(field, 'finite_time_or_null', index))
  })) return null
  if (apis.some((category, index) => p.resources.filter(r => r.category === category).length > 16 && bad('api_count', 'array_limit', index))) return null
  if (p.dom.some((d, index) => {
    if (!keys(d, ['kind', 'visible', 'ms'])) return bad('dom.keys', 'exact_keys', index)
    if (!['fallback', 'heading', 'shell', 'updating'].includes(d.kind)) return bad('dom.kind', 'enum', index)
    if (typeof d.visible !== 'boolean') return bad('dom.visible', 'boolean', index)
    return !ms(d.ms) && bad('dom.time', 'finite_time_or_null', index)
  })) return null
  if (p.stages.some((s, index) => {
    if (!keys(s, ['name', 'ms'])) return bad('stage.keys', 'exact_keys', index)
    if (!stageNames.includes(s.name)) return bad('stage.name', 'enum', index)
    return !ms(s.ms) && bad('stage.time', 'finite_time_or_null', index)
  })) return null
  if (new Set(p.stages.map(s => s.name)).size !== p.stages.length) return reject('stage.names', 'duplicate_stage')
  return p
}

export function readyPhaseComplete(p) {
  if (!validateReadyPhase(p) || p.overflow || p.unavailable) return false
  if (!['fallback', 'heading', 'shell', 'updating'].every(kind => p.dom.some(d => d.kind === kind && d.ms !== null))) return false
  const required = p.scenario === 'login' ? ['goto_done', 'heading', 'identifier', 'password', 'submit', 'ready', 'settled']
    : p.phase === 'setup' ? ['goto_done', 'workbench', 'settled'] : p.phase === 'back' ? ['nav_clicked', 'workbench']
      : ['nav_clicked', 'heading', 'ready', 'settled', ...(p.scenario === 'housing'
        ? ['room_card', 'filter_search', 'filter_apply'] : ['table', 'person_search', 'person_query', 'updating_absent'])]
  if (!required.every(name => p.stages.some(s => s.name === name && s.ms !== null))) return false
  const needed = p.scenario === 'login' ? ['document', 'auth_me'] : p.phase === 'first'
    ? (p.scenario === 'housing' ? ['catalog', 'overview'] : ['people_list', 'people_count']) : []
  if (!needed.every(category => p.resources.some(r => r.category === category && r.startMs !== null && r.responseEndMs !== null))) return false
  if (!['setup', 'back'].includes(p.phase) && !p.dom.some(d => d.kind === 'heading' && d.visible && d.ms !== null)) return false
  if (p.scenario === 'login' && (p.fcpMs === null || p.document.status === null || p.document.extraInfoStatus === null || p.document.noCache !== true)) return false
  return true
}

// Serialized directly as an init script. Only closed projections leave this page.
export function installReadyProbe(scenario, classify, scope = globalThis) {
  scope.__readyChain?.dispose()
  let start = 0, spa = false, overflow = false, dom = [], stages = [], previous = {}, starts = []
  let boundariesOverflow = false
  const bounded = value => Number.isFinite(value) && value >= 0 && value <= 90000 ? value : null
  const now = () => bounded(scope.performance.now() - start)
  const visible = element => Boolean(element && element.getClientRects().length
    && scope.getComputedStyle(element).visibility !== 'hidden' && scope.getComputedStyle(element).display !== 'none')
  const observe = () => {
    const heading = { login: '登录工作台', housing: '住宿房态', personnel: '人员中心' }[scenario]
    const states = {
      fallback: [...scope.document.querySelectorAll('[role="status"]')].some(e => e.textContent === '正在加载页面…' && visible(e)),
      heading: [...scope.document.querySelectorAll('h1,h2,h3')].some(e => e.textContent === heading && visible(e)),
      shell: visible(scope.document.querySelector('.appShell')),
      updating: [...scope.document.querySelectorAll('p,span')].some(e => e.textContent === '正在更新…' && visible(e)),
    }
    for (const [kind, value] of Object.entries(states)) if (previous[kind] !== value) {
      if (dom.length < 32) dom.push({ kind, visible: value, ms: now() })
      else overflow = true
      previous[kind] = value
    }
  }
  const observer = new scope.MutationObserver(observe)
  observer.observe(scope.document, { subtree: true, childList: true, attributes: true, characterData: true })
  scope.__readyChain = {
    begin(isSpa) {
      start = isSpa ? scope.performance.now() : 0; spa = isSpa
      if (!isSpa) { starts = [0]; boundariesOverflow = false }
      else if (starts.length < 4) starts.push(start)
      else boundariesOverflow = true
      overflow = boundariesOverflow; dom = []; stages = []; previous = {}; observe()
    },
    mark(name) { if (stages.length < 32) stages.push({ name, ms: now() }); else overflow = true },
    snapshot() {
      observe()
      const entries = [...scope.performance.getEntriesByType('resource'), ...scope.performance.getEntriesByType('navigation')]
      const total = selected => ({ requests: selected.length,
        encodedBytes: selected.reduce((sum, entry) => sum + entry.encodedBodySize, 0),
        transferBytes: selected.reduce((sum, entry) => sum + entry.transferSize, 0) })
      // ResourceTiming publishes completed requests only. Re-project earlier
      // start intervals so late completions retain their original phase and
      // phase-relative timing; never move them into the observation phase.
      const accounting = (phaseStart, end, isSpa) => {
        const resources = [], counts = {}
        let resourceOverflow = entries.length > 512
        const current = entries.filter(entry => entry.startTime >= phaseStart && entry.startTime < end
          && (!isSpa || entry.entryType !== 'navigation'))
        for (const entry of current) {
          const category = entry.entryType === 'navigation' ? 'document' : classify(entry.name, scope.location.origin)
          if (category === null) continue
          counts[category] = (counts[category] ?? 0) + 1
          if (resources.length >= 128 || (['auth_me', 'auth_csrf', 'portal', 'catalog', 'overview', 'people_list', 'people_count'].includes(category) && counts[category] > 16)) {
            resourceOverflow = true; continue
          }
          const time = field => bounded(entry[field] - phaseStart)
          const pathname = new URL(entry.name, scope.location.origin).pathname
          const target = /\/assets\/(?:HousingOverviewPage-[^/]+\.js|housing-[^/]+\.css)$/.test(pathname) ? 'housing'
            : /\/assets\/(?:PeoplePage-[^/]+\.js|people-[^/]+\.(?:js|css))$/.test(pathname) ? 'personnel' : 'none'
          resources.push({ category, target, encodedBytes: entry.encodedBodySize, transferBytes: entry.transferSize,
            startMs: time('startTime'), requestStartMs: time('requestStart'),
            responseStartMs: time('responseStart'), responseEndMs: entry.responseEnd > 0 ? time('responseEnd') : null,
            cacheHit: entry.transferSize === 0 && entry.decodedBodySize > 0,
            status: Number.isInteger(entry.responseStatus) && entry.responseStatus >= 100 && entry.responseStatus <= 599 ? entry.responseStatus : null })
        }
        return { resources, cost: total(current), journeyCost: total(entries.filter(entry => entry.startTime < end)), overflow: resourceOverflow }
      }
      const current = accounting(start, Infinity, spa)
      const earlier = starts.slice(0, -1).map((phaseStart, index) => accounting(phaseStart, starts[index + 1], index > 0))
      return { ...current, earlier, stages, dom, overflow: overflow || current.overflow,
        fcpMs: spa ? null : bounded(scope.performance.getEntriesByName('first-contentful-paint')[0]?.startTime) }
    },
    dispose() { observer.disconnect(); delete scope.__readyChain },
  }
  scope.__readyChain.begin(false)
}
