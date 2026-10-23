import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { MobaFeedback } from './MobaFeedback'
import { createRhythmEngine } from './moba/rhythm'
import { fixtureConfig, fixtureCurrentConfig, fixtureSettings } from './testSupport/fixtures'

function rhythm(current = true) {
  return createRhythmEngine((current ? fixtureCurrentConfig : fixtureConfig)('pc-moba', 'orb-walk'), 123, fixtureSettings('pc-moba'))
}

it('explains that a running legacy round has no live feedback instead of promising a trial', () => {
  const game = rhythm(false)
  const scene = game.scene()
  if (scene.kind !== 'moba') throw new Error('wrong scene')
  render(<MobaFeedback scene={scene} />)
  expect(screen.getByRole('region', { name: '测试反馈' })).toHaveTextContent('旧版不提供实时反馈')
})

it('displays actual attack windup then completion from the running engine', () => {
  const game = rhythm()
  const initial = game.scene()
  if (initial.kind !== 'moba') throw new Error('wrong scene')
  const enemy = initial.targets.find(t => t.type === 'enemy')!
  game.input({ kind: 'point', at_ms: 0, ...enemy.position, button: 0 })
  let scene = game.scene()
  if (scene.kind !== 'moba') throw new Error('wrong scene')
  const view = render(<MobaFeedback scene={scene} />)
  expect(screen.getByRole('region', { name: '测试反馈' })).toHaveTextContent('出手中')
  game.advanceTo(250)
  scene = game.scene()
  if (scene.kind !== 'moba') throw new Error('wrong scene')
  view.rerender(<MobaFeedback scene={scene} />)
  expect(screen.getByRole('region', { name: '测试反馈' })).toHaveTextContent('攻击已完成')
})

it('keeps feedback in the same reserved container when an attack is cancelled', () => {
  const game = rhythm()
  const initial = game.scene()
  if (initial.kind !== 'moba') throw new Error('wrong scene')
  game.input({ kind: 'point', at_ms: 0, ...initial.targets.find(t => t.type === 'enemy')!.position, button: 0 })
  const scene = game.scene()
  if (scene.kind !== 'moba') throw new Error('wrong scene')
  const view = render(<MobaFeedback scene={scene} compact />)
  const region = screen.getByRole('region', { name: '测试反馈' })
  game.input({ kind: 'point', at_ms: 100, ...initial.targets.find(t => t.type === 'checkpoint')!.position, button: 0 })
  const cancelled = game.scene()
  if (cancelled.kind !== 'moba') throw new Error('wrong scene')
  view.rerender(<MobaFeedback scene={cancelled} compact />)
  expect(screen.getByRole('region', { name: '测试反馈' })).toBe(region)
  expect(region).toHaveTextContent('攻击已被移动取消')
})
