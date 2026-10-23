import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode, useEffect, useState, type ComponentType } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type * as LazyModule from './loading/lazyModule'

const observed = { modules: new Map<string, ReturnType<typeof LazyModule.createLazyModule>>() }
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ status: 'authenticated', user: { account_id: 'parallel-owner' } }) }))

function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.resetModules()
  observed.modules.clear()
  vi.stubGlobal('__ERP_BUILD_ID__', 'parallel-test')
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ schema_version: 1, build_id: 'parallel-test' }))))
  vi.spyOn(console, 'error').mockImplementation(() => undefined) // Expected boundary errors.
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

async function setup(name: 'HousingOverviewPage' | 'PeoplePage' | 'AccountsPage', background = false) {
  const css = deferred<void>(), code = deferred<void>()
  const evaluated = vi.fn(), mounted = vi.fn()
  function Content() {
    useEffect(() => { mounted() }, [])
    const [draft, setDraft] = useState('')
    return <input aria-label="loaded draft" value={draft} onChange={e => setDraft(e.target.value)} />
  }
  const page = async () => { evaluated(); await code.promise; return { [name]: Content } }
  vi.doMock('../housing/HousingOverviewPage', page)
  vi.doMock('../people/PeoplePage', page)
  vi.doMock('../settings/AccountsPage', page)
  const { createStyleGate } = await import('./loading/styleGate')
  const cssImport = vi.fn<() => Promise<void>>().mockImplementationOnce(() => css.promise).mockResolvedValue(undefined)
  const gate = createStyleGate(cssImport)
  vi.doMock('./routeStyles', () => ({ loadRouteStyles: () => gate() }))
  const { installModuleLoadEvents } = await import('./loading/moduleLoadEvents')
  const uninstall = installModuleLoadEvents()
  const lazyModule = await import('./loading/lazyModule')
  const create = lazyModule.createLazyModule
  vi.spyOn(lazyModule, 'createLazyModule').mockImplementation((...args) => {
    const module = create(...args)
    observed.modules.set(args[0], module)
    return module
  })
  const pages = await import('./lazyPages')
  const Page = pages[name] as ComponentType<object>
  const view = render(background ? <p>Unrelated private page</p> : <StrictMode><Page /></StrictMode>)
  const prepare = () => name === 'HousingOverviewPage' ? pages.prepareHousingOverviewPageCode() : pages.preparePeoplePageCode()
  if (background) { prepare(); prepare() }
  return { css, code, evaluated, mounted, cssImport, Page, view, uninstall, prepare,
    cssFailure() {
      const error = new Error('Unable to preload CSS for /assets/fixture.css')
      window.dispatchEvent(Object.assign(new Event('vite:preloadError'), { payload: error }))
      return error
    },
  }
}

describe.each(['HousingOverviewPage', 'PeoplePage'] as const)('%s parallel resources', name => {
  it('prepares only code/styles and shares the pending attempt with subsequent navigation', async () => {
    const fixture = await setup(name, true)
    try {
      await waitFor(() => expect(fixture.evaluated).toHaveBeenCalledOnce())
      expect(fixture.mounted).not.toHaveBeenCalled()
      expect(fetch).not.toHaveBeenCalled()
      fixture.view.rerender(<StrictMode><fixture.Page /></StrictMode>)
      await act(async () => { fixture.css.resolve(); fixture.code.resolve() })
      const input = await screen.findByLabelText('loaded draft')
      fireEvent.change(input, { target: { value: 'prepared draft' } })
      fixture.prepare()
      expect(screen.getByLabelText('loaded draft')).toBe(input)
      expect(input).toHaveValue('prepared draft')
      expect(fixture.evaluated).toHaveBeenCalledOnce()
      expect(fixture.cssImport).toHaveBeenCalledOnce()
    } finally { fixture.uninstall() }
  })

  it.each(['css', 'code'] as const)('background %s failure remains silent until navigation, without automatic retry', async failure => {
    const fixture = await setup(name, true)
    try {
      await waitFor(() => expect(fixture.evaluated).toHaveBeenCalledOnce())
      await act(async () => {
        if (failure === 'css') { fixture.css.reject(fixture.cssFailure()); fixture.code.resolve() }
        else { fixture.css.resolve(); fixture.code.reject(new TypeError('page unavailable')) }
      })
      fixture.prepare()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(screen.getByText('Unrelated private page')).toBeVisible()
      expect(fetch).not.toHaveBeenCalled()
      expect(fixture.mounted).not.toHaveBeenCalled()
      expect(fixture.cssImport).toHaveBeenCalledOnce()
      fixture.view.rerender(<fixture.Page />)
      expect(await screen.findByRole('alert')).toBeVisible()
      if (failure === 'css') expect(screen.getByRole('button', { name: '重试加载' })).toBeDisabled()
      else expect(screen.getByRole('button', { name: '重试加载' })).toBeEnabled()
    } finally { fixture.uninstall() }
  })

  it.each(['css', 'code'] as const)('starts both resources but mounts only after both succeed (%s first), preserving loaded state', async first => {
    const fixture = await setup(name)
    try {
      await waitFor(() => expect(fixture.evaluated).toHaveBeenCalledOnce())
      expect(fixture.cssImport).toHaveBeenCalledOnce()
      expect(fixture.mounted).not.toHaveBeenCalled()
      await act(async () => fixture[first].resolve())
      expect(screen.queryByLabelText('loaded draft')).not.toBeInTheDocument()
      expect(fixture.mounted).not.toHaveBeenCalled()
      await act(async () => fixture[first === 'css' ? 'code' : 'css'].resolve())
      const input = await screen.findByLabelText('loaded draft')
      fireEvent.change(input, { target: { value: 'retained' } })
      act(() => observed.modules.get(name)!.retry())
      fixture.view.rerender(<StrictMode><fixture.Page /></StrictMode>)
      expect(screen.getByLabelText('loaded draft')).toBe(input)
      expect(input).toHaveValue('retained')
      expect(fixture.evaluated).toHaveBeenCalledOnce()
      expect(fixture.cssImport).toHaveBeenCalledOnce()
    } finally { fixture.uninstall() }
  })

  it.each(['css', 'code'] as const)('prioritizes sticky CSS when both fail (%s first), observing late JS rejection without mounting', async first => {
    const fixture = await setup(name)
    try {
      await waitFor(() => expect(fixture.evaluated).toHaveBeenCalledOnce())
      const cssError = fixture.cssFailure()
      const fail = (part: 'css' | 'code') => fixture[part].reject(part === 'css' ? cssError : new TypeError('module unavailable'))
      await act(async () => fail(first))
      if (first === 'code') expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      else expect(await screen.findByText('页面样式资源无法局部重试，请刷新页面。')).toBeVisible()
      await act(async () => fail(first === 'css' ? 'code' : 'css'))
      expect(await screen.findByText('页面样式资源无法局部重试，请刷新页面。')).toBeVisible()
      expect(screen.getByRole('button', { name: '重试加载' })).toBeDisabled()
      act(() => observed.modules.get(name)!.retry())
      expect(fixture.cssImport).toHaveBeenCalledOnce()
      expect(fixture.evaluated).toHaveBeenCalledOnce()
      expect(fixture.mounted).not.toHaveBeenCalled()
    } finally { fixture.uninstall() }
  })

  it.each(['css', 'code'] as const)('never mounts successful code with failed CSS (%s first)', async first => {
    const fixture = await setup(name)
    try {
      await waitFor(() => expect(fixture.evaluated).toHaveBeenCalledOnce())
      await act(async () => first === 'code' ? fixture.code.resolve() : fixture.css.reject(fixture.cssFailure()))
      expect(fixture.mounted).not.toHaveBeenCalled()
      await act(async () => first === 'code' ? fixture.css.reject(fixture.cssFailure()) : fixture.code.resolve())
      expect(await screen.findByRole('alert')).toBeVisible()
      expect(screen.getByRole('button', { name: '重试加载' })).toBeDisabled()
      expect(fixture.mounted).not.toHaveBeenCalled()
    } finally { fixture.uninstall() }
  })

  it('does not revive an unmounted consumer when parallel resources arrive late', async () => {
    const fixture = await setup(name)
    try {
      await waitFor(() => expect(fixture.evaluated).toHaveBeenCalledOnce())
      fixture.view.unmount()
      await act(async () => { fixture.code.resolve(); fixture.css.resolve() })
      expect(fixture.mounted).not.toHaveBeenCalled()
      expect(screen.queryByLabelText('loaded draft')).not.toBeInTheDocument()
    } finally { fixture.uninstall() }
  })

  it('permits only explicit JS-owner retry and reuses successfully imported page code', async () => {
    const fixture = await setup(name)
    try {
      await waitFor(() => expect(fixture.evaluated).toHaveBeenCalledOnce())
      await act(async () => fixture.code.resolve())
      await act(async () => fixture.css.reject(new TypeError('owner JS unavailable')))
      expect(await screen.findByRole('alert')).toBeVisible()
      expect(fixture.mounted).not.toHaveBeenCalled()
      expect(fixture.cssImport).toHaveBeenCalledOnce()
      expect(screen.getByRole('button', { name: '重试加载' })).toBeEnabled()
      fireEvent.click(screen.getByRole('button', { name: '重试加载' }))
      expect(await screen.findByLabelText('loaded draft')).toBeVisible()
      expect(fixture.cssImport).toHaveBeenCalledTimes(2)
      expect(fixture.evaluated).toHaveBeenCalledOnce()
    } finally { fixture.uninstall() }
  })

  it('reports retryable page JS failure only after successful CSS', async () => {
    const fixture = await setup(name)
    try {
      await waitFor(() => expect(fixture.evaluated).toHaveBeenCalledOnce())
      await act(async () => fixture.code.reject(new TypeError('page JS unavailable')))
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      await act(async () => fixture.css.resolve())
      expect(await screen.findByRole('alert')).toBeVisible()
      expect(screen.getByRole('button', { name: '重试加载' })).toBeEnabled()
      expect(fixture.mounted).not.toHaveBeenCalled()
    } finally { fixture.uninstall() }
  })
})

it('keeps other pages serial: Accounts does not import until its CSS succeeds', async () => {
  const fixture = await setup('AccountsPage')
  try {
    await act(async () => {})
    expect(fixture.cssImport).toHaveBeenCalledOnce()
    expect(fixture.evaluated).not.toHaveBeenCalled()
    await act(async () => fixture.css.resolve())
    await waitFor(() => expect(fixture.evaluated).toHaveBeenCalledOnce())
    await act(async () => fixture.code.resolve())
    expect(await screen.findByLabelText('loaded draft')).toBeVisible()
  } finally { fixture.uninstall() }
})
