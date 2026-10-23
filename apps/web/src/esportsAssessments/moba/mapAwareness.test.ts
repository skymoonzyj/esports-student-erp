import { expect, it } from 'vitest'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { createMapEngine } from './mapAwareness'
import { taskConfigSchema, validateRaw } from '../contracts'

it('offers exactly 30 independent main targets and 12 map events', () => {
  const game = createMapEngine(fixtureConfig('mobile-moba', 'map-awareness'), 123, fixtureSettings('mobile-moba'))
  for (let time = 0; time < 60000; time += 1000) {
    game.advanceTo(time)
    const scene = game.scene()
    if (scene.kind !== 'moba') throw new Error('scene')
    expect(scene.minimap.visible).toBe(true)
    for (const t of scene.targets.filter(t => t.type === 'main' && t.state === 'active')) game.input({ kind: 'point', at_ms: time, ...t.position, button: 0 })
    if (scene.minimap.event?.state === 'active') game.input({ kind: 'point', at_ms: time, ...scene.minimap.event.position, button: 0 })
  }
  game.advanceTo(60000)
  const raw = game.finish()
  if (raw.kind !== 'map') throw new Error('raw')
  expect(raw.main_hits).toEqual(Array(30).fill(true))
  expect(raw.events).toEqual(Array.from({ length: 12 }, () => ({ response: 'correct', latency_ms: 0 })))
  expect(raw.unprompted_count).toBe(0)
})
it('consumes a first wrong map choice, separates unprompted clicks, and never moves for map taps', () => {
  const game = createMapEngine(fixtureConfig('pc-moba', 'map-awareness'), 123, fixtureSettings('pc-moba'))
  const initial = game.scene()
  game.input({ kind: 'point', at_ms: 0, x: 830, y: 10, button: 0 })
  game.advanceTo(3000)
  const scene = game.scene()
  if (scene.kind !== 'moba' || initial.kind !== 'moba' || !scene.minimap.event) throw new Error('scene')
  const correct = scene.minimap.event.position
  game.input({ kind: 'point', at_ms: 3100, x: correct.x < 910 ? 955 : 865, y: correct.y, button: 0 })
  game.input({ kind: 'point', at_ms: 3200, ...correct, button: 0 })
  game.advanceTo(60000)
  const end = game.scene(), raw = game.finish()
  if (end.kind !== 'moba' || raw.kind !== 'map') throw new Error('raw')
  expect(raw.events[0]).toEqual({ response: 'wrong', latency_ms: 100 })
  expect(raw.events.slice(1).every(e => e.response === 'missed' && e.latency_ms === 1000)).toBe(true)
  expect(raw.unprompted_count).toBe(1)
  expect(raw.main_hits.every(v => !v)).toBe(true)
  expect(end.player.position).toEqual(initial.player.position)
})
it('does not accept a response at event expiry or a main response at test expiry', () => {
  const game = createMapEngine(fixtureConfig('pc-moba', 'map-awareness'), 123, fixtureSettings('pc-moba'))
  game.advanceTo(3000)
  const scene = game.scene()
  if (scene.kind !== 'moba' || !scene.minimap.event) throw new Error('scene')
  game.input({ kind: 'point', at_ms: 4000, ...scene.minimap.event.position, button: 0 })
  game.input({ kind: 'point', at_ms: 60000, ...scene.targets[0].position, button: 0 })
  const raw = game.finish()
  if (raw.kind !== 'map') throw new Error('raw')
  expect(raw.events[0]).toEqual({ response: 'missed', latency_ms: 1000 })
  expect(raw.unprompted_count).toBe(1)
  expect(raw.main_hits.every(v => !v)).toBe(true)
})

it.each([1, 2, 3, 4, 5])('practice level %i changes only map visibility time, not touch area or opportunities', difficulty => {
  const base = fixtureConfig('mobile-moba', 'map-awareness')
  if (base.params.kind !== 'map') throw new Error('config')
  const window = 1000 * [1.4, 1.2, 1, .85, .7][difficulty - 1]
  const config = taskConfigSchema.parse({ ...base, difficulty, params: { ...base.params, event_window_ms: window } })
  const game = createMapEngine(config, 42, fixtureSettings('mobile-moba'))
  for (let k = 0; k < 12; k++) {
    const start = 3000 + k * 5000
    game.advanceTo(start + window - .01)
    const scene = game.scene()
    if (scene.kind !== 'moba') throw new Error('scene')
    expect(scene.minimap.bounds).toEqual({ x: 820, y: 0, width: 180, height: 110 })
    expect(scene.minimap.event?.state).toBe('active')
    game.advanceTo(start + window)
    const expired = game.scene()
    if (expired.kind !== 'moba') throw new Error('scene')
    expect(expired.minimap.event).toBeNull()
  }
  game.advanceTo(60000)
  const raw = game.finish()
  if (raw.kind !== 'map') throw new Error('raw')
  expect(raw.events).toEqual(Array.from({ length: 12 }, () => ({ response: 'missed', latency_ms: window })))
  expect(raw.main_hits).toHaveLength(30)
  expect(() => validateRaw(config, raw)).not.toThrow()
})
