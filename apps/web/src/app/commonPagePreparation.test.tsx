import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import type * as Auth from '../auth/AuthProvider'
import type * as Styles from './routeStyles'

type Capability = boolean | 'pending' | 'error'
const targets = ['HousingOverviewPage', 'PeoplePage']
const privatePage = (path: string) => /^\/api\/v1\/(housing\/(catalog|overview)|people(?:\/counts)?)$/.test(path)
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

async function setup(permissions: string[], scoped: string[] = [], capability: Capability = false,
  status: 'authenticated' | 'anonymous' | 'forced' = 'authenticated', held?: string) {
  vi.resetModules()
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  const started: string[] = [], completed: string[] = [], requests: string[] = []
  vi.doMock('./routeStyles', async original => {
    const actual = await original<typeof Styles>()
    return { ...actual, loadRouteStyles: async (name: string) => {
      if (name === held) await pending
      return actual.loadRouteStyles(name)
    } }
  })
  let account = { account_id: 'prepare-a', person_id: 'prepare-person-a', full_name: 'Preparation actor', email: null,
    force_password_change: status === 'forced', permissions: ['accounts.read', ...permissions], scoped_permissions: scoped }
  let available = capability
  const json = (body: unknown, code = 200) => new Response(JSON.stringify(body), { status: code, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => {
    const path = new URL(String(input), 'http://localhost').pathname
    requests.push(path)
    if (path.endsWith('/auth/me')) return status === 'anonymous' ? json({ status: 401 }, 401) : json(account)
    if (path.endsWith('/auth/csrf')) return json({ csrf_token: 'prepare-test' })
    if (path.endsWith('/auth/logout')) return new Response(null, { status: 204 })
    if (path.endsWith('/auth/login')) return json({ user: account, csrf_token: 'prepare-next' })
    if (path.endsWith('/portal/context')) {
      if (available === 'pending') await pending
      if (available === 'error') return json({ status: 503, detail: 'Unavailable' }, 503)
      return json({ coach_available: false, learning_available: false, payments_available: false,
        housing_management_available: available === true, housing_available: false, invitations_available: false,
        adult_guardian_invitations_available: false, food_profile_available: false,
        food_preferences_onboarding_required: false, learning_mode: null })
    }
    if (path.endsWith('/team-management/context')) return json({ can_open_management: false, can_read_self: false,
      can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    if (path.endsWith('/context')) return json({ can_submit: false, can_review: false, can_direct_create: false, can_read_managed: false })
    return json({ items: [], total: 0, page: 1, page_size: 20 })
  }))
  const { AuthProvider, useAuth } = await import('../auth/AuthProvider')
  const lazyModule = await import('./loading/lazyModule')
  const create = lazyModule.createLazyModule
  vi.spyOn(lazyModule, 'createLazyModule').mockImplementation((id, loader, mode) => create(id, async () => {
    started.push(id)
    const result = await loader(); completed.push(id); return result
  }, mode))
  const { appRoutes } = await import('./router')
  let auth!: ReturnType<typeof Auth.useAuth>
  function Identity() { const value = useAuth(); useEffect(() => { auth = value }, [value]); return null }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(appRoutes, { initialEntries: ['/settings/accounts'] })
  const view = render(<QueryClientProvider client={client}><AuthProvider><Identity /><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
  return { started, completed, requests, router, client, view, release,
    auth: () => auth,
    changeAccount: (replace: boolean, permissions: string[]) => { account = { ...account, account_id: replace ? 'prepare-b' : account.account_id, permissions: ['accounts.read', ...permissions], scoped_permissions: [] } },
    setCapability: (value: Capability) => { available = value },
    async dispose() { view.unmount(); await act(async () => release()); router.dispose(); client.clear() },
  }
}

it.each([
  { name: 'both permitted', permissions: ['people.list', 'housing.read'], scoped: [], capability: true, expected: targets },
  { name: 'people only', permissions: ['people.list'], scoped: [], capability: false, expected: ['PeoplePage'] },
  { name: 'scoped people denied', permissions: [], scoped: ['people.list'], capability: true, expected: [] },
  { name: 'scoped housing permitted', permissions: [], scoped: ['housing.read'], capability: true, expected: ['HousingOverviewPage'] },
  { name: 'housing capability false', permissions: ['housing.read'], scoped: [], capability: false, expected: [] },
  { name: 'housing capability pending', permissions: ['housing.read'], scoped: [], capability: 'pending', expected: [] },
  { name: 'housing capability error', permissions: ['housing.read'], scoped: [], capability: 'error', expected: [] },
] as const)('real restored auth prepares only authorized code: $name', async row => {
  const fixture = await setup([...row.permissions], [...row.scoped], row.capability)
  try {
    await screen.findByRole('heading', { name: '账号查看' })
    await waitFor(() => expect(fixture.completed.filter(id => targets.includes(id)).sort()).toEqual([...row.expected].sort()))
    expect(fixture.started.filter(id => targets.includes(id)).sort()).toEqual([...row.expected].sort())
    expect(fixture.requests.filter(privatePage)).toEqual([])
    expect(screen.queryByRole('heading', { name: '住宿房态' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '人员中心' })).not.toBeInTheDocument()
    expect(fixture.requests.filter(path => path.endsWith('/auth/me'))).toHaveLength(1)
    expect(fixture.requests.filter(path => path.endsWith('/portal/context'))).toHaveLength(1)
    expect(fixture.started.filter(id => !targets.includes(id)).sort()).toEqual(['AccountsPage', 'AppShell'])
  } finally { await fixture.dispose() }
})

it.each(['anonymous', 'forced'] as const)('%s auth never prepares business code', async status => {
  const fixture = await setup(['people.list', 'housing.read'], [], true, status)
  try {
    await waitFor(() => expect(fixture.router.state.location.pathname).toBe(status === 'anonymous' ? '/login' : '/change-password'))
    expect(fixture.started).toEqual([])
    expect(fixture.requests.filter(privatePage)).toEqual([])
    // Existing portal hook is account-enabled even during forced password change.
    // Preparation adds no request and still starts no business module.
    expect(fixture.requests.filter(path => path.endsWith('/portal/context'))).toHaveLength(status === 'forced' ? 1 : 0)
  } finally { await fixture.dispose() }
})

it.each(targets.flatMap(target => ['logout', 'replacement', 'revocation', ...(target === 'HousingOverviewPage' ? ['capability revocation'] : [])].map(reason => ({ target, reason }))))(
  '$target preparation completion after $reason never mounts or queries old private content', async ({ target, reason }) => {
    const housing = target === 'HousingOverviewPage'
    const fixture = await setup([housing ? 'housing.read' : 'people.list'], [], housing, 'authenticated', target)
    try {
      await screen.findByRole('heading', { name: '账号查看' })
      await waitFor(() => expect(fixture.started).toContain(target))
      expect(fixture.completed).not.toContain(target)
      if (reason === 'logout') await act(async () => fixture.auth().logout())
      else if (reason === 'capability revocation') {
        fixture.setCapability(false)
        const { portalKeys } = await import('../portal/api')
        await act(async () => { await fixture.client.invalidateQueries({ queryKey: portalKeys.context('prepare-a') }) })
      }
      else {
        fixture.changeAccount(reason === 'replacement', [])
        await act(async () => fixture.auth().login('fixture', 'fixture'))
      }
      await act(async () => fixture.release())
      await waitFor(() => expect(fixture.completed).toContain(target))
      expect(fixture.requests.filter(privatePage)).toEqual([])
      expect(screen.queryByRole('heading', { name: '住宿房态' })).not.toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: '人员中心' })).not.toBeInTheDocument()
      await act(async () => { await fixture.router.navigate(housing ? '/housing' : '/people') })
      await waitFor(() => expect(fixture.router.state.location.pathname).not.toBe(housing ? '/housing' : '/people'))
      expect(fixture.requests.filter(privatePage)).toEqual([])
      expect(fixture.started.filter(id => id === target)).toHaveLength(1)
    } finally { await fixture.dispose() }
  },
)

it('prepared People code is reused after account replacement with current permissions, never old props or early queries', async () => {
  const fixture = await setup(['people.list', 'people.create'], [], false, 'authenticated', 'PeoplePage')
  try {
    await screen.findByRole('heading', { name: '账号查看' })
    await waitFor(() => expect(fixture.started).toContain('PeoplePage'))
    fixture.changeAccount(true, ['people.list'])
    await act(async () => fixture.auth().login('fixture', 'fixture'))
    await act(async () => fixture.release())
    await waitFor(() => expect(fixture.completed).toContain('PeoplePage'))
    expect(fixture.requests.filter(privatePage)).toEqual([])
    await act(async () => { await fixture.router.navigate('/people') })
    await screen.findByRole('heading', { name: '人员中心' })
    expect(fixture.auth().user?.account_id).toBe('prepare-b')
    expect(screen.queryByRole('link', { name: '新建人员' })).not.toBeInTheDocument()
    expect(fixture.requests.filter(privatePage).length).toBeGreaterThan(0)
    expect(fixture.started.filter(id => id === 'PeoplePage')).toHaveLength(1)
  } finally { await fixture.dispose() }
})
