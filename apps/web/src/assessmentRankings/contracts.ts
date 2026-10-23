import { z } from 'zod'

const date = z.iso.datetime({ offset: true })
export const assessmentRankingSchema = z.strictObject({
  report_id: z.uuid(), calculated_at: date, status: z.enum(['ranked', 'not_eligible']),
  reason: z.enum(['quality_warning', 'incomplete_results', 'incompatible_input']).nullable(),
  best_report_id: z.uuid().nullable(), best_completed_at: date.nullable(),
  overall_score: z.number().min(0).max(100).nullable(), overall_rank: z.number().int().positive().nullable(),
  participant_count: z.number().int().nonnegative(),
  items: z.array(z.strictObject({ test_id: z.string().min(1), score: z.number().int().min(0).max(100), rank: z.number().int().positive() })).max(8),
}).refine(value => {
  if (value.status === 'not_eligible') return value.reason !== null && value.best_report_id === null
    && value.best_completed_at === null && value.overall_score === null && value.overall_rank === null
    && value.participant_count === 0 && value.items.length === 0
  return value.reason === null && value.best_report_id !== null && value.best_completed_at !== null
    && value.overall_score !== null && value.overall_rank !== null && value.participant_count > 0
    && value.overall_rank <= value.participant_count && value.items.length > 0
    && value.items.every(item => item.rank <= value.participant_count)
    && new Set(value.items.map(item => item.test_id)).size === value.items.length
}, 'invalid ranking state')
export type AssessmentRanking = z.infer<typeof assessmentRankingSchema>
export function parseAssessmentRanking(raw: unknown, reportId: string, testIds?: readonly string[]): AssessmentRanking {
  const result = assessmentRankingSchema.safeParse(raw)
  if (!result.success || result.data.report_id !== reportId || (testIds && result.data.status === 'ranked'
    && (result.data.items.length !== testIds.length || result.data.items.some((item, index) => item.test_id !== testIds[index])))) {
    throw new Error('排名数据格式无效，请稍后重试。')
  }
  return result.data
}
