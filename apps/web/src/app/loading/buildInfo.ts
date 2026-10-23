type BuildInfoResult = { kind: 'same' | 'changed' | 'unknown' }
const validBuildId = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$/.test(value)

export async function readBuildInfo(signal: AbortSignal): Promise<BuildInfoResult> {
  if (signal.aborted || !validBuildId(__ERP_BUILD_ID__)) return { kind: 'unknown' }
  const controller = new AbortController()
  let finishAbort!: (result: BuildInfoResult) => void
  const aborted = new Promise<BuildInfoResult>(resolve => { finishAbort = resolve })
  const abort = () => { controller.abort(); finishAbort({ kind: 'unknown' }) }
  signal.addEventListener('abort', abort, { once: true })
  const timeout = setTimeout(abort, 5000)
  try {
    return await Promise.race([aborted, (async (): Promise<BuildInfoResult> => {
      try {
        const response = await fetch('/build-info.json', {
          signal: controller.signal, cache: 'no-store', credentials: 'same-origin', redirect: 'error',
        })
        if (!response.ok || !response.headers.get('Content-Type')?.includes('application/json')) return { kind: 'unknown' }
        const info: unknown = await response.json()
        if (controller.signal.aborted || !info || typeof info !== 'object') return { kind: 'unknown' }
        const value = info as Record<string, unknown>
        if (value.schema_version !== 1 || !validBuildId(value.build_id)) return { kind: 'unknown' }
        return { kind: value.build_id === __ERP_BUILD_ID__ ? 'same' : 'changed' }
      } catch {
        return { kind: 'unknown' }
      }
    })()])
  } finally {
    clearTimeout(timeout)
    signal.removeEventListener('abort', abort)
  }
}
