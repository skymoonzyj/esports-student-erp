import { expect, it } from 'vitest'
import { comparePractice } from './practiceComparison'
import { practiceFixture } from './reportTestFixtures'

it('compares only numeric raw metrics as second minus first and never derives score or null differences', () => {
  const first = practiceFixture(), second = practiceFixture()
  second.scored.metrics.hits = 18
  second.scored.metrics.hit_rate = .6
  second.scored.metrics.successful_average_latency_ms = 100
  expect(comparePractice(first, second)).toEqual({ reasons: [], differences: { hits: 3, hit_rate: .1, average_latency_ms: 0 } })
})

it.each(['suite', 'test', 'difficulty', 'config', 'settings', 'environment', 'quality'] as const)('blocks raw differences on mismatched %s', kind => {
  const first = practiceFixture(), second = practiceFixture()
  if (kind === 'suite') second.suite_id = 'mobile-fps'
  if (kind === 'test') second.test_id = 'tracking'
  if (kind === 'difficulty') second.config.difficulty = 2
  if (kind === 'config') second.config.engine_version = 'changed'
  if (kind === 'settings') second.settings.sensitivity = 1.05
  if (kind === 'environment') second.environment.width_css_px = 1200
  if (kind === 'quality') second.scored.quality_flags = ['long_frames']
  const result = comparePractice(first, second)
  expect(result.reasons.length).toBeGreaterThan(0)
  expect(result.differences).toEqual({})
})

it('compares complete object values independent of object property insertion order', () => {
  const first = practiceFixture(), second = practiceFixture()
  second.settings = Object.fromEntries(Object.entries(second.settings).reverse()) as typeof second.settings
  expect(comparePractice(first, second).reasons).toEqual([])
})
