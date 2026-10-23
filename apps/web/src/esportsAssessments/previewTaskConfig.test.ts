import { expect, it } from 'vitest'
import { previewTaskConfig, suiteMembers, taskConfigSchema } from './contracts'
import { fixtureConfig } from './testSupport/fixtures'

it('previews every registered difficulty without changing suite/version identity', () => {
  for (const [suite, members] of Object.entries(suiteMembers)) for (const test of members) {
    const base = fixtureConfig(suite, test)
    for (const difficulty of [1, 2, 3, 4, 5]) {
      const preview = previewTaskConfig(base, difficulty)
      expect(taskConfigSchema.parse(preview)).toEqual(preview)
      expect(preview.difficulty).toBe(difficulty)
      expect(preview.suite_id).toBe(base.suite_id)
      expect(preview.catalog_version).toBe(base.catalog_version)
      if (difficulty === 3) expect(preview).toEqual(base)
    }
  }
})
it('rejects invalid base and difficulty before registry indexing', () => {
  const base = fixtureConfig('pc-fps', 'precision')
  for (const difficulty of [0, 6, 1.5, NaN, Infinity]) expect(() => previewTaskConfig(base, difficulty)).toThrow()
  expect(() => previewTaskConfig({ ...base, engine_version: 'unregistered' }, 3)).toThrow()
})
