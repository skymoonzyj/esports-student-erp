import { render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { AssistedPeopleRoutes } from './AssistedPeopleRoutes'

const loaded = vi.hoisted(() => ({ requests: 0, create: 0, managed: 0 }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ status: 'authenticated', user: { account_id: 'actor' } }) }))
vi.mock('./MyAssistedRequestsPage', () => { loaded.requests++; return { MyAssistedRequestsPage: () => <p>mine ready</p>, AssistedRequestsPage: ({ view }: { view: string }) => <p>{view} ready</p> } })
vi.mock('./AssistedDirectCreatePage', () => { loaded.create++; return { AssistedDirectCreatePage: () => <p>create ready</p> } })
vi.mock('./ManagedPeoplePage', () => { loaded.managed++; return { ManagedPeoplePage: () => <p>managed ready</p> } })
it('loads only the selected assisted workspace and shares mine/review implementation', async () => {
  expect(loaded).toEqual({ requests: 0, create: 0, managed: 0 })
  const view = render(<AssistedPeopleRoutes view="mine" />)
  await screen.findByText('mine ready')
  expect(loaded).toEqual({ requests: 1, create: 0, managed: 0 })
  view.rerender(<AssistedPeopleRoutes view="review" />)
  await screen.findByText('review ready')
  expect(loaded).toEqual({ requests: 1, create: 0, managed: 0 })
  view.rerender(<AssistedPeopleRoutes view="create" />)
  await screen.findByText('create ready')
  expect(loaded.managed).toBe(0)
})
