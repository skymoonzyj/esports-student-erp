import { z } from 'zod'

export const itemRankingReasonSchema = z.enum([
  'quality_warning', 'incomplete_result', 'incompatible_input', 'missing_comparison_metadata',
  'subject_inactive', 'practice_only', 'mode_unverified',
])
export type ItemRankingReason = z.infer<typeof itemRankingReasonSchema>
export const itemRankingReasonLabels: Record<ItemRankingReason, string> = {
  quality_warning: '本项成绩存在质量提示，暂不参与排名。',
  incomplete_result: '本项尚未有效完成，暂不参与排名。',
  incompatible_input: '输入方式或测试条件不兼容，无法进行排名比较。',
  missing_comparison_metadata: '缺少必要的比较条件，暂不参与排名。',
  subject_inactive: '人员已删除或账号均已停用，暂不参与排名。',
  practice_only: '本次为练习成绩，不参与正式排名。',
  mode_unverified: '此模式尚未完成排名验证，暂不参与排名。',
}

const metricKey = z.string().regex(/^[a-z][a-z0-9_]*$/)
const nonblankText = z.string().regex(/\S/)
const positiveInteger = z.number().int().positive()
const nonnegativeInteger = z.number().int().nonnegative()
const finiteNumber = z.number().finite()

export const itemRankingMetricSchema = z.strictObject({
  key: metricKey, label: nonblankText, unit: z.string(),
  better: z.enum(['higher', 'lower']), decimals: nonnegativeInteger,
})
export const itemRankingStandingSchema = z.strictObject({
  rank: positiveInteger, values: z.record(metricKey, finiteNumber),
})
export const itemRankingGapSchema = z.strictObject({
  key: metricKey, amount: finiteNumber.nonnegative(), state: z.enum(['ahead', 'behind', 'equal']),
}).refine(gap => (gap.state === 'equal') === (gap.amount === 0), 'invalid gap state')
export const itemRankingTopRowSchema = z.strictObject({
  ...itemRankingStandingSchema.shape,
  is_self: z.boolean(), tie_count: positiveInteger, gaps: z.array(itemRankingGapSchema),
})

// Each supplying service declares its versioned metric catalog. Exact ordering uses
// raw service values, never these display numbers or the frontend's formatting.
export const itemRankingViewSchema = z.strictObject({
  status: z.enum(['ranked', 'not_eligible']), reason: itemRankingReasonSchema.nullable(),
  group_label: nonblankText, calculated_at: z.iso.datetime({ offset: true }),
  participant_count: nonnegativeInteger, metrics: z.array(itemRankingMetricSchema).min(1),
  current: itemRankingStandingSchema.nullable(), best: itemRankingStandingSchema.nullable(),
  top_three: z.array(itemRankingTopRowSchema).max(3),
}).refine(view => {
  const keys = new Set(view.metrics.map(metric => metric.key))
  if (keys.size !== view.metrics.length) return false
  if (view.status === 'not_eligible') {
    return view.reason !== null && view.participant_count === 0
      && view.current === null && view.best === null && view.top_three.length === 0
  }
  if (view.reason !== null || view.participant_count === 0 || view.current === null || view.best === null
    || view.top_three.length === 0 || view.top_three.length > view.participant_count) return false
  for (const standing of [view.current, view.best, ...view.top_three]) {
    const valueKeys = Object.keys(standing.values)
    if (standing.rank > view.participant_count || valueKeys.length !== keys.size
      || valueKeys.some(key => !keys.has(key))) return false
  }
  if (view.top_three.filter(row => row.is_self).length > 1) return false
  let previous: z.infer<typeof itemRankingTopRowSchema> | undefined
  let cohortSize = 0
  for (const row of view.top_three) {
    if (row.rank + row.tie_count - 1 > view.participant_count) return false
    if (!previous) {
      if (row.rank !== 1) return false
      cohortSize = 1
    } else if (row.rank === previous.rank) {
      cohortSize += 1
      if (row.tie_count !== previous.tie_count || cohortSize > row.tie_count) return false
    } else {
      if (cohortSize !== previous.tie_count || row.rank !== previous.rank + previous.tie_count) return false
      cohortSize = 1
    }
    const gapKeys = new Set(row.gaps.map(gap => gap.key))
    if (row.gaps.length !== keys.size || gapKeys.size !== keys.size
      || [...gapKeys].some(key => !keys.has(key))) return false
    previous = row
  }
  return true
}, 'invalid item ranking state')

export type ItemRankingMetric = z.infer<typeof itemRankingMetricSchema>
export type ItemRankingStanding = z.infer<typeof itemRankingStandingSchema>
export type ItemRankingGap = z.infer<typeof itemRankingGapSchema>
export type ItemRankingTopRow = z.infer<typeof itemRankingTopRowSchema>
export type ItemRankingView = z.infer<typeof itemRankingViewSchema>

export function parseItemRankingView(raw: unknown): ItemRankingView {
  const result = itemRankingViewSchema.safeParse(raw)
  if (!result.success) throw new Error('排名数据格式无效，请稍后重试。')
  return result.data
}
