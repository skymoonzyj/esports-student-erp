import { expect, it } from 'vitest'
import { assessmentRankingSchema, parseAssessmentRanking } from './contracts'

const id = '11111111-1111-4111-8111-111111111111'
const ranked = {
  report_id: id, calculated_at: '2026-10-05T00:00:00Z', status: 'ranked', reason: null,
  best_report_id: id, best_completed_at: '2026-10-04T00:00:00+08:00',
  overall_score: 80.5, overall_rank: 1, participant_count: 3,
  items: [{ test_id: 'aim', score: 81, rank: 1 }, { test_id: 'memory', score: 80, rank: 3 }],
}
it('accepts tied ranks and keeps the ordered whole-round items', () => {
  expect(parseAssessmentRanking(ranked, id, ['aim', 'memory']).items).toEqual(ranked.items.map(item => ({ ...item, top_scores: [] })))
})
it.each([
  { participant_count: 0 }, { overall_rank: 4 }, { overall_score: 101 },
  { best_report_id: null }, { best_completed_at: '2026-10-04T00:00:00' },
  { reason: 'quality_warning' }, { other_person_id: id },
  { items: [{ test_id: 'aim', score: 101, rank: 1 }] },
  { items: [{ test_id: 'aim', score: 80, rank: 4 }] },
  { items: [{ test_id: 'aim', score: 80, rank: 1 }, { test_id: 'aim', score: 80, rank: 1 }] },
])('rejects malformed or privacy-expanding ranking fields %j', override => {
  expect(assessmentRankingSchema.safeParse({ ...ranked, ...override }).success).toBe(false)
})
it('rejects wrong report identity, incomplete items and reordered items', () => {
  expect(() => parseAssessmentRanking(ranked, '22222222-2222-4222-8222-222222222222', ['aim', 'memory'])).toThrow()
  expect(() => parseAssessmentRanking(ranked, id, ['aim', 'memory', 'sequence'])).toThrow()
  expect(() => parseAssessmentRanking(ranked, id, ['memory', 'aim'])).toThrow()
})
it('accepts only an empty non-eligible response without synthetic ranks', () => {
  const empty = { ...ranked, status: 'not_eligible', reason: 'quality_warning', best_report_id: null,
    best_completed_at: null, overall_score: null, overall_rank: null, participant_count: 0, items: [] }
  expect(parseAssessmentRanking(empty, id, ['aim', 'memory']).status).toBe('not_eligible')
  expect(assessmentRankingSchema.safeParse({ ...empty, overall_rank: 1 }).success).toBe(false)
  expect(assessmentRankingSchema.safeParse({ ...empty, participant_count: 1 }).success).toBe(false)
})

it('accepts anonymous top-three score gaps but rejects identity fields and inconsistent gaps', () => {
  const value = { ...ranked, top_scores: [{ rank: 1, score: 90, gap: 9.5 }], items: ranked.items.map(item => ({ ...item, top_scores: [{ rank: 1, score: 90, gap: 90 - item.score }] })) }
  expect(parseAssessmentRanking(value, id).top_scores).toEqual([{ rank: 1, score: 90, gap: 9.5 }])
  expect(assessmentRankingSchema.safeParse({ ...value, top_scores: [{ rank: 1, score: 90, gap: 9.5, name: 'private' }] }).success).toBe(false)
  expect(assessmentRankingSchema.safeParse({ ...value, top_scores: [{ rank: 1, score: 90, gap: 0 }] }).success).toBe(false)
  expect(assessmentRankingSchema.safeParse({ ...value, top_scores: Array(4).fill({ rank: 1, score: 90, gap: 9.5 }) }).success).toBe(false)
})
it('accepts signed trailing gaps and competition-rank ties without inventing missing rows', () => {
  const tops = [{ rank: 1, score: 90, gap: 9.5 }, { rank: 1, score: 90, gap: 9.5 }, { rank: 3, score: 70, gap: -10.5 }]
  expect(parseAssessmentRanking({ ...ranked, top_scores: tops }, id).top_scores).toEqual(tops)
  expect(parseAssessmentRanking(ranked, id).top_scores).toEqual([])
  expect(assessmentRankingSchema.safeParse({ ...ranked, top_scores: [tops[0], { ...tops[1], rank: 2 }] }).success).toBe(false)
})
