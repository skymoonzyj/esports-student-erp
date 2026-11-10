import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useLocation } from 'react-router-dom'

import { AssessmentHome } from './AssessmentHome'
import { clearAssessmentPending, savePending } from './pending'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'
import { fixturePending } from './testSupport/fixtures'
import catalogV3 from './testSupport/catalog-v3.json'
import { fixtureV4SuiteConfig } from './testSupport/fixturesV4'
import catalogV5 from './testSupport/catalog-v5.json'
import laneV4 from './testSupport/catalog-moba-v4.json'
import laneV5 from './testSupport/catalog-moba-v5.json'
import { suiteConfigSchema, type SessionView } from './contracts'
import { useAuth } from '../auth/AuthProvider'

vi.mock(import('./availability'), () => ({
  ASSESSMENT_SUITE_AVAILABILITY: {
    'pc-fps': true,
    'mobile-fps': false,
    'pc-moba': false,
    'mobile-moba': false,
  },
}))

const NOW = '2026-09-22T00:00:00.000Z'

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function context(overrides: Record<string, unknown> = {}) {
  return {
    server_now: NOW,
    can_take: true,
    can_read_self: true,
    can_read_reports: true,
    available_suites: ['pc-fps', 'mobile-fps'],
    own_in_progress_sessions: [],
    active_run: null,
    ...overrides,
  }
}

function renderHome(value = context(), superAdmin = false) {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith('/esports-assessments/context')) return json(value)
    if (url.includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    throw new Error(`Unexpected request: ${url}`)
  }))
  return renderAssessmentPage(<AssessmentHome />, {
    initialEntries: ['/esports-assessments'],
    account: { ...studentFixture, is_super_admin: superAdmin },
  })
}

function oldRound(): SessionView {
  return { id: '33333333-3333-4333-8333-333333333333', suite_id: 'pc-fps', suite_config: suiteConfigSchema.parse(catalogV3['pc-fps']), status: 'in_progress', members: ['precision', 'tracking', 'switching', 'recognition'], results: [], report_id: null }
}
function newRound(): SessionView {
  const frozen = fixtureV4SuiteConfig()
  return { ...oldRound(), id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', suite_config: frozen, members: frozen.test_ids }
}
function LocationAndAccount() {
  const location = useLocation()
  const { user, refreshUser } = useAuth()
  return <><output aria-label="当前地址">{location.pathname}</output><output aria-label="当前账号">{user?.account_id}</output><button onClick={() => void refreshUser()}>刷新登录账号</button></>
}

it('freezes a selected MOBA attack mode in creation and uses a new request key after switching mode', async () => {
  const posts: Array<{ attack_mode: string; operation_key: string }> = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/context')) return json(context({ available_suites: ['pc-moba'] }))
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (String(input).endsWith('/sessions') && init?.method === 'POST') {
      posts.push(JSON.parse(String(init.body)))
      return new Response('{}', { status: 500, headers: { 'Content-Type': 'application/json' } })
    }
    throw new Error(`Unexpected request: ${input}`)
  }))
  renderAssessmentPage(<AssessmentHome />, { initialEntries: ['/esports-assessments'], account: { ...studentFixture, is_super_admin: true } })
  const mode = await screen.findByRole('combobox', { name: '端游 MOBA 人物模式' })
  const create = screen.getByRole('button', { name: '端游 MOBA 正式测试' })
  fireEvent.click(create)
  await screen.findByRole('alert')
  await waitFor(() => expect(create).toBeEnabled())
  fireEvent.click(create)
  await waitFor(() => expect(posts).toHaveLength(2))
  await waitFor(() => expect(create).toBeEnabled())
  fireEvent.change(mode, { target: { value: 'ranged' } })
  fireEvent.click(create)
  await waitFor(() => expect(posts).toHaveLength(3))
  await waitFor(() => expect(create).toBeEnabled())
  fireEvent.change(mode, { target: { value: 'melee' } })
  fireEvent.click(create)
  await waitFor(() => expect(posts).toHaveLength(4))
  expect(posts.map(post => post.attack_mode)).toEqual(['melee', 'melee', 'ranged', 'melee'])
  expect(posts[0].operation_key).toBe(posts[1].operation_key)
  expect(posts[2].operation_key).not.toBe(posts[1].operation_key)
  expect(posts[3].operation_key).not.toBe(posts[0].operation_key)
  expect(posts[3].operation_key).not.toBe(posts[2].operation_key)
  expect(screen.getByText(/一个五分钟兵线补刀项目/)).toBeVisible()
})
function conflict() {
  return new Response(JSON.stringify({ status: 409, code: 'esports_active_session', detail: '当前评测状态不允许此操作，请刷新后重试', active_session_id: 'untrusted-not-a-route' }), { status: 409, headers: { 'Content-Type': 'application/json' } })
}

it('offers continuation instead of a create POST for a known old desktop round without blocking other suites', async () => {
  renderHome(context({ available_suites: ['pc-fps', 'pc-moba'], own_in_progress_sessions: [oldRound()] }), true)
  const pc = await screen.findByRole('region', { name: '端游 FPS' })
  expect(within(pc).getByText(/旧版四项.*新版三项/)).toBeVisible()
  expect(within(pc).getByText(/主动确认放弃.*不会自动删除或升级/)).toBeVisible()
  expect(within(pc).queryByRole('button', { name: '端游 FPS 正式测试' })).toBeNull()
  expect(screen.getByRole('button', { name: '端游 MOBA 正式测试' })).toBeEnabled()
  fireEvent.click(within(pc).getByRole('button', { name: '继续已有 端游 FPS 正式轮次' }))
  expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
})

it.each(['v4', 'v5'] as const)('does not describe a frozen %s triad as four tasks on the home page', async version => {
  const frozen = version === 'v4' ? fixtureV4SuiteConfig() : suiteConfigSchema.parse(catalogV5['pc-fps'])
  renderHome(context({ own_in_progress_sessions: [{ ...newRound(), suite_config: frozen }] }))
  const pc = await screen.findByRole('region', { name: '端游 FPS' })
  expect(within(pc).getByRole('button', { name: '继续已有 端游 FPS 正式轮次' })).toBeVisible()
  expect(within(pc).queryByText(/旧版四项/)).toBeNull()
  if (version === 'v4') expect(within(pc).getByText(/旧版三项.*固定靶区/)).toBeVisible()
})
it.each([laneV4, laneV5])('does not call a frozen $catalog_version lane round four sixty-second tasks', async source => {
  const frozen = suiteConfigSchema.parse(source)
  renderHome(context({ available_suites: ['pc-moba'], own_in_progress_sessions: [{ ...oldRound(), suite_id: 'pc-moba', suite_config: frozen, members: frozen.test_ids }] }), true)
  const card = await screen.findByRole('region', { name: '端游 MOBA' })
  expect(within(card).getByRole('button', { name: '继续已有 端游 MOBA 正式轮次' })).toBeVisible()
  expect(within(card).queryByText(/已有旧版四项正式轮次|保留原来的 60 秒/)).toBeNull()
})

it('still creates another available suite when only desktop FPS has an unfinished round', async () => {
  const frozen = suiteConfigSchema.parse(catalogV3['pc-moba'])
  const moba: SessionView = { ...oldRound(), suite_id: 'pc-moba', suite_config: frozen, members: frozen.test_ids }
  const posts: unknown[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/context')) return json(context({ available_suites: ['pc-fps', 'pc-moba'], own_in_progress_sessions: [oldRound()] }))
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (String(input).endsWith('/sessions') && init?.method === 'POST') { posts.push(JSON.parse(String(init.body))); return json(moba) }
    throw new Error(`Unexpected request: ${input}`)
  }))
  renderAssessmentPage(<><AssessmentHome /><LocationAndAccount /></>, { initialEntries: ['/esports-assessments'], account: { ...studentFixture, is_super_admin: true } })
  fireEvent.click(await screen.findByRole('button', { name: '端游 MOBA 正式测试' }))
  await waitFor(() => expect(screen.getByLabelText('当前地址')).toHaveTextContent(`/esports-assessments/sessions/${moba.id}`))
  expect(posts).toEqual([expect.objectContaining({ suite_id: 'pc-moba' })])
})

it('does not bypass the active-attempt gate when an old round can otherwise be continued', async () => {
  renderHome(context({ own_in_progress_sessions: [oldRound()], active_run: { mode: 'formal', attempt_id: '22222222-2222-4222-8222-222222222222', session_id: oldRound().id, suite_id: 'pc-fps', test_id: 'precision', run_until: '2026-09-22T00:01:30.000Z', submit_until: '2026-09-22T00:30:00.000Z' } }))
  expect(await screen.findByRole('button', { name: '继续已有 端游 FPS 正式轮次' })).toBeDisabled()
})

it('refreshes authorized context after a stale-tab active-session conflict and only offers its own continuation', async () => {
  let contextReads = 0
  const posts: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/context')) return json(context({ own_in_progress_sessions: contextReads++ ? [oldRound()] : [] }))
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (String(input).endsWith('/sessions') && init?.method === 'POST') { posts.push(String(init.body)); return conflict() }
    throw new Error(`Unexpected request: ${input}`)
  }))
  renderAssessmentPage(<><AssessmentHome /><LocationAndAccount /></>, { initialEntries: ['/esports-assessments'], account: studentFixture })
  fireEvent.click(await screen.findByRole('button', { name: '端游 FPS 正式测试' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('已有未完成的正式轮次')
  const continueButton = await screen.findByRole('button', { name: '继续已有 端游 FPS 正式轮次' })
  expect(posts).toHaveLength(1)
  expect(screen.getByLabelText('当前地址')).toHaveTextContent('/esports-assessments')
  fireEvent.click(continueButton)
  expect(screen.getByLabelText('当前地址')).toHaveTextContent(`/esports-assessments/sessions/${oldRound().id}`)
  expect(posts).toHaveLength(1)
})

it('requires manual context retry after a conflict refresh fails and retains the operation key for a later new-round create', async () => {
  let contextReads = 0
  const posts: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/context')) { contextReads++; return contextReads === 2 ? new Response('{}', { status: 503 }) : json(context()) }
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (String(input).endsWith('/sessions') && init?.method === 'POST') { posts.push(String(init.body)); return posts.length === 1 ? conflict() : json(newRound()) }
    throw new Error(`Unexpected request: ${input}`)
  }))
  renderAssessmentPage(<><AssessmentHome /><LocationAndAccount /></>, { initialEntries: ['/esports-assessments'], account: studentFixture })
  fireEvent.click(await screen.findByRole('button', { name: '端游 FPS 正式测试' }))
  expect(await screen.findByText(/刷新状态失败/)).toBeVisible()
  expect(posts).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: '重试' }))
  fireEvent.click(await screen.findByRole('button', { name: '端游 FPS 正式测试' }))
  await waitFor(() => expect(screen.getByLabelText('当前地址')).toHaveTextContent(`/esports-assessments/sessions/${newRound().id}`))
  expect(posts).toHaveLength(2)
  expect(posts[1]).toBe(posts[0])
})

it.each([
  { status: 409, code: 'esports_operation_conflict', message: '创建失败，请手动重试；将保留原操作键。' },
  { status: 429, code: 'esports_rate_limited', message: '请求过于频繁，请等待 60 秒后手动重试。' },
  { status: 0, code: 'network_failure', message: '创建失败，请手动重试；将保留原操作键。' },
])('keeps $code as a manual POST retry with the same key and no automatic context loop', async ({ status, code, message }) => {
  let contextReads = 0
  const posts: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/context')) { contextReads++; return json(context()) }
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (String(input).endsWith('/sessions') && init?.method === 'POST') {
      posts.push(String(init.body))
      if (posts.length === 1 && status === 0) throw new TypeError('Failed to fetch')
      return posts.length === 1 ? new Response(JSON.stringify({ status, code }), { status }) : json(newRound())
    }
    throw new Error(`Unexpected request: ${input}`)
  }))
  renderAssessmentPage(<><AssessmentHome /><LocationAndAccount /></>, { initialEntries: ['/esports-assessments'], account: studentFixture })
  fireEvent.click(await screen.findByRole('button', { name: '端游 FPS 正式测试' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(message)
  expect(contextReads).toBe(1)
  expect(posts).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: '端游 FPS 正式测试' }))
  await waitFor(() => expect(screen.getByLabelText('当前地址')).toHaveTextContent(`/esports-assessments/sessions/${newRound().id}`))
  expect(posts[1]).toBe(posts[0])
})

it('does not expose continuation after the conflict refresh withdraws participation permission', async () => {
  let reads = 0
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/context')) return json(reads++ ? context({ can_take: false, own_in_progress_sessions: [oldRound()] }) : context())
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (String(input).endsWith('/sessions') && init?.method === 'POST') return conflict()
    throw new Error(`Unexpected request: ${input}`)
  }))
  renderAssessmentPage(<AssessmentHome />, { initialEntries: ['/esports-assessments'], account: studentFixture })
  fireEvent.click(await screen.findByRole('button', { name: '端游 FPS 正式测试' }))
  expect(await screen.findByText('当前账号仅可查看已授权记录，不能开始评测。')).toBeVisible()
  expect(screen.queryByRole('button', { name: /继续已有|正式测试/ })).toBeNull()
})

it.each(['switch', 'aba', 'unmount'] as const)('ignores a late conflict after an owner boundary: %s', async boundary => {
  let respond!: (response: Response) => void
  let reads = 0
  const account = { ...studentFixture }
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/context')) { reads++; return json(context()) }
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (String(input).endsWith('/sessions') && init?.method === 'POST') return new Promise<Response>(resolve => { respond = resolve })
    throw new Error(`Unexpected request: ${input}`)
  }))
  const mounted = renderAssessmentPage(<><AssessmentHome /><LocationAndAccount /></>, { initialEntries: ['/esports-assessments'], account })
  fireEvent.click(await screen.findByRole('button', { name: '端游 FPS 正式测试' }))
  await waitFor(() => expect(respond).toBeTypeOf('function'))
  if (boundary === 'unmount') mounted.unmount()
  else {
    account.account_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    fireEvent.click(screen.getByRole('button', { name: '刷新登录账号' }))
    await waitFor(() => expect(screen.getByLabelText('当前账号')).toHaveTextContent(account.account_id))
    await screen.findByRole('button', { name: '端游 FPS 正式测试' })
    if (boundary === 'aba') {
      account.account_id = studentFixture.account_id
      fireEvent.click(screen.getByRole('button', { name: '刷新登录账号' }))
      await waitFor(() => expect(screen.getByLabelText('当前账号')).toHaveTextContent(account.account_id))
      await screen.findByRole('button', { name: '端游 FPS 正式测试' })
    }
  }
  const before = reads
  await act(async () => { respond(conflict()) })
  expect(reads).toBe(before)
  expect(screen.queryByText(/已有未完成的正式轮次/)).toBeNull()
  if (boundary !== 'unmount') expect(screen.getByLabelText('当前地址')).toHaveTextContent('/esports-assessments')
})

it('does not expose the previous owner round when an already-started conflict refresh resolves after account switch', async () => {
  const account = { ...studentFixture }
  let finishRefresh!: (response: Response) => void
  let reads = 0
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/context')) {
      reads++
      if (reads === 2) return new Promise<Response>(resolve => { finishRefresh = resolve })
      return json(context())
    }
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (String(input).endsWith('/sessions') && init?.method === 'POST') return conflict()
    throw new Error(`Unexpected request: ${input}`)
  }))
  renderAssessmentPage(<><AssessmentHome /><LocationAndAccount /></>, { initialEntries: ['/esports-assessments'], account })
  fireEvent.click(await screen.findByRole('button', { name: '端游 FPS 正式测试' }))
  await waitFor(() => expect(finishRefresh).toBeTypeOf('function'))
  account.account_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
  fireEvent.click(screen.getByRole('button', { name: '刷新登录账号' }))
  await waitFor(() => expect(screen.getByLabelText('当前账号')).toHaveTextContent(account.account_id))
  await screen.findByRole('button', { name: '端游 FPS 正式测试' })
  await act(async () => { finishRefresh(json(context({ own_in_progress_sessions: [oldRound()] }))) })
  expect(screen.queryByRole('button', { name: /继续已有/ })).toBeNull()
  expect(screen.queryByText(/已有未完成的正式轮次/)).toBeNull()
  expect(screen.getByLabelText('当前地址')).toHaveTextContent('/esports-assessments')
})

it('offers the protected listening preview only to an authorized superadmin', async () => {
  renderHome(context(), true)
  const link = await screen.findByRole('link', { name: '听声辨位（试听）' })
  expect(link).toHaveAttribute('href', '/api/v1/esports-assessments/sound-preview/index.html')
  expect(link).toHaveAttribute('target', '_blank')
  expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  expect(screen.getByText(/试听结果仅在本次页面显示/)).toBeVisible()
})
it('describes the v4 desktop tasks without implying every suite has four items', async () => {
  renderHome(context(), true)
  const pc = await screen.findByRole('region', { name: '端游 FPS' })
  expect(within(pc).getByText(/网格射击.*六目标射击.*90°范围跟枪/)).toBeVisible()
  expect(screen.queryByText(/完成四项后生成报告/)).toBeNull()
})

it('keeps the listening preview closed to a regular account even when it can take assessments', async () => {
  renderHome()
  await screen.findByRole('heading', { name: '天赋评测 2' })
  expect(screen.queryByRole('link', { name: '听声辨位（试听）' })).not.toBeInTheDocument()
})

it.each([
  { can_take: false },
  { can_read_self: false },
  { available_suites: [] },
])('hides the listening preview when the server no longer allows superadmin participation (%j)', async overrides => {
  renderHome(context(overrides), true)
  await screen.findByRole('heading', { name: '天赋评测 2' })
  expect(screen.queryByRole('link', { name: '听声辨位（试听）' })).not.toBeInTheDocument()
})

afterEach(() => {
  cleanup()
  clearAssessmentPending()
  sessionStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('offers a suite only when both the local implementation and server context enable it', async () => {
  renderHome()

  expect(await screen.findByRole('heading', { name: '天赋评测 2' })).toBeVisible()
  const pc = screen.getByRole('region', { name: '端游 FPS' })
  const mobile = screen.getByRole('region', { name: '手游 FPS' })
  expect(within(pc).getByText('可进行')).toBeVisible()
  expect(within(pc).getByRole('button', { name: '端游 FPS 正式测试' })).toBeEnabled()
  expect(within(pc).getByRole('button', { name: '端游 FPS 自由练习' })).toBeEnabled()
  expect(within(mobile).getByText('开发中')).toBeVisible()
  expect(within(mobile).queryByRole('button', { name: '开始正式测试' })).not.toBeInTheDocument()
})

it('does not expose a locally implemented suite when the server keeps it unavailable', async () => {
  renderHome(context({ available_suites: [] }))

  const pc = await screen.findByRole('region', { name: '端游 FPS' })
  expect(within(pc).getByText('暂未开放')).toBeVisible()
  expect(within(pc).queryByRole('button', { name: '开始正式测试' })).not.toBeInTheDocument()
})

it('keeps report navigation for a reader without showing a formal start control', async () => {
  renderHome(context({ can_take: false, can_read_self: true, can_read_reports: true }))

  expect(await screen.findByText('当前账号仅可查看已授权记录，不能开始评测。')).toBeVisible()
  expect(screen.getByRole('link', { name: '查看评测记录' })).toHaveAttribute(
    'href',
    '/esports-assessments/history',
  )
  expect(screen.queryByRole('button', { name: '开始正式测试' })).not.toBeInTheDocument()
})

it('blocks a new start while another attempt owns the account run slot', async () => {
  renderHome(context({
    active_run: {
      mode: 'practice',
      attempt_id: '22222222-2222-4222-8222-222222222222',
      session_id: null,
      suite_id: 'pc-moba',
      test_id: 'dodge',
      run_until: '2026-09-22T00:01:30.000Z',
      submit_until: '2026-09-22T00:30:00.000Z',
    },
  }))

  expect(await screen.findByRole('alert')).toHaveTextContent('已有一项评测正在进行')
  expect(screen.getByRole('button', { name: '端游 FPS 正式测试' })).toBeDisabled()
})

it('uses one stable session creation key after an ambiguous server failure', async () => {
  const bodies: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/context')) return json(context())
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    bodies.push(String(init?.body))
    return new Response(JSON.stringify({ detail: 'failed' }), { status: 500 })
  }))
  renderAssessmentPage(<AssessmentHome />, { initialEntries: ['/esports-assessments'], account: studentFixture })
  fireEvent.click(await screen.findByRole('button', { name: '端游 FPS 正式测试' }))
  await screen.findByRole('alert')
  fireEvent.click(screen.getByRole('button', { name: '端游 FPS 正式测试' }))
  await screen.findByRole('alert')
  expect(bodies).toHaveLength(2)
  expect(bodies[0]).toBe(bodies[1])
})

it('offers server recovery even after the 90-second active slot ended and no local raw exists', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => json(String(input).endsWith('/context') ? context() : { items: [{ mode: 'practice', attempt_id: '22222222-2222-4222-8222-222222222222', session_id: null, suite_id: 'pc-fps', test_id: 'tracking', state: 'awaiting_submission', issued_at: NOW, run_until: '2026-09-22T00:01:30.000Z', submit_until: '2026-09-22T00:30:00.000Z' }], next_cursor: null, visible_total: 1 })))
  renderAssessmentPage(<AssessmentHome />, { initialEntries: ['/esports-assessments'], account: studentFixture })
  expect(await screen.findByRole('link', { name: /恢复练习/ })).toHaveAttribute('href', '/esports-assessments/practice?suite=pc-fps&test=tracking&practice_id=22222222-2222-4222-8222-222222222222')
})

it('warns when a pending result exists only in page memory', async () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new DOMException('quota', 'QuotaExceededError')
  })
  const pending = fixturePending({
    account_id: studentFixture.account_id,
    created_at: new Date().toISOString(),
  })
  expect(savePending(pending).persisted).toBe(false)

  renderHome()

  expect(await screen.findByText('结果暂存仅在当前页面有效，刷新或关闭页面后可能无法恢复。'))
    .toBeVisible()
})
