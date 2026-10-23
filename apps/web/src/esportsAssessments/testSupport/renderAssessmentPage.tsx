import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderResult } from '@testing-library/react'
import { useEffect, type ReactElement, type ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import { AuthProvider, type AuthUser } from '../../auth/AuthProvider'
import { clearAssessmentPending } from '../pending'

export const studentFixture: AuthUser = {
  account_id: '11111111-1111-4111-8111-111111111111',
  person_id: '77777777-7777-4777-8777-777777777777',
  full_name: '合成评测学员',
  email: null,
  force_password_change: false,
  is_super_admin: false,
  permissions: [],
  scoped_permissions: ['esports_assessments.take', 'esports_assessments.read'],
}

function TestLifecycle({ client, children }: { client: QueryClient; children: ReactNode }) {
  useEffect(() => () => {
    client.clear()
    clearAssessmentPending()
    sessionStorage.clear()
  }, [client])
  return children
}

export function renderAssessmentPage(
  ui: ReactElement,
  {
    initialEntries,
    account,
  }: {
    initialEntries: string[]
    account: AuthUser
  },
): RenderResult & { queryClient: QueryClient } {
  const downstreamFetch = globalThis.fetch
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) {
      return new Response(JSON.stringify(account), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith('/auth/csrf')) {
      return new Response(JSON.stringify({ csrf_token: 'synthetic-assessment-csrf' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return downstreamFetch(input, init)
  }))
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const result = render(
    <QueryClientProvider client={queryClient}>
      <TestLifecycle client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          <AuthProvider>{ui}</AuthProvider>
        </MemoryRouter>
      </TestLifecycle>
    </QueryClientProvider>,
  )
  return { ...result, queryClient }
}
