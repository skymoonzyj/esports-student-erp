// Explicit v4 fixture helpers. Never rebind the historical current/v2 aliases.
import catalog from './catalog-v4.json' with { type: 'json' }
import { rawResultSchema, suiteConfigSchema, suiteIdSchema, testIdSchema, type RawResult, type TaskConfig } from '../contracts'
import { fixtureV3Raw } from './fixtures'

export function fixtureV4SuiteConfig(suite_id: string = 'pc-fps') {
  return suiteConfigSchema.parse(catalog[suiteIdSchema.parse(suite_id)])
}

export function fixtureV4Config(suite_id: string, test_id: string): TaskConfig {
  const config = fixtureV4SuiteConfig(suite_id).tasks[testIdSchema.parse(test_id)]
  if (!config) throw new Error('test is not a suite member')
  return config
}

export function fixtureV4Raw(suite_id: string, test_id: string, perfect = true): RawResult {
  const config = fixtureV4Config(suite_id, test_id)
  if (config.params.kind === 'multi_target') return rawResultSchema.parse({ kind: 'multi_target', shots: perfect ? Array.from({ length: config.params.reference_hits }, (_, index) => ({ at_ms: index * 100, hit: true })) : [] })
  if (config.params.kind === 'spheretrack90') return rawResultSchema.parse({ kind: 'spheretrack90', coverage_ms: perfect ? 60000 : 0, loss_count: 0, reacquisition_ms: [] })
  return fixtureV3Raw(suite_id, test_id, perfect)
}
