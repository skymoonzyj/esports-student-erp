import { expect, it } from 'vitest'
import { suiteMembers, validateRaw } from '../contracts'
import { fixtureConfig, fixtureCurrentConfig, fixtureSettings } from '../testSupport/fixtures'
import { mobileMobaFactory } from './mobileMoba'
import { TouchInputRouter } from '../input/touch'

it('assembles exactly the four independent mobile MOBA modules with frozen raw contracts', () => {
  const expected = ['dodge', 'skill_aim', 'rhythm', 'map']
  suiteMembers['mobile-moba'].forEach((test, index) => {
    const config = fixtureConfig('mobile-moba', test)
    const game = mobileMobaFactory.create(config, 123, fixtureSettings('mobile-moba'), { width: 800, height: 450 })
    game.advanceTo(60000)
    expect(game.finish().kind).toBe(expected[index])
    expect(() => validateRaw(config, game.finish())).not.toThrow()
  })
})

it.each(['dodge', 'skill-aim', 'move-attack', 'map-awareness'] as const)('routes current %s configs to a v2-feedback engine', test => {
  const game = mobileMobaFactory.create(fixtureCurrentConfig('mobile-moba', test), 123, fixtureSettings('mobile-moba'), { width: 800, height: 450 })
  expect(game.scene()).toMatchObject({ kind: 'moba', feedback: { version: 'v2', task_id: test } })
})

it('rejects a PC configuration even when a shared engine exists', () => {
  expect(() => mobileMobaFactory.create(fixtureConfig('pc-moba', 'dodge'), 123, fixtureSettings('pc-moba'), { width: 1000, height: 562.5 })).toThrow()
})

it('does not change logical geometry or speed when the mobile viewport changes', () => {
  const games = [{ width: 560, height: 315 }, { width: 1000, height: 562.5 }].map(viewport => mobileMobaFactory.create(fixtureConfig('mobile-moba', 'skill-aim'), 123, fixtureSettings('mobile-moba'), viewport))
  games.forEach(game => { game.input({ kind: 'move', at_ms: 0, x: 1, y: 0 }); game.advanceTo(500) })
  expect(games[0].scene()).toEqual(games[1].scene())
  expect(games[0].scene()).toMatchObject({ player: { position: { x: 590, y: 281.25 } } })
})

it('keeps the mobile 300 ms windup and does not release a held movement stick for an attack', () => {
  const game = mobileMobaFactory.create(fixtureConfig('mobile-moba', 'move-attack'), 123, fixtureSettings('mobile-moba'), { width: 800, height: 450 })
  game.input({ kind: 'fire', at_ms: 0, pressed: true })
  game.advanceTo(299)
  expect(game.finish()).toMatchObject({ rounds: [expect.objectContaining({ attacked: false }), ...Array(29).fill(expect.anything())] })
  game.advanceTo(300)
  expect(game.finish()).toMatchObject({ rounds: [expect.objectContaining({ attacked: true }), ...Array(29).fill(expect.anything())] })
  game.advanceTo(2000)
  game.input({ kind: 'move', at_ms: 2000, x: 1, y: 0 })
  game.input({ kind: 'fire', at_ms: 2100, pressed: true })
  game.advanceTo(2500)
  const raw = game.finish()
  if (raw.kind !== 'rhythm') throw new Error('raw')
  expect(raw.rounds[1]).toEqual({ attacked: false, reached: false, cancelled_count: 1 })
})

it('keeps a map tap separate from another finger moving the joystick and from main-task hits', () => {
  const game = mobileMobaFactory.create(fixtureConfig('mobile-moba', 'map-awareness'), 123, fixtureSettings('mobile-moba'), { width: 1000, height: 562.5 })
  game.advanceTo(3000)
  const scene = game.scene()
  if (scene.kind !== 'moba' || !scene.minimap.event) throw new Error('scene')
  const router = new TouchInputRouter({ bounds: { x: 0, y: 0, width: 1000, height: 562.5 }, logical_size: { width: 1000, height: 562.5 }, sensitivity: 1, controls: fixtureSettings('mobile-moba').controls, get_skill_origin: () => scene.player.position }, action => game.input(action))
  router.down({ pointerId: 1, x: 180, y: 421.875, at_ms: 3000 })
  router.down({ pointerId: 2, ...scene.minimap.event.position, at_ms: 3000, isPrimary: false })
  router.up({ pointerId: 2, ...scene.minimap.event.position, at_ms: 3001 })
  game.advanceTo(3100)
  const after = game.scene(), raw = game.finish()
  if (after.kind !== 'moba' || raw.kind !== 'map') throw new Error('raw')
  expect(after.player.position).toEqual({ x: 509, y: 281.25 })
  expect(raw.events[0]).toEqual({ response: 'correct', latency_ms: 0 })
  expect(raw.main_hits.every(value => !value)).toBe(true)
  expect(router.activeCount).toBe(1)
})
