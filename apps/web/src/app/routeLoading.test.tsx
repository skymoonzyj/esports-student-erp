import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AuthProvider } from '../auth/AuthProvider'
import { appRoutes } from './router'
import type * as LazyModule from './loading/lazyModule'
import type * as ShellModule from '../layout/AppShell'

const observed = vi.hoisted(() => ({ imports: [] as string[], shellEvaluations: 0, completed: new Set<string>(), parentReady: [] as boolean[] }))
vi.mock('./loading/lazyModule', async original => {
  const actual = await original<typeof LazyModule>()
  return { ...actual, createLazyModule: (id: string, loader: Parameters<typeof actual.createLazyModule>[1], mode: Parameters<typeof actual.createLazyModule>[2]) =>
    actual.createLazyModule(id, async () => {
      observed.imports.push(id)
      if (id === 'PortalDashboardPage') observed.parentReady.push(observed.completed.has('AppShell'))
      if (id === 'assessment2-home') observed.parentReady.push(observed.completed.has('assessment2-layout'))
      const result = await loader(); observed.completed.add(id); return result
    }, mode) }
})
vi.mock('../layout/AppShell', async original => {
  observed.shellEvaluations++
  return await original<typeof ShellModule>()
})

function setup(path: string, authenticated = false, restore?: Promise<Response>) {
  const user = { account_id: 'route-loading-account', person_id: 'route-loading-person', full_name: 'Loading actor',
    email: 'loading@example.test', force_password_change: false, permissions: ['accounts.read', 'accounts.manage', 'people.list', 'esports_assessments.read'], scoped_permissions: [] }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost')
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
    if (url.pathname.endsWith('/auth/me')) return restore ?? (authenticated ? json(user) : json({ status: 401 }, 401))
    if (url.pathname.endsWith('/auth/csrf')) return json({ csrf_token: 'loading-test-csrf' })
    if (url.pathname.endsWith('/portal/context')) return json({ coach_available: false, learning_available: false, payments_available: false, housing_management_available: false, housing_available: false, invitations_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null })
    if (url.pathname.endsWith('/team-management/context')) return json({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    if (url.pathname.endsWith('/esports-assessments/context')) return json({ server_now: '2026-10-18T00:00:00Z', can_take: false, can_read_self: false, can_read_reports: false, available_suites: [], own_in_progress_sessions: [], active_run: null })
    if (url.pathname.endsWith('/context')) return json({ can_submit: false, can_review: false, can_direct_create: false, can_read_managed: false })
    return json({ items: [], total: 0, page: 1, page_size: 20 })
  }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] })
  const view = render(<QueryClientProvider client={client}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
  return { router, view }
}
afterEach(() => { vi.unstubAllGlobals(); observed.imports.length = 0 })

it('login does not evaluate the shell or invoke any business loader', async () => {
  setup('/login')
  await screen.findByRole('button', { name: '登录' })
  expect(observed.shellEvaluations).toBe(0)
  expect(observed.imports).toEqual([])
})

it.each([
  ['/register', '创建账号', 'RegistrationStartPage'],
  ['/forgot-password', '找回密码', 'ForgotPasswordPage'],
  ['/activate-profile', '激活已有档案', 'ActivateProfilePage'],
])('public %s loads only its requested page', async (path, heading, module) => {
  setup(path)
  await screen.findByRole('heading', { name: heading })
  expect(observed.imports).toEqual([module])
  expect(observed.shellEvaluations).toBe(0)
})

it('business navigation and history preserve one real shell DOM instance', async () => {
  const { router, view } = setup('/settings/accounts', true)
  await screen.findByRole('heading', { name: '账号管理' })
  const shell = view.container.querySelector('.appShell')
  expect(shell).not.toBeNull()
  // Approved preparation starts permitted code on an unrelated private route,
  // without mounting the page or making its list/count queries.
  expect(observed.imports).toContain('PeoplePage')
  expect(screen.queryByRole('heading', { name: '人员中心' })).not.toBeInTheDocument()
  expect(vi.mocked(fetch).mock.calls.some(([input]) => /\/api\/v1\/people(?:\?|\/counts|$)/.test(String(input)))).toBe(false)
  await act(async () => { await router.navigate('/people') })
  await screen.findByRole('heading', { name: '人员中心' })
  expect(view.container.querySelector('.appShell')).toBe(shell)
  await act(async () => { await router.navigate(-1) })
  await screen.findByRole('heading', { name: '账号管理' })
  expect(view.container.querySelector('.appShell')).toBe(shell)
  expect(observed.imports.filter(id => id === 'AppShell')).toHaveLength(1)
})

it('the two shared-style parent exceptions finish before their child loader can start', async () => {
  const { router } = setup('/', true)
  await screen.findByRole('heading', { name: '工作台' })
  await act(async () => { await router.navigate('/esports-assessments') })
  await screen.findByRole('heading', { name: '天赋评测 2' })
  expect(observed.parentReady).toEqual([true, true])
})

it('a successful public form retains its draft when delayed anonymous restoration completes', async () => {
  let finish!: (response: Response) => void
  setup('/register', false, new Promise(resolve => { finish = resolve }))
  await screen.findByRole('heading', { name: '创建账号' })
  fireEvent.click(screen.getByRole('radio', { name: '学员创建学员账号并完善实名资料' }))
  const input = screen.getByPlaceholderText('请输入邀请码')
  fireEvent.change(input, { target: { value: 'DRAFT42' } })
  await act(async () => finish(new Response(JSON.stringify({ status: 401 }), { status: 401, headers: { 'Content-Type': 'application/json' } })))
  expect(screen.getByPlaceholderText('请输入邀请码')).toBe(input)
  expect(input).toHaveValue('DRAFT42')
})

it('the real housing capability guard rejects access without invoking its controlled page loader', async () => {
  const { router } = setup('/housing', true)
  await screen.findByRole('heading', { name: '工作台' })
  expect(router.state.location.pathname).toBe('/')
  expect(observed.imports).not.toContain('HousingOverviewPage')
  expect(vi.mocked(fetch).mock.calls.some(([input]) => /\/housing\/(catalog|overview)/.test(String(input)))).toBe(false)
})
