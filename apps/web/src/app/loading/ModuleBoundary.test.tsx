import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Component, Suspense, useEffect, useState, type ComponentType, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthProvider, useAuth } from '../../auth/AuthProvider'
import { createLazyModule as createModule } from './lazyModule'
import { ModuleBoundary } from './ModuleBoundary'
import { installModuleLoadEvents } from './moduleLoadEvents'
import { createStyleGate } from './styleGate'

const account = { account_id: 'account-a', person_id: 'person-a', full_name: '甲', email: null, force_password_change: false, permissions: [] }
const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
let versionRequests = 0
let logoutRequests = 0
let reload: ReturnType<typeof vi.fn>
let version: () => Promise<Response>

beforeEach(() => {
  vi.stubGlobal('__ERP_BUILD_ID__', 'build-a')
  versionRequests = 0
  logoutRequests = 0
  reload = vi.fn()
  version = async () => json({ schema_version: 1, build_id: 'build-a' })
  vi.stubGlobal('location', { reload })
  vi.spyOn(console, 'error').mockImplementation(() => undefined) // React reports intentionally thrown boundary fixtures.
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url === '/build-info.json') { versionRequests++; return version() }
    if (url.endsWith('/auth/me')) return json(account)
    if (url.endsWith('/auth/csrf')) return json({ csrf_token: 'test-only-csrf' })
    if (url.endsWith('/auth/logout')) { logoutRequests++; return new Response(null, { status: 204 }) }
    if (url.endsWith('/auth/login')) return json({ user: { ...account, account_id: 'account-b', full_name: '乙' }, csrf_token: 'next-csrf' })
    throw new Error(`Unexpected request: ${url}`)
  }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

function Scope({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider>{children}</AuthProvider></QueryClientProvider>
}

function Identity({ record }: { record?: (auth: ReturnType<typeof useAuth>, query: QueryClient) => void }) {
  const auth = useAuth()
  const query = useQueryClient()
  useEffect(() => { if (auth.status === 'authenticated') record?.(auth, query) }, [auth, query, record])
  return <><p>{auth.status}:{auth.user?.full_name}</p><button onClick={() => void auth.logout()}>退出测试</button><button onClick={() => void auth.login('test', 'test')}>切换账号</button></>
}

const rejected = () => Promise.reject(new TypeError('Failed to fetch dynamically imported module'))

describe.each(['suspense', 'controlled'] as const)('%s module ownership', mode => {
const createLazyModule = <P extends object>(id: string, loader: () => Promise<{ default: ComponentType<P> }>) => createModule(id, loader, mode)

it('same-account status changes preserve a successful child but real account changes clear it', async () => {
  const module = createLazyModule('stable-status', async () => ({ default: function Draft() { const [value, setValue] = useState(''); return <input aria-label="模块内草稿" value={value} onChange={e => setValue(e.target.value)} /> } }))
  function Parent() { const auth = useAuth(); return <><Identity /><button onClick={() => void auth.refreshUser()}>重新确认身份</button><ModuleBoundary moduleId="stable-status" retry={module.retry}><Suspense><module.Component /></Suspense></ModuleBoundary></> }
  render(<Scope><Parent /></Scope>)
  await screen.findByText('authenticated:甲')
  const input = await screen.findByLabelText('模块内草稿')
  fireEvent.change(input, { target: { value: '同账号未保存内容' } })
  const originalFetch = globalThis.fetch
  let finish!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn<typeof fetch>((input, init) => String(input).endsWith('/auth/me') ? new Promise(resolve => { finish = resolve }) : originalFetch(input, init)))
  fireEvent.click(screen.getByRole('button', { name: '重新确认身份' }))
  await screen.findByText('loading:甲')
  expect(screen.getByLabelText('模块内草稿')).toBe(input)
  expect(input).toHaveValue('同账号未保存内容')
  await act(async () => finish(json(account)))
  await screen.findByText('authenticated:甲')
  expect(screen.getByLabelText('模块内草稿')).toBe(input)
  await userEvent.click(screen.getByRole('button', { name: '切换账号' }))
  await screen.findByText('authenticated:乙')
  expect(screen.getByLabelText('模块内草稿')).not.toBe(input)
  expect(screen.getByLabelText('模块内草稿')).toHaveValue('')
})

it('a pending import resolving after account switch mounts only the current account subtree', async () => {
  let finish!: (value: { default: ComponentType<object> }) => void
  const mounted: string[] = []
  const module = createLazyModule<object>('pending-switch', () => new Promise(resolve => { finish = resolve }))
  function Current() { const { user } = useAuth(); useEffect(() => { mounted.push(user!.account_id) }, [user]); return <p>当前模块:{user?.full_name}</p> }
  render(<Scope><Identity /><ModuleBoundary moduleId="pending-switch" retry={module.retry}><Suspense fallback={<p>等待模块</p>}><module.Component /></Suspense></ModuleBoundary></Scope>)
  await screen.findByText('authenticated:甲')
  await userEvent.click(screen.getByRole('button', { name: '切换账号' }))
  await screen.findByText('authenticated:乙')
  await act(async () => finish({ default: Current }))
  expect(await screen.findByText('当前模块:乙')).toBeVisible()
  expect(mounted).toEqual(['account-b'])
})

it('status-only recovery reset cancels the old probe without retaining its late update claim', async () => {
  let oldProbe!: (response: Response) => void
  version = () => versionRequests === 1 ? new Promise(resolve => { oldProbe = resolve }) : Promise.resolve(json({ schema_version: 1, build_id: 'build-a' }))
  const module = createLazyModule('status-probe', rejected)
  function Parent() { const auth = useAuth(); return <><Identity /><button onClick={() => void auth.refreshUser()}>刷新身份状态</button><ModuleBoundary moduleId="status-probe" retry={module.retry}><Suspense><module.Component /></Suspense></ModuleBoundary></> }
  render(<Scope><Parent /></Scope>)
  await screen.findByText('authenticated:甲')
  await screen.findByText('页面模块加载失败，请检查网络后重试')
  const originalFetch = globalThis.fetch
  let finishRestore!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn<typeof fetch>((input, init) => String(input).endsWith('/auth/me') ? new Promise(resolve => { finishRestore = resolve }) : originalFetch(input, init)))
  const before = versionRequests
  fireEvent.click(screen.getByRole('button', { name: '刷新身份状态' }))
  await screen.findByText('loading:甲')
  await waitFor(() => expect(versionRequests).toBeGreaterThan(before))
  await act(async () => oldProbe(json({ schema_version: 1, build_id: 'old-status-release' })))
  expect(screen.queryByText('系统已更新')).not.toBeInTheDocument()
  await act(async () => finishRestore(json(account)))
  await screen.findByText('authenticated:甲')
  expect(screen.queryByText('系统已更新')).not.toBeInTheDocument()
})

it('changing module identity clears the old successful private subtree', async () => {
  function Draft() { const [value, setValue] = useState(''); return <input aria-label="模块身份草稿" value={value} onChange={e => setValue(e.target.value)} /> }
  function Parent() { const [id, setId] = useState('a'); return <><Identity /><button onClick={() => setId('b')}>换模块</button><ModuleBoundary moduleId={id} retry={() => undefined}><Draft /></ModuleBoundary></> }
  render(<Scope><Parent /></Scope>)
  await screen.findByText('authenticated:甲')
  const input = screen.getByLabelText('模块身份草稿')
  fireEvent.change(input, { target: { value: 'private-a' } })
  fireEvent.click(screen.getByRole('button', { name: '换模块' }))
  expect(screen.getByLabelText('模块身份草稿')).not.toBe(input)
  expect(screen.getByLabelText('模块身份草稿')).toHaveValue('')
})

it('concurrent and later consumers keep their own boundary IDs after one shared CSS failure', async () => {
  const uninstall = installModuleLoadEvents()
  let reject!: (error: Error) => void, calls = 0, pageImports = 0
  const gate = createStyleGate(() => { calls++; return new Promise((_, fail) => { reject = fail }) })
  const modules = ['first', 'second', 'later'].map(id => ({ id, ...createLazyModule(id, async () => { await gate(); pageImports++; return { default: () => <p>不应挂载</p> } }) }))
  function Parent() {
    const [later, setLater] = useState(false)
    return <><Identity /><button onClick={() => setLater(true)}>打开另一个页面</button><p>无关页面仍可用</p>{modules.slice(0, later ? 3 : 2).map(module => <ModuleBoundary key={module.id} moduleId={module.id} retry={module.retry}><Suspense fallback={<p>等待共享样式</p>}><module.Component /></Suspense></ModuleBoundary>)}</>
  }
  try {
    render(<Scope><Parent /></Scope>)
    await screen.findByText('authenticated:甲')
    expect(screen.getAllByText(mode === 'controlled' ? '正在加载页面…' : '等待共享样式')).toHaveLength(2)
    expect(calls).toBe(1)
    expect(pageImports).toBe(0)
    const error = new Error('Unable to preload CSS for /assets/shared.css')
    window.dispatchEvent(Object.assign(new Event('vite:preloadError'), { payload: error }))
    await act(async () => reject(error))
    expect(await screen.findAllByText('页面样式资源无法局部重试，请刷新页面。')).toHaveLength(2)
    await userEvent.click(screen.getByRole('button', { name: '打开另一个页面' }))
    await waitFor(() => expect(screen.getAllByText('页面样式资源无法局部重试，请刷新页面。')).toHaveLength(3))
    expect(screen.getByText('无关页面仍可用')).toBeVisible()
    expect(pageImports).toBe(0)
    expect(calls).toBe(1)
  } finally { uninstall() }
})

it('an owner JS failure waits for explicit retry and then keeps the successful child mounted', async () => {
  let calls = 0, mounts = 0
  const gate = createStyleGate(async () => { if (++calls === 1) throw new TypeError('owner JS unavailable') })
  const module = createLazyModule('owned', async () => { await gate(); return { default: function Page() { useEffect(() => { mounts++ }, []); return <p>带样式的成功页面</p> } } })
  render(<Scope><ModuleBoundary moduleId="owned" retry={module.retry}><Suspense><module.Component /></Suspense></ModuleBoundary></Scope>)
  await screen.findByText('页面模块加载失败，请检查网络后重试')
  expect(calls).toBe(1)
  await userEvent.click(screen.getByRole('button', { name: '重试加载' }))
  await screen.findByText('带样式的成功页面')
  act(() => module.retry())
  expect(calls).toBe(2)
  expect(mounts).toBe(1)
})

it('retry_does_not_remount_provider_or_parent_draft', async () => {
  let attempts = 0
  const module = createLazyModule('panel', async () => { if (++attempts === 1) return rejected(); return { default: () => <p>模块已恢复</p> } })
  const identities: Array<{ auth: ReturnType<typeof useAuth>; query: QueryClient }> = []
  const record = (auth: ReturnType<typeof useAuth>, query: QueryClient) => identities.push({ auth, query })
  function Parent() {
    const [draft, setDraft] = useState('')
    return <><Identity record={record} /><input aria-label="父页面草稿" value={draft} onChange={e => setDraft(e.target.value)} /><ModuleBoundary moduleId="panel" retry={module.retry}><Suspense fallback={<p>加载中</p>}><module.Component /></Suspense></ModuleBoundary></>
  }
  render(<Scope><Parent /></Scope>)
  await screen.findByText('authenticated:甲')
  await screen.findByText('页面模块加载失败，请检查网络后重试')
  await userEvent.type(screen.getByLabelText('父页面草稿'), '未保存草稿')
  await userEvent.click(screen.getByRole('button', { name: '重试加载' }))
  expect(await screen.findByText('模块已恢复')).toBeVisible()
  expect(screen.getByLabelText('父页面草稿')).toHaveValue('未保存草稿')
  expect(new Set(identities.map(item => item.query)).size).toBe(1)
  expect(new Set(identities.map(item => item.auth)).size).toBe(1)
  expect(attempts).toBe(2)
  expect(reload).not.toHaveBeenCalled()
})

it('version_probe_failure_does_not_logout', async () => {
  version = async () => { throw new TypeError('offline') }
  const module = createLazyModule('offline', rejected)
  render(<Scope><Identity /><ModuleBoundary moduleId="offline" retry={module.retry}><Suspense><module.Component /></Suspense></ModuleBoundary></Scope>)
  await screen.findByText('页面模块加载失败，请检查网络后重试')
  await waitFor(() => expect(versionRequests).toBeGreaterThan(0))
  expect(screen.getByText('authenticated:甲')).toBeVisible()
  expect(screen.queryByText('系统已更新')).not.toBeInTheDocument()
  expect(logoutRequests).toBe(0)
  expect(reload).not.toHaveBeenCalled()
})

it('refresh_requires_confirmation', async () => {
  version = async () => json({ schema_version: 1, build_id: 'new-release' })
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  const module = createLazyModule('changed', rejected)
  render(<Scope><ModuleBoundary moduleId="changed" retry={module.retry}><Suspense><module.Component /></Suspense></ModuleBoundary></Scope>)
  await screen.findByText('系统已更新')
  expect(reload).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '刷新页面' }))
  expect(confirm).toHaveBeenCalledWith('刷新会丢失尚未保存的内容，是否继续？')
  expect(reload).not.toHaveBeenCalled()
  confirm.mockReturnValue(true)
  await userEvent.click(screen.getByRole('button', { name: '刷新页面' }))
  expect(reload).toHaveBeenCalledTimes(1)
})

it('late_import_after_account_change_cannot_restore_old_view', async () => {
  let finish!: (value: { default: ComponentType<object> }) => void
  const module = createLazyModule<object>('slow', () => new Promise(resolve => { finish = resolve }))
  function Protected() {
    const auth = useAuth()
    return <><Identity />{auth.status === 'authenticated' && auth.user?.account_id === 'account-a' ? <ModuleBoundary moduleId="slow" retry={module.retry}><Suspense fallback={<p>旧视图加载中</p>}><module.Component /></Suspense></ModuleBoundary> : <p>无旧账号受保护视图</p>}</>
  }
  render(<Scope><Protected /></Scope>)
  await screen.findByText(mode === 'controlled' ? '正在加载页面…' : '旧视图加载中')
  await userEvent.click(screen.getByRole('button', { name: '退出测试' }))
  await userEvent.click(screen.getByRole('button', { name: '切换账号' }))
  await screen.findByText('authenticated:乙')
  await act(async () => finish({ default: () => <p>旧账号私密视图</p> }))
  expect(screen.queryByText('旧账号私密视图')).not.toBeInTheDocument()
  expect(screen.getByText('无旧账号受保护视图')).toBeVisible()
  expect(reload).not.toHaveBeenCalled()
})

it('late version response cannot mark the new account view as updated', async () => {
  let finish!: (value: Response) => void
  version = () => new Promise(resolve => { finish = resolve })
  const module = createLazyModule('late-version', rejected)
  function Protected() {
    const { user } = useAuth()
    return <><Identity />{user?.account_id === 'account-a' ? <ModuleBoundary moduleId="late-version" retry={module.retry}><Suspense><module.Component /></Suspense></ModuleBoundary> : <p>新页面</p>}</>
  }
  render(<Scope><Protected /></Scope>)
  await screen.findByText('页面模块加载失败，请检查网络后重试')
  await userEvent.click(screen.getByRole('button', { name: '切换账号' }))
  await screen.findByText('authenticated:乙')
  await act(async () => finish(json({ schema_version: 1, build_id: 'new-release' })))
  expect(screen.queryByText('系统已更新')).not.toBeInTheDocument()
  expect(screen.getByText('新页面')).toBeVisible()
})

it('successful module retry is a no-op and normal navigation never probes versions', async () => {
  let mounts = 0
  const module = createLazyModule('success', async () => ({ default: function Page() { useEffect(() => { mounts++ }, []); return <p>成功页面</p> } }))
  // Isolate retry from the distinct anonymous -> authenticated identity reset.
  function Authorized() { const { status } = useAuth(); return status === 'authenticated' ? <ModuleBoundary moduleId="success" retry={module.retry}><Suspense><module.Component /></Suspense></ModuleBoundary> : null }
  const view = render(<Scope><Authorized /></Scope>)
  await screen.findByText('成功页面')
  act(() => module.retry())
  view.rerender(<Scope><Authorized /></Scope>)
  expect(mounts).toBe(1)
  expect(versionRequests).toBe(0)
  expect(reload).not.toHaveBeenCalled()
})

class ExistingBusinessBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() { return this.state.failed ? <p>原有业务错误处理</p> : this.props.children }
}

it('ordinary business render errors pass through without a version probe', async () => {
  function Broken(): ReactNode { throw new Error('business failure') }
  const module = createLazyModule('business', async () => ({ default: Broken }))
  render(<Scope><ExistingBusinessBoundary><ModuleBoundary moduleId="business" retry={module.retry}><Suspense><module.Component /></Suspense></ModuleBoundary></ExistingBusinessBoundary></Scope>)
  await screen.findByText('原有业务错误处理')
  expect(screen.queryByText('系统已更新')).not.toBeInTheDocument()
  expect(versionRequests).toBe(0)
  expect(reload).not.toHaveBeenCalled()
})

it('repeated cached import rejection stays local without automatic retries', async () => {
  let attempts = 0
  const module = createLazyModule('cached', () => { attempts++; return rejected() })
  render(<Scope><p>父页面仍在</p><ModuleBoundary moduleId="cached" retry={module.retry}><Suspense><module.Component /></Suspense></ModuleBoundary></Scope>)
  await screen.findByText('页面模块加载失败，请检查网络后重试')
  await userEvent.click(screen.getByRole('button', { name: '重试加载' }))
  await screen.findByText('页面模块加载失败，请检查网络后重试')
  expect(attempts).toBe(2)
  expect(screen.getByText('父页面仍在')).toBeVisible()
  expect(reload).not.toHaveBeenCalled()
})

it('Vite CSS preload rejection cannot retry into an unstyled successful child', async () => {
  const uninstall = installModuleLoadEvents()
  const cssError = new Error('Unable to preload CSS for /assets/reports-deadbeef.css')
  let attempts = 0
  const module = createLazyModule('css', async () => {
    if (++attempts === 1) {
      window.dispatchEvent(Object.assign(new Event('vite:preloadError', { cancelable: true }), { payload: cssError }))
      throw cssError
    }
    return { default: () => <p>不应挂载的无样式页面</p> }
  })
  try {
    render(<Scope><ModuleBoundary moduleId="css" retry={module.retry}><Suspense><module.Component /></Suspense></ModuleBoundary></Scope>)
    await screen.findByText('页面模块加载失败，请检查网络后重试')
    expect(screen.getByRole('button', { name: '重试加载' })).toBeDisabled()
    act(() => module.retry())
    expect(attempts).toBe(1)
    expect(screen.queryByText('不应挂载的无样式页面')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '刷新页面' })).toBeEnabled()
    expect(reload).not.toHaveBeenCalled()
  } finally { uninstall() }
})

})
