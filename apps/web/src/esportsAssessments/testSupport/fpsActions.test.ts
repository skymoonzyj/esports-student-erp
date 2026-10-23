import { expect, it } from 'vitest'
import { pcFpsFactory } from '../suites/pcFps'
import { aimHighlighted, pulse } from './fpsActions'
import { fixtureCurrentConfig, fixtureSettings } from './fixtures'

it('integrates to the input timestamp before aiming at the highlighted target', () => {
  const game = pcFpsFactory.create(
    fixtureCurrentConfig('pc-fps', 'precision'),
    123,
    fixtureSettings('pc-fps'),
    { width: 844, height: 390 },
  )
  aimHighlighted(game, 2000)
  pulse(game, 2000)
  const raw = game.finish()
  if (raw.kind !== 'aim') throw new Error('expected aim result')
  expect(raw.trials[0]).toEqual({ hit: false, latency_ms: 2000 })
  expect(raw.trials[1]).toEqual({ hit: true, latency_ms: 0 })
  expect(raw.shots).toBe(1)
})
