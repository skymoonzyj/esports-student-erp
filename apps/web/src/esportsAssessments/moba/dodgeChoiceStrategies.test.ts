import { describe, expect, it } from 'vitest'
import { nextU32, validateRaw } from '../contracts'
import type { GameEngine, MobaSceneSnapshot } from '../engineRuntime/types'
import { fixtureSettings, fixtureV3Config } from '../testSupport/fixtures'
import { createDodgeChoiceEngine } from './dodgeChoice'

// Preregistered population: every integer seed 1..1000, no filtering or retries.
const seeds = Array.from({ length: 1000 }, (_, index) => index + 1)
const strategies = ['horizontal', 'orbital', 'random', 'idle'] as const
function scene(game: GameEngine) { return game.scene() as MobaSceneSnapshot }
function score(game: GameEngine) {
  const raw = game.finish()
  if (raw.kind !== 'dodge_choice') throw new Error('choice raw required')
  expect(raw.rounds).toHaveLength(30)
  // Official nonnegative HALF_UP, fixed 30-opportunity denominator, integer 0..100 score.
  return Math.floor((100 * raw.rounds.filter(round => !round.hit).length / 30) + .5)
}

describe.each(['pc-moba', 'mobile-moba'] as const)('%s preregistered safe-circle strategies', suite => {
  const config = fixtureV3Config(suite, 'dodge'), settings = fixtureSettings(suite)
  it.each(strategies)('%s blind strategy stays below mean 40 / p95 50 over seeds 1..1000', strategy => {
    const scores = seeds.map(seed => {
      const game = createDodgeChoiceEngine(config, seed, settings)
      // Independent PRNG seed is fixed across all event seeds, never read from engine results.
      let random = 0xc0ffee
      const interval = strategy === 'horizontal' ? 2000 : strategy === 'orbital' ? 100 : 200
      if (strategy !== 'idle') for (let at = 0; at < 60000; at += interval) {
        game.advanceTo(at)
        let x: number, y = 0
        if (strategy === 'horizontal') x = (at / 2000) % 2 === 0 ? 1 : -1
        else if (strategy === 'orbital') {
          // Blind tangent around the world center, based only on the player's position.
          const p = scene(game).player.position
          x = -(p.y - 281.25); y = p.x - 500
          if (x === 0 && y === 0) x = 1
          const length = Math.hypot(x, y); x /= length; y /= length
        } else {
          random = nextU32(random)
          const angle = random / 0x100000000 * 2 * Math.PI
          x = Math.cos(angle); y = Math.sin(angle)
        }
        game.input({ kind: 'move', at_ms: at, x, y })
      }
      game.advanceTo(60000)
      validateRaw(config, game.finish())
      return score(game)
    }).sort((a, b) => a - b)
    const mean = scores.reduce((sum, value) => sum + value, 0) / 1000, p95 = scores[949]
    process.stdout.write(`${JSON.stringify({ suite, strategy, seeds: '1..1000', mean, p95 })}\n`)
    expect(mean).toBeLessThanOrEqual(40)
    expect(p95).toBeLessThanOrEqual(50)
    if (strategy === 'idle') expect(scores.every(value => value === 0)).toBe(true)
  }, 60000)

  it('earns 100 on every seed using only observed warning cues and actual input', () => {
    for (const seed of seeds) {
      const game = createDodgeChoiceEngine(config, seed, settings)
      let commandedRound = 0, stopAt = Infinity
      // 100ms visual polling; no plan, option id, raw result or seed-based steering.
      for (let at = 0; at < 60000; at += 100) {
        if (stopAt <= at) {
          game.input({ kind: 'move', at_ms: stopAt, x: 0, y: 0 })
          stopAt = Infinity
        }
        game.advanceTo(at)
        const s = scene(game)
        if (s.danger_choice!.phase !== 'warning' || commandedRound === s.feedback!.round.current) continue
        commandedRound = s.feedback!.round.current
        const center = s.danger_choice!.safe_zone!.position
        if (suite === 'pc-moba') game.input({ kind: 'point', at_ms: at, ...center, button: 0 })
        else {
          const dx = center.x - s.player.position.x, dy = center.y - s.player.position.y, distance = Math.hypot(dx, dy)
          game.input({ kind: 'move', at_ms: at, x: dx / distance, y: dy / distance })
          stopAt = at + distance / .18
        }
      }
      game.advanceTo(60000)
      validateRaw(config, game.finish())
      expect(score(game), `seed ${seed}`).toBe(100)
    }
    process.stdout.write(`${JSON.stringify({ suite, strategy: 'visible-cue-oracle', seeds: '1..1000', mean: 100, p95: 100 })}\n`)
  }, 60000)
})
