import { describe, expect, it } from 'vitest'
import { previewTaskConfig, suiteConfigSchema, suiteMembersForCatalog, taskConfigSchema } from './contracts'
import { fixtureV4Config, fixtureV4SuiteConfig } from './testSupport/fixturesV4'
import { fixtureV5Config } from './testSupport/fixturesV5'
import * as rules from './versionRules'

function v5Suite() {
  const suite = structuredClone(fixtureV4SuiteConfig())
  suite.catalog_version = 'pc-fps-v5'
  for (const config of Object.values(suite.tasks)) {
    if (!config) continue
    config.catalog_version = config.engine_version = 'pc-fps-v5'
    if (config.params.kind === 'multi_target') {
      config.params.pitch_limit_deg = config.test_id === 'gridshot' ? 9 : 12
      config.params.grid_spacing_deg = config.test_id === 'gridshot' ? 9 : 6
    }
  }
  return suite
}

describe('fixed-board v5 frozen contracts', () => {
  it('accepts v5 without changing v4 and keeps the three exact members', () => {
    expect(suiteConfigSchema.parse(v5Suite()).test_ids).toEqual(['gridshot', 'sixshot', 'spheretrack90'])
    expect(suiteMembersForCatalog('pc-fps', 'pc-fps-v5')).toEqual(['gridshot', 'sixshot', 'spheretrack90'])
    expect(fixtureV4Config('pc-fps', 'gridshot').params).toMatchObject({ grid_spacing_deg: 10, pitch_limit_deg: 70 })
    for (const test of ['gridshot', 'sixshot', 'spheretrack90'] as const) {
      expect(fixtureV5Config(test)).toEqual(v5Suite().tasks[test])
      expect(rules.usesCurrentFpsView(fixtureV5Config(test))).toBe(true)
    }
  })

  it('reuses score/report/raw identities with a v5 engine', () => {
    for (const test of ['gridshot', 'sixshot', 'spheretrack90'] as const) {
      expect(rules.registeredRule('pc-fps', test, 'pc-fps-v5')).toEqual({ raw_kind: test === 'spheretrack90' ? test : 'multi_target', scoring_version: 'pc-fps-v4', report_version: 'pc-fps-v4' })
    }
  })

  it('freezes every difficulty and rejects mutated board parameters', () => {
    const suite = suiteConfigSchema.parse(v5Suite())
    for (const [index, size] of [1.5, 1.25, 1, .85, .7].entries()) {
      for (const [test, diameter, spacing, pitch] of [['gridshot', 6, 9, 9], ['sixshot', 1.5, 6, 12]] as const) {
        const config = fixtureV5Config(test, index + 1)
        expect(config.params).toMatchObject({ target_diameter_deg: diameter * size, grid_spacing_deg: spacing, pitch_limit_deg: pitch })
        expect(taskConfigSchema.safeParse({ ...config, params: { ...config.params, pitch_limit_deg: 70 } }).success).toBe(false)
      }
      expect(previewTaskConfig(suite.tasks.spheretrack90!, index + 1).params).toEqual(previewTaskConfig(fixtureV4Config('pc-fps', 'spheretrack90'), index + 1).params)
    }
  })

  it('recognizes only the desktop v4/v5 triads without accepting future or cross-suite identities', () => {
    for (const version of ['pc-fps-v4', 'pc-fps-v5']) expect(rules.isPcFpsTriadCatalog('pc-fps', version)).toBe(true)
    for (const version of ['pc-fps-v1', 'pc-fps-v2', 'pc-fps-v3', 'pc-fps-v6', 'mobile-fps-v5']) expect(rules.isPcFpsTriadCatalog('pc-fps', version)).toBe(false)
    for (const suite of ['mobile-fps', 'pc-moba', 'mobile-moba'] as const) expect(rules.isPcFpsTriadCatalog(suite, 'pc-fps-v5')).toBe(false)
    expect(() => fixtureV5Config('gridshot', 0)).toThrow()
    expect(() => fixtureV5Config('gridshot', 6)).toThrow()
  })
})
