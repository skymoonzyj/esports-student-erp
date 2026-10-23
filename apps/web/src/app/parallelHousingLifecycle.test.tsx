import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import type * as Auth from '../auth/AuthProvider'
import type * as Styles from './routeStyles'
import type * as Housing from '../housing/HousingOverviewPage'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it.each(['revocation', 'logout', 'account replacement'] as const)(
  'real housing router/filter guard never mounts or queries an evaluated page after pending CSS and %s', async reason => {
    vi.resetModules()
    let release!: () => void
    const pending = new Promise<void>(resolve => { release = resolve })
    let evaluated = false, gateStarted = false, completed = false, allowed = true
    vi.doMock('./routeStyles', async original => {
      const actual = await original<typeof Styles>()
      return { ...actual, loadRouteStyles: async (name: string) => {
        if (name === 'HousingOverviewPage') { gateStarted = true; await pending }
        return actual.loadRouteStyles(name)
      } }
    })
    vi.doMock('../housing/HousingOverviewPage', async original => {
      const actual = await original<typeof Housing>()
      evaluated = true
      return actual
    })
    const account = { account_id: 'parallel-housing-a', person_id: 'parallel-person-a', full_name: 'Housing actor',
      email: null, force_password_change: false, permissions: ['housing.read'], scoped_permissions: [] }
    const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
    const requests: string[] = []
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => {
      const path = new URL(String(input), 'http://localhost').pathname
      requests.push(path)
      if (path.endsWith('/auth/me')) return json(account)
      if (path.endsWith('/auth/csrf')) return json({ csrf_token: 'parallel-test' })
      if (path.endsWith('/auth/logout')) return new Response(null, { status: 204 })
      if (path.endsWith('/auth/login')) return json({ user: { ...account, account_id: 'parallel-housing-b', permissions: [] }, csrf_token: 'parallel-next' })
      if (path.endsWith('/portal/context')) return json({ coach_available: false, learning_available: false,
        payments_available: false, housing_management_available: allowed, housing_available: false,
        invitations_available: false, adult_guardian_invitations_available: false, food_profile_available: false,
        food_preferences_onboarding_required: false, learning_mode: null })
      if (path.endsWith('/team-management/context')) return json({ can_open_management: false, can_read_self: false,
        can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
      throw new Error(`Unexpected fixture request: ${path}`)
    }))
    const { AuthProvider, useAuth } = await import('../auth/AuthProvider')
    const lazyModule = await import('./loading/lazyModule')
    const create = lazyModule.createLazyModule
    vi.spyOn(lazyModule, 'createLazyModule').mockImplementation((id, loader, mode) => create(id, async () => {
      const result = await loader()
      if (id === 'HousingOverviewPage') completed = true
      return result
    }, mode))
    const { appRoutes } = await import('./router')
    const { housingKeys } = await import('../housing/housingKeys')
    const { portalKeys } = await import('../portal/api')
    let currentAuth!: ReturnType<typeof Auth.useAuth>
    function Identity() {
      const auth = useAuth()
      useEffect(() => { currentAuth = auth }, [auth])
      return null
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(appRoutes, { initialEntries: ['/housing'] })
    const view = render(<QueryClientProvider client={client}><AuthProvider><Identity /><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
    try {
      await waitFor(() => { expect(gateStarted).toBe(true); expect(evaluated).toBe(true) })
      expect(router.state.location.pathname).toBe('/housing')
      expect(screen.queryByRole('heading', { name: '住宿房态' })).not.toBeInTheDocument()
      expect(requests.filter(path => /\/housing\/(catalog|overview)$/.test(path))).toEqual([])
      client.setQueryData(housingKeys.scope(account.account_id, 'old-revision'), { private: true })
      if (reason === 'revocation') {
        allowed = false
        await act(async () => { await client.invalidateQueries({ queryKey: portalKeys.context(account.account_id) }) })
      } else if (reason === 'logout') {
        await act(async () => currentAuth.logout())
      } else {
        await act(async () => currentAuth.login('fixture', 'fixture'))
      }
      await waitFor(() => expect(router.state.location.pathname).not.toBe('/housing'))
      await waitFor(() => expect(client.getQueryCache().findAll({ queryKey: housingKeys.root(account.account_id) })).toHaveLength(0))
      await act(async () => release())
      await waitFor(() => expect(completed).toBe(true))
      expect(screen.queryByRole('heading', { name: '住宿房态' })).not.toBeInTheDocument()
      expect(screen.queryByRole('list', { name: '房间列表' })).not.toBeInTheDocument()
      expect(requests.filter(path => /\/housing\/(catalog|overview)$/.test(path))).toEqual([])
      if (reason === 'logout') expect(currentAuth.status).toBe('anonymous')
      if (reason === 'account replacement') expect(currentAuth.user?.account_id).toBe('parallel-housing-b')
    } finally {
      view.unmount()
      await act(async () => release())
      router.dispose()
      client.clear()
    }
  },
)
