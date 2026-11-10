// @vitest-environment node
import { expect, it } from 'vitest'
import type { Page } from '@playwright/test'
import { currentCatalogVersion, exercisePcInput, expectCurrentTaskIdentity, expectFrozenMobaV4TaskIdentity, expectFrozenV3TaskIdentity, expectStableStageLayout } from '../../tests/helpers/esportsAssessment'
import { fixtureV3Config } from './testSupport/fixtures'
import { fixtureV4Config } from './testSupport/fixturesV4'
import { fixtureV5Config } from './testSupport/fixturesV5'
import { fixtureMobaV4Config } from './testSupport/fixturesMobaV4'
import { fixtureMobaV5Config } from './testSupport/fixturesMobaV5'

it('does not mistake viewport scrolling for a resized or locally moved fixed-board canvas', () => {
  const before = { canvas: { x: 336, y: 381, width: 1016, height: 571.5 }, host: { x: 300, y: 281 }, window_scroll: { x: 0, y: 0 }, ancestor_scroll: [{ tag: 'MAIN', left: 0, top: 0 }] }
  const after = { ...before, canvas: { ...before.canvas, y: 180 }, host: { x: 300, y: 80 }, ancestor_scroll: [{ tag: 'MAIN', left: 0, top: 201 }] }
  expect(() => expectStableStageLayout(before, after)).not.toThrow()
  expect(() => expectStableStageLayout(before, { ...after, canvas: { ...after.canvas, width: 1000 } })).toThrow()
  expect(() => expectStableStageLayout(before, { ...after, canvas: { ...after.canvas, y: 181 } })).toThrow()
})

it.each(['gridshot', 'sixshot', 'spheretrack90'] as const)('accepts only the exact current %s identity', testId => {
  const config = fixtureV5Config(testId)
  expect(() => expectCurrentTaskIdentity(config, 'pc-fps', testId)).not.toThrow()
  expect(() => expectCurrentTaskIdentity(fixtureV4Config('pc-fps', testId), 'pc-fps', testId)).toThrow()
  for (const field of ['catalog_version', 'engine_version', 'scoring_version', 'report_version'] as const) {
    expect(() => expectCurrentTaskIdentity({ ...config, [field]: 'pc-fps-v3' }, 'pc-fps', testId)).toThrow()
  }
})

it('does not treat legacy PC v3 as current while retaining its frozen identity check', () => {
  const config = fixtureV3Config('pc-fps', 'precision')
  expect(() => expectFrozenV3TaskIdentity(config, 'pc-fps', 'precision')).not.toThrow()
  expect(() => expectCurrentTaskIdentity(config, 'pc-fps', 'precision')).toThrow()
})

it('pins current suite versions independently of historical fixtures', () => {
  expect(currentCatalogVersion('pc-fps')).toBe('pc-fps-v5')
  expect(currentCatalogVersion('pc-moba')).toBe('pc-moba-v5')
  expect(currentCatalogVersion('mobile-fps')).toBe('mobile-fps-v3')
  expect(currentCatalogVersion('mobile-moba')).toBe('mobile-moba-v3')
})

it('rejects missing or parameter-drifted current configuration', () => {
  expect(() => expectCurrentTaskIdentity(undefined, 'pc-fps', 'gridshot')).toThrow()
  const config = fixtureV5Config('gridshot')
  if (config.params.kind !== 'multi_target') throw new Error('gridshot fixture must be multi-target')
  const drifted = { ...config, params: { ...config.params, fire_interval_ms: 200 } }
  expect(() => expectCurrentTaskIdentity(drifted, 'pc-fps', 'gridshot')).toThrow()
})

it.each([
  ['mobile-fps', 'move-fire'], ['mobile-moba', 'dodge'],
] as const)('keeps %s pinned to registered v3 result identities', (suite, testId) => {
  const config = fixtureV3Config(suite, testId)
  expect(() => expectCurrentTaskIdentity(config, suite, testId)).not.toThrow()
  expect(() => expectCurrentTaskIdentity({ ...config, engine_version: `${suite}-v4` }, suite, testId)).toThrow()
})
it.each(['melee', 'ranged'] as const)('requires the complete current %s lane identity', mode => {
  const config = fixtureMobaV5Config(mode)
  expect(() => expectCurrentTaskIdentity(config, 'pc-moba', 'last-hit')).not.toThrow()
  expect(() => expectCurrentTaskIdentity(fixtureMobaV4Config(mode), 'pc-moba', 'last-hit')).toThrow()
  expect(() => expectFrozenMobaV4TaskIdentity(fixtureMobaV4Config(mode))).not.toThrow()
  expect(() => expectFrozenMobaV4TaskIdentity(config)).toThrow()
  expect(() => expectCurrentTaskIdentity(fixtureV3Config('pc-moba', 'last-hit'), 'pc-moba', 'last-hit')).toThrow()
  expect(() => expectCurrentTaskIdentity({ ...config, duration_ms: 60000 }, 'pc-moba', 'last-hit')).toThrow()
})

function inputRecorder() {
  const events: string[] = []
  const page = {
    getByLabel: () => ({ boundingBox: async () => ({ x: 0, y: 0, width: 1000, height: 600 }) }),
    mouse: {
      move: async () => { events.push('move') },
      click: async () => { events.push('click') },
      down: async () => { events.push('down') },
      up: async () => { events.push('up') },
    },
    waitForTimeout: async (ms: number) => { events.push(`wait:${ms}`) },
  } as unknown as Page
  return { page, events }
}

it('exercises continuous v4 tracking without issuing fire input', async () => {
  const { page, events } = inputRecorder()
  await exercisePcInput(page, 'pc-fps', 'spheretrack90')
  expect(events.filter(event => ['click', 'down', 'up'].includes(event))).toEqual([])
  expect(events.filter(event => event === 'move')).toHaveLength(4)
})

it.each(['gridshot', 'sixshot'] as const)('exercises %s with one held press and four distinct clicks', async testId => {
  const { page, events } = inputRecorder()
  await exercisePcInput(page, 'pc-fps', testId)
  expect(events.filter(event => ['click', 'down', 'up'].includes(event))).toEqual(['down', 'up', 'click', 'click', 'click', 'click'])
  expect(events).toContain('wait:350')
  expect(events.filter(event => event === 'wait:75')).toHaveLength(4)
})
