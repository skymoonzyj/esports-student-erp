import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AssessmentHome } from './AssessmentHome'
import { AssessmentSessionPage } from './AssessmentSessionPage'
import { PracticePage } from './PracticePage'
import type { SuiteId } from './contracts'
import { sessionViewSchema, suiteMembers } from './contracts'
import catalog from './testSupport/catalog-v1.json'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'

// Keep all release gates closed: the real pages must distinguish preview access
// from general availability, and still honor the live server decision.
vi.mock('./availability', () => ({ ASSESSMENT_SUITE_AVAILABILITY: {
  'pc-fps': false, 'mobile-fps': false, 'pc-moba': false, 'mobile-moba': false,
} }))

const suites = [
  ['pc-fps', '端游 FPS'], ['mobile-fps', '手游 FPS'],
  ['pc-moba', '端游 MOBA'], ['mobile-moba', '手游 MOBA'],
] as const
const id = '33333333-3333-4333-8333-333333333333'
type Page = 'home' | 'formal' | 'practice'

function mount({ suite = 'pc-fps', page = 'home', superAdmin = true, serverOpen = true, canTake = true }: {
  suite?: SuiteId; page?: Page; superAdmin?: boolean; serverOpen?: boolean; canTake?: boolean
} = {}) {
  const view = sessionViewSchema.parse({ id, suite_id: suite, suite_config: catalog[suite], status: 'in_progress', members: [...suiteMembers[suite]], results: [], report_id: null })
  const writes: { path: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const path = String(input)
    let data: unknown
    if (init?.method === 'POST') {
      writes.push({ path, body: JSON.parse(String(init.body)) })
      if (!path.endsWith('/esports-assessments/sessions')) throw new Error('unexpected write')
      data = view
    } else if (path.endsWith('/context')) {
      data = { server_now: '2026-09-22T00:00:00Z', can_take: canTake, can_read_self: true, can_read_reports: true,
        available_suites: serverOpen ? suites.map(([key]) => key) : [], own_in_progress_sessions: [], active_run: null }
    // A previously loaded catalog can outlive a newer context denial.
    } else if (path.endsWith('/suites')) data = { items: Object.values(catalog) }
    else if (path.endsWith(`/sessions/${id}`)) data = view
    else if (path.includes('/recovery?')) data = { items: [], next_cursor: null, visible_total: 0 }
    else throw new Error(`unexpected request: ${path}`)
    return new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })
  }))
  const entry = page === 'formal' ? `/esports-assessments/sessions/${id}`
    : page === 'practice' ? `/esports-assessments/practice?suite=${suite}` : '/esports-assessments'
  renderAssessmentPage(<Routes>
    <Route path="/esports-assessments" element={<AssessmentHome />} />
    <Route path="/esports-assessments/sessions/:sessionId" element={<AssessmentSessionPage />} />
    <Route path="/esports-assessments/practice" element={<PracticePage />} />
  </Routes>, { initialEntries: [entry], account: { ...studentFixture, is_super_admin: superAdmin } })
  return writes
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it.each(suites)('lets a superadmin create and enter a closed %s formal round when the server authorizes it', async (suite, name) => {
  const writes = mount({ suite })
  const card = await screen.findByRole('region', { name })
  expect(within(card).getByText('超管试测')).toBeVisible()
  fireEvent.click(within(card).getByRole('button', { name: `${name} 正式测试` }))
  expect(await screen.findByRole('heading', { name: '正式评测项目' })).toBeVisible()
  expect(screen.getByText('已保存 0 / 4')).toBeVisible()
  expect(writes).toEqual([{ path: '/api/v1/esports-assessments/sessions', body: { suite_id: suite, operation_key: expect.any(String) } }])
})

it.each(suites)('lets a superadmin enter a closed %s practice without allocating a result', async (suite, name) => {
  const writes = mount({ suite })
  const card = await screen.findByRole('region', { name })
  fireEvent.click(within(card).getByRole('button', { name: `${name} 自由练习` }))
  expect(await screen.findByLabelText('练习难度')).toBeVisible()
  expect(screen.getByText(/不生成正式评分或报告/)).toBeVisible()
  expect(writes).toEqual([])
})

it.each(['home', 'formal', 'practice'] as const)('does not let a non-superadmin bypass a closed suite on %s even with a stale open server context', async page => {
  const writes = mount({ page, superAdmin: false })
  if (page === 'home') {
    const card = await screen.findByRole('region', { name: '端游 FPS' })
    expect(within(card).queryByRole('button')).toBeNull()
  } else expect(await screen.findByText(/此专项暂未开放/)).toBeVisible()
  expect(screen.queryByLabelText('练习难度')).toBeNull()
  expect(screen.queryByRole('heading', { name: '正式评测项目' })).toBeNull()
  expect(writes).toEqual([])
})

it.each(['home', 'formal', 'practice'] as const)('does not let a superadmin ignore a closed server decision on %s even with a cached catalog', async page => {
  const writes = mount({ page, serverOpen: false })
  if (page === 'home') {
    const card = await screen.findByRole('region', { name: '端游 FPS' })
    expect(within(card).queryByRole('button')).toBeNull()
  } else expect(await screen.findByText(/此专项暂未开放/)).toBeVisible()
  expect(screen.queryByLabelText('练习难度')).toBeNull()
  expect(screen.queryByRole('heading', { name: '正式评测项目' })).toBeNull()
  expect(writes).toEqual([])
})

it.each(['home', 'formal', 'practice'] as const)('honors a superadmin participation denial on %s', async page => {
  const writes = mount({ page, canTake: false })
  expect(await screen.findByText(page === 'home' ? /不能开始评测/ : /没有参加本专项的权限/)).toBeVisible()
  expect(screen.queryByLabelText('练习难度')).toBeNull()
  expect(screen.queryByRole('button', { name: '端游 FPS 正式测试' })).toBeNull()
  expect(writes).toEqual([])
})
