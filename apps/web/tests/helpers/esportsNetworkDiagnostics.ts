export interface SafeNetworkEvent {
  kind: 'http_error' | 'request_failed' | 'console_error'
  path_category: 'auth_probe' | 'auth_session' | 'assessment' | 'static_asset' | 'navigation' | 'other'
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS' | 'NONE' | 'OTHER'
  status: number | null
  failure_category: 'aborted_navigation' | 'other' | null
  count: number
}

/** Captures categories only; URL strings, bodies and console text never persist. */
export class BoundedNetworkDiagnostics {
  private readonly bins = new Map<string, SafeNetworkEvent>()
  private dropped = 0

  recordResponse(url: string, method: string, status: number): void {
    if (!Number.isInteger(status) || status < 400 || status > 599) return
    this.add({ kind: 'http_error', path_category: pathCategory(url), method: methodCategory(method), status, failure_category: null })
  }
  recordFailure(url: string, method: string, failure: string): void {
    this.add({ kind: 'request_failed', path_category: pathCategory(url), method: methodCategory(method), status: null,
      failure_category: /ERR_ABORTED|NS_BINDING_ABORTED|cancelled|canceled/i.test(failure) ? 'aborted_navigation' : 'other' })
  }
  recordConsole(url: string): void {
    this.add({ kind: 'console_error', path_category: pathCategory(url), method: 'NONE', status: null, failure_category: null })
  }
  snapshot(): { events: SafeNetworkEvent[]; dropped_count: number } {
    return { events: [...this.bins.values()].map(event => ({ ...event })), dropped_count: this.dropped }
  }
  private add(event: Omit<SafeNetworkEvent, 'count'>): void {
    const key = JSON.stringify(event)
    const existing = this.bins.get(key)
    if (existing && existing.count < 999) existing.count++
    else if (existing || this.bins.size >= 24) this.dropped = Math.min(999999, this.dropped + 1)
    else this.bins.set(key, { ...event, count: 1 })
  }
}

function pathCategory(url: string): SafeNetworkEvent['path_category'] {
  let path: string
  try { path = new URL(url).pathname } catch { return 'other' }
  if (path === '/api/v1/auth/me') return 'auth_probe'
  if (/^\/api\/v1\/auth\/(?:login|logout|csrf)$/.test(path)) return 'auth_session'
  if (path.startsWith('/api/v1/esports-assessments/')) return 'assessment'
  if (path.startsWith('/assets/') || /^\/favicon(?:\.ico|\.svg|\.png)?$/.test(path)) return 'static_asset'
  if (path === '/login' || path.startsWith('/esports-assessments')) return 'navigation'
  return 'other'
}
function methodCategory(method: string): SafeNetworkEvent['method'] {
  return ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].includes(method) ? method as SafeNetworkEvent['method'] : 'OTHER'
}
