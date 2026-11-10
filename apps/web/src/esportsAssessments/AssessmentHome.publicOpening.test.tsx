import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useLocation } from 'react-router-dom'

import { AssessmentHome } from './AssessmentHome'
import { fixtureV4SuiteConfig } from './testSupport/fixturesV4'
import laneCatalog from './testSupport/catalog-moba-v4.json'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'

const SESSION_ID = '33333333-3333-4333-8333-333333333333'
const SUITES = ['pc-fps', 'mobile-fps', 'pc-moba', 'mobile-moba']
const CLOSED_NAMES = ['手游 FPS', '手游 MOBA']

function json(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function Location() {
  return <output aria-label="当前地址">{useLocation().pathname + useLocation().search}</output>
}

function renderHome(overrides: Record<string, unknown> = {}, superAdmin = false) {
  const posts: unknown[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/context')) return json({
      server_now: '2026-11-09T00:00:00.000Z',
      can_take: true,
      can_read_self: true,
      can_read_reports: true,
      available_suites: ['pc-fps'],
      own_in_progress_sessions: [],
      active_run: null,
      ...overrides,
    })
    if (url.includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (url.endsWith('/sessions') && init?.method === 'POST') {
      posts.push(JSON.parse(String(init.body)))
      const body = JSON.parse(String(init.body))
      const suite = body.suite_id === 'pc-moba' ? laneCatalog : fixtureV4SuiteConfig()
      return json({ id: SESSION_ID, suite_id: body.suite_id, suite_config: suite, status: 'in_progress', members: suite.test_ids, results: [], report_id: null })
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  renderAssessmentPage(<><AssessmentHome /><Location /></>, {
    initialEntries: ['/esports-assessments'],
    account: { ...studentFixture, is_super_admin: superAdmin },
  })
  return posts
}

it('lets an ordinary authorized student create the released formal suite using real availability', async () => {
  const posts = renderHome()
  const card = await screen.findByRole('region', { name: '端游 FPS' })
  expect(within(card).getByText('可进行')).toBeVisible()
  fireEvent.click(within(card).getByRole('button', { name: '端游 FPS 正式测试' }))
  await waitFor(() => expect(screen.getByLabelText('当前地址')).toHaveTextContent(`/esports-assessments/sessions/${SESSION_ID}`))
  expect(posts).toEqual([{ suite_id: 'pc-fps', operation_key: expect.any(String) }])
})

it('lets an ordinary authorized student enter released practice using real availability', async () => {
  const posts = renderHome()
  fireEvent.click(await screen.findByRole('button', { name: '端游 FPS 自由练习' }))
  expect(screen.getByLabelText('当前地址')).toHaveTextContent('/esports-assessments/practice?suite=pc-fps')
  expect(posts).toEqual([])
})

it('lets an ordinary authorized student select and create the released five-minute MOBA mode', async () => {
  const posts = renderHome({ available_suites: ['pc-fps', 'pc-moba'] })
  const card = await screen.findByRole('region', { name: '端游 MOBA' })
  expect(within(card).getByText('可进行')).toBeVisible()
  fireEvent.change(within(card).getByRole('combobox', { name: '端游 MOBA 人物模式' }), { target: { value: 'ranged' } })
  fireEvent.click(within(card).getByRole('button', { name: '端游 MOBA 正式测试' }))
  await waitFor(() => expect(posts).toEqual([{ suite_id: 'pc-moba', attack_mode: 'ranged', operation_key: expect.any(String) }]))
})

it('keeps unopened suite actions hidden for an ordinary account even if the server lists them', async () => {
  renderHome({ available_suites: SUITES })
  await screen.findByRole('region', { name: '端游 FPS' })
  for (const name of CLOSED_NAMES) {
    const card = screen.getByRole('region', { name })
    expect(within(card).getByText('开发中')).toBeVisible()
    expect(within(card).queryByRole('button')).not.toBeInTheDocument()
  }
})

it('keeps the server refusal authoritative for the released suite', async () => {
  renderHome({ available_suites: [] })
  const card = await screen.findByRole('region', { name: '端游 FPS' })
  expect(within(card).getByText('暂未开放')).toBeVisible()
  expect(within(card).queryByRole('button')).not.toBeInTheDocument()
})

it.each([{ can_take: false }, { can_read_self: false }])('does not expose released-suite actions without participation authorization: %j', async overrides => {
  renderHome(overrides)
  await screen.findByRole('region', { name: '端游 FPS' })
  expect(screen.queryByRole('button', { name: /正式测试|自由练习/ })).not.toBeInTheDocument()
})

it('distinguishes released desktop FPS from retained unopened superadmin preview', async () => {
  renderHome({ available_suites: SUITES }, true)
  const pc = await screen.findByRole('region', { name: '端游 FPS' })
  expect(within(pc).getByText('可进行')).toBeVisible()
  expect(within(pc).queryByText('超管试测')).not.toBeInTheDocument()
  for (const name of CLOSED_NAMES) {
    const card = screen.getByRole('region', { name })
    expect(within(card).getByText('超管试测')).toBeVisible()
    expect(within(card).getByRole('button', { name: `${name} 正式测试` })).toBeEnabled()
    expect(within(card).getByRole('button', { name: `${name} 自由练习` })).toBeEnabled()
  }
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
