import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AssessmentHost } from './AssessmentHost'
import { fixtureConfig, fixtureCurrentConfig } from './testSupport/fixtures'
import { pcMobaFactory } from './suites/pcMoba'
import { mobileMobaFactory } from './suites/mobileMoba'
import type { TaskConfig } from './contracts'

vi.mock('./moba/MobaStage', () => ({ MobaStage: ({ canvasRef, inputHandlers }: { canvasRef: React.Ref<HTMLCanvasElement>; inputHandlers: object }) => <canvas ref={canvasRef} aria-label="MOBA 测评场景" {...inputHandlers} /> }))
beforeEach(() => { vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, width: 1000, height: 562.5, top: 0, left: 0, right: 1000, bottom: 562.5, toJSON: () => ({}) }) })
afterEach(() => vi.restoreAllMocks())

function show(config: TaskConfig) {
  const session = { accountId: 'owner', canRestart: true, ready: true, busy: false, error: null, revoked: false, volatile: false, leaseMs: 90000, saved: null, pending: null, serverView: null,
    start: vi.fn(async () => null), submit: vi.fn(async () => {}), interrupt: vi.fn(async () => {}), reconcile: vi.fn(async () => null), retry: vi.fn(async () => {}), leave: vi.fn(async () => true) }
  return render(<AssessmentHost config={config} factory={config.suite_id === 'pc-moba' ? pcMobaFactory : mobileMobaFactory} session={session} onBack={vi.fn()} />)
}

it.each(['last-hit', 'orb-walk', 'dodge'])('explains usable v2 left and right actions for %s', test => {
  show(fixtureCurrentConfig('pc-moba', test))
  const guide = screen.getByRole('region', { name: '本项测试说明' })
  expect(guide).toHaveTextContent('左键')
  expect(guide).toHaveTextContent('右键')
  expect(screen.getByRole('region', { name: '测试反馈' })).toBeInTheDocument()
})

it('does not promise left-click actions to a frozen legacy movement task', () => {
  show(fixtureConfig('pc-moba', 'orb-walk'))
  const guide = screen.getByRole('region', { name: '本项测试说明' })
  expect(guide).toHaveTextContent('右键')
  expect(guide).not.toHaveTextContent('左键')
})

it('explains direct tap dual tasks instead of an ineffective mobile attack control', () => {
  show(fixtureCurrentConfig('mobile-moba', 'map-awareness'))
  expect(screen.getByRole('region', { name: '本项测试说明' })).toHaveTextContent('直接点')
  expect(screen.queryByText('攻击', { selector: 'div' })).not.toBeInTheDocument()
  expect(screen.queryByText('移动', { selector: 'div' })).not.toBeInTheDocument()
  fireEvent.contextMenu(screen.getByLabelText('MOBA 测评场景'))
  expect(screen.getByRole('region', { name: '本项测试说明' })).toBeVisible()
})
