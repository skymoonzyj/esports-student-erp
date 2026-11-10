import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type * as ThreeModule from 'three'
import { PracticePage } from './PracticePage'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'
import { fixtureAttempt, fixtureConfig, fixtureCurrentConfig, fixtureSettings, fixtureV3Config } from './testSupport/fixtures'
import { fixtureMobaV4Config } from './testSupport/fixturesMobaV4'
import { fixtureMobaV5Config } from './testSupport/fixturesMobaV5'
import laneCatalog from './testSupport/catalog-moba-v5.json'
import type { TaskConfig } from './contracts'

// Keep the page, controller, Host, scene and input real; replace GPU/Canvas only.
vi.mock('three', async original => ({ ...await original<typeof ThreeModule>(), WebGLRenderer: class {
  setPixelRatio() {} setSize() {} render() {} dispose() {}
} }))
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, width: 1000, height: 562.5, top: 0, left: 0, right: 1000, bottom: 562.5, toJSON: () => ({}) })
  const context = new Proxy({}, { get: () => () => {} })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as CanvasRenderingContext2D)
  vi.stubGlobal('requestAnimationFrame', () => 1)
  vi.stubGlobal('cancelAnimationFrame', () => {})
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

const configs: TaskConfig[] = [fixtureConfig, fixtureCurrentConfig, fixtureV3Config].map(fixture => fixture('pc-moba', 'last-hit'))
configs.push(fixtureMobaV4Config('melee'))
configs.push(fixtureMobaV5Config('melee'))
it.each(configs.flatMap(config => ['interrupted', 'expired'].map(terminal => ({ config, terminal }))))(
  'returns discovered $config.catalog_version practice through $terminal to the selected new ranged request', async ({ config, terminal }) => {
    const old = fixtureAttempt({ mode: 'practice', session_id: null, suite_id: 'pc-moba', test_id: 'last-hit', config, settings: fixtureSettings('pc-moba') })
    let state = 'running'
    let discoveries = 0
    const starts: Array<{ attack_mode?: string; operation_key: string }> = []
    const reply = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.endsWith('/context')) return reply({ server_now: old.server_now, can_take: true, can_read_self: true, can_read_reports: true, available_suites: ['pc-moba'], own_in_progress_sessions: [], active_run: null })
      if (url.endsWith('/suites')) return reply({ items: [laneCatalog] })
      if (url.includes('/recovery?')) {
        discoveries++
        return reply({ items: state === 'running' ? [{ mode: 'practice', attempt_id: old.id, session_id: null, suite_id: 'pc-moba', test_id: 'last-hit', state, issued_at: old.issued_at, run_until: old.run_until, submit_until: old.submit_until }] : [], next_cursor: null, visible_total: state === 'running' ? 1 : 0 })
      }
      if (url.endsWith('/interrupt')) { state = 'interrupted'; return reply({ ...old, state }) }
      if (init?.method === 'POST') {
        const request = JSON.parse(String(init.body))
        starts.push(request)
        return reply(fixtureAttempt({ ...old, id: '99999999-9999-4999-8999-999999999999', config: fixtureMobaV5Config('ranged'), settings: request.settings, environment: request.environment, run_until: '2026-09-21T00:06:00Z', submit_until: '2026-09-21T00:07:00Z' }))
      }
      return reply({ attempt: { ...old, state }, saved: null })
    }))
    renderAssessmentPage(<PracticePage />, { initialEntries: ['/esports-assessments/practice?suite=pc-moba&test=last-hit&attack_mode=ranged'], account: { ...studentFixture, is_super_admin: true } })
    expect(await screen.findByRole('region', { name: '恢复与保存' })).toBeVisible()
    if (terminal === 'expired') {
      state = 'expired'
      fireEvent.click(screen.getByRole('button', { name: '重新对账' }))
    } else fireEvent.click(screen.getByRole('button', { name: '结束未完成尝试并重新开始' }))
    const returnToSelector = await screen.findByRole('button', { name: '返回项目列表重新开始' })
    expect(screen.getByText(/选择当前近战／远程人物后新建练习/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '检查设备并进入试练' })).toBeNull()
    fireEvent.click(returnToSelector)
    expect(await screen.findByLabelText('练习人物模式')).toHaveValue('ranged')
    expect(starts).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: '五分钟兵线补刀' }))
    const trial = await screen.findByRole('button', { name: '检查设备并进入试练' })
    await waitFor(() => expect(trial).toBeEnabled())
    fireEvent.click(trial)
    fireEvent.click(await screen.findByRole('button', { name: '结束试练' }))
    fireEvent.click(screen.getByRole('button', { name: '开始练习' }))
    await waitFor(() => expect(starts).toHaveLength(1))
    expect(starts[0].attack_mode).toBe('ranged')
    await waitFor(() => expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'countdown'))
    expect(discoveries).toBe(2)
  },
)
