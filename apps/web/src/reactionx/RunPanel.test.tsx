import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { RunPanel } from './RunPanel'
import type { RunView } from './api'
import fixtures from '../../../../shared/reactionx/v1/fixtures.json'

afterEach(cleanup)

it('treats uncertainty archive as a terminal non-result, not pending result synchronization or local receipt proof', () => {
  const run = { run_id: '11111111-1111-4111-8111-111111111111', connector_id: '22222222-2222-4222-8222-222222222222', status: 'uncertain_closed', manifest: fixtures.cases[0].manifest, lease_expires_at: '2026-11-04T00:00:00.000Z', lease_remaining_ms: 0, stop_confirmed: true, result_saved: false } as RunView
  render(<RunPanel run={run} canOperate={false} canStop={false} busy={false} onStart={vi.fn()} onStop={vi.fn()} onActivate={vi.fn()} />)
  expect(screen.getByRole('status')).toHaveTextContent('执行状态未知，需复测')
  expect(screen.getByText(/后台已归档，不生成成绩、不参与排名/)).toBeVisible()
  expect(screen.getByText(/尚不能确认原连接器已收到并持久化归档凭据/)).toBeVisible()
  expect(screen.queryByText(/保存状态|待同步|状态未知，请刷新确认|已保存|已取消|未开始执行/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /开始|停止|点亮/ })).not.toBeInTheDocument()
})
