import { expect, it } from 'vitest'
import { attemptViewSchema, previewTaskConfig, sessionViewSchema, suiteConfigSchema, taskConfigSchema } from './contracts'
import catalogV1 from './testSupport/catalog-v1.json'
import catalogV2 from './testSupport/catalog-v2.json'
import { fixtureAttempt, fixtureConfig, fixtureCurrentConfig, fixtureEnvironment, fixtureSettings, fixtureV3Config } from './testSupport/fixtures'
import { pcFpsFactory } from './suites/pcFps'
import { mobileFpsFactory } from './suites/mobileFps'

const fpsSuites = ['pc-fps', 'mobile-fps'] as const

it.each(fpsSuites)('accepts frozen v1 and current v2 %s tasks without changing scoring/reporting', suite => {
  for (const source of Object.values(catalogV1[suite].tasks)) {
    const v1 = taskConfigSchema.parse(source)
    expect(v1.catalog_version).toBe(`${suite}-v1`)
    const v2 = { ...v1, catalog_version: `${suite}-v2`, engine_version: `${suite}-v2` }
    expect(taskConfigSchema.parse(v2)).toEqual(v2)
    for (const difficulty of [1, 2, 3, 4, 5]) {
      const preview = previewTaskConfig(v2, difficulty)
      expect(preview).toMatchObject({ catalog_version: `${suite}-v2`, engine_version: `${suite}-v2`, scoring_version: `${suite}-v1`, report_version: `${suite}-v1` })
      expect(preview.params).toEqual(previewTaskConfig(v1, difficulty).params)
    }
  }
})

it.each(fpsSuites)('rejects mixed, future and cross-suite %s task versions', suite => {
  const source = fixtureConfig(suite, 'precision')
  for (const patch of [
    { catalog_version: `${suite}-v2` },
    { engine_version: `${suite}-v2` },
    { catalog_version: `${suite}-v2`, engine_version: `${suite}-v2`, scoring_version: `${suite}-v2` },
    { catalog_version: `${suite}-v2`, engine_version: `${suite}-v2`, report_version: `${suite}-v2` },
    { catalog_version: `${suite}-v4`, engine_version: `${suite}-v4` },
    { catalog_version: 'other-v2', engine_version: 'other-v2' },
    { catalog_version: `${suite}-v2`, engine_version: suite === 'pc-fps' ? 'mobile-fps-v2' : 'pc-fps-v2' },
  ]) expect(taskConfigSchema.safeParse({ ...source, ...patch }).success).toBe(false)
  expect(taskConfigSchema.safeParse({ ...source, catalog_version: `${suite}-v2`, engine_version: `${suite}-v2`, params: { ...source.params, fire_interval_ms: 1 } }).success).toBe(false)
})

it.each(fpsSuites)('accepts complete %s v2 catalogs but refuses mixed task catalogs', suite => {
  const old = catalogV1[suite]
  const current = { ...old, catalog_version: `${suite}-v2`, tasks: Object.fromEntries(Object.entries(old.tasks).map(([test, config]) => [test, { ...config, catalog_version: `${suite}-v2`, engine_version: `${suite}-v2` }])) }
  expect(suiteConfigSchema.parse(current)).toEqual(current)
  expect(suiteConfigSchema.parse(old)).toEqual(old)
  expect(suiteConfigSchema.safeParse({ ...current, tasks: { ...current.tasks, precision: old.tasks.precision } }).success).toBe(false)
  expect(suiteConfigSchema.safeParse({ ...old, tasks: { ...old.tasks, precision: current.tasks.precision } }).success).toBe(false)
})

it.each(fpsSuites)('preserves %s attempt version identities when parsing frozen server payloads', suite => {
  for (const version of ['v1', 'v2']) {
    const config = { ...fixtureConfig(suite, 'precision'), catalog_version: `${suite}-${version}`, engine_version: `${suite}-${version}` }
    const attempt = fixtureAttempt({ suite_id: suite, test_id: 'precision', config, settings: fixtureSettings(suite), environment: fixtureEnvironment(suite) })
    expect(attemptViewSchema.parse(JSON.parse(JSON.stringify(attempt))).config).toEqual(config)
  }
})

it.each(['pc-moba', 'mobile-moba'] as const)('accepts frozen and current %s versions without changing score rules', suite => {
  const config = fixtureConfig(suite, 'dodge')
  expect(taskConfigSchema.parse(config)).toEqual(config)
  const current = { ...config, catalog_version: `${suite}-v2`, engine_version: `${suite}-v2` }
  expect(taskConfigSchema.parse(current)).toEqual(current)
  for (const patch of [{ engine_version: `${suite}-v1` }, { scoring_version: `${suite}-v2` }, { report_version: `${suite}-v2` }]) {
    expect(taskConfigSchema.safeParse({ ...current, ...patch }).success).toBe(false)
  }
})

it.each(['pc-fps', 'mobile-fps', 'pc-moba', 'mobile-moba'] as const)('keeps legacy fixtures frozen while supplying the latest %s catalog', suite => {
  const current = suiteConfigSchema.parse(catalogV2[suite])
  expect(current.catalog_version).toBe(`${suite}-v2`)
  for (const test of current.test_ids) {
    const legacy = fixtureConfig(suite, test)
    const latest = fixtureCurrentConfig(suite, test)
    expect(legacy.catalog_version).toBe(`${suite}-v1`)
    expect(latest).toEqual(current.tasks[test])
    expect(latest.params).toEqual(legacy.params)
    expect(latest.scoring_version).toBe(`${suite}-v1`)
    expect(latest.report_version).toBe(`${suite}-v1`)
  }
})

it.each(fpsSuites)('requires an explicit, same-suite frozen %s snapshot in a formal session', suite => {
  const base = { id: '33333333-3333-4333-8333-333333333333', suite_id: suite, status: 'in_progress', members: catalogV1[suite].test_ids, results: [], report_id: null }
  for (const snapshot of [catalogV1[suite], catalogV2[suite]]) {
    const parsed = sessionViewSchema.parse({ ...base, suite_config: snapshot })
    expect(parsed.suite_config).toEqual(snapshot)
  }
  expect(sessionViewSchema.safeParse(base).success).toBe(false)
  const other = suite === 'pc-fps' ? 'mobile-fps' : 'pc-fps'
  expect(sessionViewSchema.safeParse({ ...base, suite_config: catalogV1[other] }).success).toBe(false)
})

it.each([
  ['pc-fps', 'precision'],
  ['mobile-fps', 'precision'],
  ['pc-fps', 'recognition'],
] as const)('keeps %s %s scene and raw behavior identical from v2 to v3', (suite, test) => {
  const factory = suite === 'pc-fps' ? pcFpsFactory : mobileFpsFactory
  const make = (version: 'v2' | 'v3') => factory.create(
    version === 'v2' ? fixtureCurrentConfig(suite, test) : fixtureV3Config(suite, test),
    123,
    fixtureSettings(suite),
    { width: 844, height: 390 },
  )
  const v2 = make('v2'), v3 = make('v3')
  for (const game of [v2, v3]) {
    game.input({ kind: 'look', at_ms: 1999, dx: 123, dy: -50 })
    game.advanceTo(2000)
  }
  expect(v3.scene()).toEqual(v2.scene())
  expect(v3.finish()).toEqual(v2.finish())
})
