import { createElement, lazy, useEffect, useSyncExternalStore, type ComponentType } from 'react'
import { isPreloadFailure } from './moduleLoadEvents'

export class ModuleLoadError extends Error {
  readonly preloadFailure: boolean
  readonly retryable: boolean
  constructor(readonly moduleId: string, cause: unknown) {
    super('Page module import failed', { cause })
    this.name = 'ModuleLoadError'
    this.preloadFailure = isPreloadFailure(cause)
    // Vite marks CSS dependencies seen BEFORE they load. Its next import would
    // skip a failed stylesheet and could mount unstyled content. Only a full,
    // explicitly confirmed document refresh can safely reset that helper state.
    this.retryable = !(this.preloadFailure && cause instanceof Error && cause.message.startsWith('Unable to preload CSS for '))
  }
}

export function createLazyModule<P extends object>(
  id: string,
  loader: () => Promise<{ default: ComponentType<P> }>,
  mode: 'suspense' | 'controlled' = 'suspense',
): { Component: ComponentType<P>; retry: () => void; prepare?: () => void } {
  let failure: ModuleLoadError | null = null
  const listeners = new Set<() => void>()
  const load = async () => {
    try {
      return await loader()
    } catch (error) {
      // Only the dynamic import boundary may label an error. A successfully
      // imported component throwing during rendering is deliberately untouched.
      failure = new ModuleLoadError(id, error)
      throw failure
    }
  }
  const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }
  const notify = () => listeners.forEach(listener => listener())

  // Two route leaves opt out of a newly mounted Suspense boundary's reveal
  // delay. This cache owns code only, never an element, props or private data.
  if (mode === 'controlled') {
    type State = { status: 'idle' | 'pending' } | { status: 'ready'; component: ComponentType<P> } | { status: 'error'; error: ModuleLoadError }
    let state: State = { status: 'idle' }
    const snapshot = () => state
    const start = () => {
      if (state.status !== 'idle') return
      state = { status: 'pending' }
      notify()
      void load().then(
        module => { state = { status: 'ready', component: module.default }; notify() },
        (error: ModuleLoadError) => { state = { status: 'error', error }; notify() },
      )
    }
    function ControlledModule(props: P) {
      const value = useSyncExternalStore(subscribe, snapshot, snapshot)
      // No speculative render/denied guard starts loading. StrictMode and all
      // subscribers share the same pending attempt, including across unmounts.
      useEffect(() => { if (value.status === 'idle') start() }, [value])
      if (value.status === 'error') throw value.error
      if (value.status === 'ready') return createElement(value.component, props)
      return createElement('p', { role: 'status' }, '正在加载页面…')
    }
    return {
      Component: ControlledModule,
      prepare: start,
      retry() {
        if (!failure?.retryable) return
        failure = null
        state = { status: 'idle' }
        notify()
      },
    }
  }

  const makeLazy = () => lazy(load)
  let current = makeLazy()
  const snapshot = () => current
  function Module(props: P) {
    const Lazy = useSyncExternalStore(subscribe, snapshot, snapshot)
    return createElement(Lazy, props)
  }
  return {
    Component: Module,
    retry() {
      if (!failure?.retryable) return
      failure = null
      current = makeLazy()
      notify()
    },
  }
}
