export type Appearance = { theme: "clear" | "command"; revision: number }

const CACHE_KEY = "erp.appearance.v1"
const REQUEST_TIMEOUT_MS = 10_000

export function validateAppearance(value: unknown): Appearance {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("系统外观数据无效，请重试。")
  }
  const data = value as Record<string, unknown>
  if ((data.theme !== "clear" && data.theme !== "command") ||
    !Number.isSafeInteger(data.revision) || (data.revision as number) < 1) {
    throw new Error("系统外观数据无效，请重试。")
  }
  return { theme: data.theme, revision: data.revision as number }
}

function cachedAppearance(): Appearance {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    const cached = raw ? JSON.parse(raw) as unknown : null
    if (typeof cached === "object" && cached !== null && "version" in cached && cached.version === 1) {
      return validateAppearance(cached)
    }
  } catch { /* Storage is optional and never blocks application startup. */ }
  return { theme: "clear", revision: 1 }
}

function applyAppearance(value: Appearance) {
  document.documentElement.dataset.uiTheme = value.theme
  let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
  if (!meta) {
    meta = document.createElement("meta")
    meta.name = "theme-color"
    document.head.append(meta)
  }
  meta.content = value.theme === "command" ? "#14253d" : "#f4f7f6"
}

export function createAppearanceStore() {
  let value = cachedAppearance()
  // Cache revisions are hints, not trusted server history. The first authoritative
  // response may correct an old/restored/manually edited cache in either direction.
  let authoritativeRevision = 0
  const listeners = new Set<() => void>()
  let flight: { controller: AbortController; promise: Promise<void> } | null = null

  function accept(candidate: Appearance) {
    const next = validateAppearance(candidate)
    if (next.revision <= authoritativeRevision) return
    authoritativeRevision = next.revision
    value = next
    applyAppearance(value)
    try { localStorage.setItem(CACHE_KEY, JSON.stringify({ version: 1, ...value })) } catch { /* Optional cache. */ }
    listeners.forEach((listener) => listener())
  }

  async function load(controller: AbortController): Promise<void> {
    try {
      // Public configuration must not invoke the authenticated client's session invalidation.
      const response = await fetch("/api/v1/appearance", {
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
      })
      if (!response.ok) return
      const next = validateAppearance(await response.json())
      if (!controller.signal.aborted) accept(next)
    } catch { /* Keep the last valid visual state; login and business requests stay independent. */ }
  }

  function refresh(): Promise<void> {
    if (flight) return flight.promise
    const controller = new AbortController()
    let finishAborted!: () => void
    const aborted = new Promise<void>((resolve) => { finishAborted = resolve })
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    const onAbort = () => {
      window.clearTimeout(timeout)
      finishAborted()
    }
    controller.signal.addEventListener("abort", onAbort, { once: true })
    // Bound the whole read, including response.json(). Racing the abort also
    // releases callers if a transport ignores cancellation; load fences late data.
    const promise = Promise.race([load(controller), aborted]).finally(() => {
      window.clearTimeout(timeout)
      controller.signal.removeEventListener("abort", onAbort)
      if (flight?.controller === controller) flight = null
    })
    flight = { controller, promise }
    return flight.promise
  }

  return {
    getSnapshot: () => value,
    getAuthoritativeSnapshot: (): Appearance | null => authoritativeRevision > 0 ? value : null,
    subscribe: (listener: () => void) => {
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
    apply: () => applyAppearance(value),
    start: () => {
      applyAppearance(value)
      void refresh()
      const visibleRefresh = () => {
        if (document.visibilityState === "visible") void refresh()
      }
      const interval = window.setInterval(visibleRefresh, 60_000)
      window.addEventListener("focus", visibleRefresh)
      document.addEventListener("visibilitychange", visibleRefresh)
      return () => {
        window.clearInterval(interval)
        window.removeEventListener("focus", visibleRefresh)
        document.removeEventListener("visibilitychange", visibleRefresh)
        flight?.controller.abort()
        flight = null
      }
    },
    refresh,
    accept,
  }
}

export const appearanceStore = createAppearanceStore()
export type AppearanceStore = ReturnType<typeof createAppearanceStore>
