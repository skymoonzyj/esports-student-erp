// Explicit fixed-board fixtures; historical current/v4 helpers stay frozen.
import catalog from './catalog-v5.json' with { type: 'json' }
import { previewTaskConfig, suiteConfigSchema, type TaskConfig } from '../contracts'

export function fixtureV5Config(testId: 'gridshot' | 'sixshot' | 'spheretrack90', difficulty = 3): TaskConfig {
  const config = suiteConfigSchema.parse(catalog['pc-fps']).tasks[testId]
  if (!config) throw new Error('test is not a suite member')
  return previewTaskConfig(config, difficulty)
}
