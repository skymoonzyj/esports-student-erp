import { expect, it } from 'vitest'
import { previewTaskConfig, sessionViewSchema, suiteConfigSchema, suiteMembers, taskConfigSchema } from './contracts'
import { fixtureConfig, fixtureSettings, fixtureSuiteConfig, fixtureV3Config } from './testSupport/fixtures'
import { pcMobaFactory } from './suites/pcMoba'
import { mobileMobaFactory } from './suites/mobileMoba'

it.each(['pc-moba', 'mobile-moba'] as const)('validates every %s v2 task and difficulty but rejects mixed snapshots', suite => {
  const legacy = fixtureSuiteConfig(suite)
  const tasks = Object.fromEntries(suiteMembers[suite].map(test => {
    const v1 = fixtureConfig(suite, test)
    const v2 = { ...v1, catalog_version: `${suite}-v2`, engine_version: `${suite}-v2` }
    for (const difficulty of [1, 2, 3, 4, 5]) {
      const current = previewTaskConfig(v2, difficulty)
      expect(current.params).toEqual(previewTaskConfig(v1, difficulty).params)
      expect(current.scoring_version).toBe(`${suite}-v1`)
      expect(current.report_version).toBe(`${suite}-v1`)
    }
    expect(taskConfigSchema.safeParse({ ...v2, catalog_version: `${suite}-v4`, engine_version: `${suite}-v4` }).success).toBe(false)
    const upgraded = { ...v2, catalog_version: `${suite}-v3`, engine_version: `${suite}-v3` }
    if (test === 'dodge') {
      // A changed rule may not disguise legacy params as its registered v3 shape.
      expect(taskConfigSchema.safeParse(upgraded).success).toBe(false)
    } else {
      expect(taskConfigSchema.parse(upgraded).params).toEqual(v2.params)
    }
    return [test, v2]
  }))
  const current = { ...legacy, catalog_version: `${suite}-v2`, tasks }
  expect(suiteConfigSchema.parse(current)).toEqual(current)
  const first = legacy.test_ids[0]
  expect(suiteConfigSchema.safeParse({ ...current, tasks: { ...tasks, [first]: legacy.tasks[first] } }).success).toBe(false)
  const session = { id: '33333333-3333-4333-8333-333333333333', suite_id: suite, status: 'in_progress', members: legacy.test_ids, results: [], report_id: null }
  for (const frozen of [legacy, current]) expect(sessionViewSchema.parse({ ...session, suite_config: frozen }).suite_config).toEqual(frozen)
})

it.each(['pc-moba', 'mobile-moba'] as const)('preserves unchanged %s v3 task behavior exactly against v2', suite => {
  const factory = suite === 'pc-moba' ? pcMobaFactory : mobileMobaFactory
  for (const test of suiteMembers[suite].filter(test => test !== 'dodge')) {
    const v3 = fixtureV3Config(suite, test)
    const v2 = { ...v3, catalog_version: `${suite}-v2`, engine_version: `${suite}-v2` }
    const games = [v2, v3].map(config => factory.create(config, 123, fixtureSettings(suite), { width: 1000, height: 562.5 }))
    for (const game of games) {
      game.input({ kind: 'point', at_ms: 0, x: 650, y: 300, button: 2 })
      game.input({ kind: 'move', at_ms: 731, x: .7, y: -.4 })
      game.advanceTo(60000)
    }
    expect(games[1].finish()).toEqual(games[0].finish())
    expect(games[1].scene()).toEqual(games[0].scene())
  }
})
