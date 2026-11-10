import { describe, expect, it } from 'vitest'
import { itemRankingReasonLabels, itemRankingViewSchema, parseItemRankingView } from './itemContracts'

function ranked() {
  return {
    status: 'ranked', reason: null, group_label: '网页反应速度 · v1',
    calculated_at: '2026-10-31T09:00:00+08:00', participant_count: 4,
    metrics: [{ key: 'score', label: '评分', unit: '分', better: 'higher', decimals: 1 }],
    current: { rank: 4, values: { score: 70.5 } }, best: { rank: 2, values: { score: 80 } },
    top_three: [
      { rank: 1, values: { score: 90 }, is_self: false, tie_count: 1,
        gaps: [{ key: 'score', amount: 19.5, state: 'behind' }] },
      { rank: 2, values: { score: 80 }, is_self: true, tie_count: 2,
        gaps: [{ key: 'score', amount: 9.5, state: 'behind' }] },
      { rank: 2, values: { score: 80 }, is_self: false, tie_count: 2,
        gaps: [{ key: 'score', amount: 9.5, state: 'behind' }] },
    ],
  }
}

function ineligible(reason = 'quality_warning') {
  return { ...ranked(), status: 'not_eligible', reason, participant_count: 0,
    current: null, best: null, top_three: [] }
}

it('keeps anonymous competition ranks and JSON numbers', () => {
  const view = parseItemRankingView(ranked())
  expect(view.participant_count).toBe(4)
  expect(view.top_three.map(row => row.rank)).toEqual([1, 2, 2])
  expect(view.current?.values.score).toBe(70.5)
  expect(Object.keys(view).sort()).toEqual(['best', 'calculated_at', 'current', 'group_label',
    'metrics', 'participant_count', 'reason', 'status', 'top_three'])
  expect(Object.keys(view.top_three[0]).sort()).toEqual(['gaps', 'is_self', 'rank', 'tie_count', 'values'])
})

describe.each(['current', 'best', 'top', 'gap'])('%s number boundary', location => {
  it.each([NaN, Infinity, -Infinity, true, '70.5', null])('rejects non-finite/coerced %s', number => {
    const payload = ranked()
    if (location === 'gap') Object.assign(payload.top_three[0].gaps[0], { amount: number })
    else if (location === 'top') Object.assign(payload.top_three[0].values, { score: number })
    else Object.assign(payload[location as 'current' | 'best'].values, { score: number })
    expect(itemRankingViewSchema.safeParse(payload).success).toBe(false)
  })
})

describe.each(['root', 'metric', 'current', 'best', 'top', 'gap'])('%s privacy boundary', location => {
  it.each(['person_id', 'account_id', 'report_id', 'device_address', 'sort_key'])('rejects %s', field => {
    const payload = ranked()
    const targets = { root: payload, metric: payload.metrics[0], current: payload.current,
      best: payload.best, top: payload.top_three[0], gap: payload.top_three[0].gaps[0] }
    Object.assign(targets[location as keyof typeof targets], { [field]: 'private' })
    expect(itemRankingViewSchema.safeParse(payload).success).toBe(false)
  })
})

it.each([
  { participant_count: 0 }, { participant_count: -1 }, { participant_count: true },
  { participant_count: '4' }, { participant_count: undefined }, { reason: 'quality_warning' },
  { current: null }, { best: null }, { top_three: [] }, { metrics: [] }, { group_label: ' ' },
  { calculated_at: '2026-10-31T09:00:00' },
])('rejects incomplete ranked state %j', override => {
  expect(itemRankingViewSchema.safeParse({ ...ranked(), ...override }).success).toBe(false)
})

describe.each(['current', 'best', 'top'])('%s rank boundary', location => {
  it.each([0, -1, 5, true, '1', 1.5])('rejects out-of-range/noninteger %s', rank => {
    const payload = ranked()
    const target = location === 'top' ? payload.top_three[0] : payload[location as 'current' | 'best']
    Object.assign(target, { rank })
    expect(itemRankingViewSchema.safeParse(payload).success).toBe(false)
  })
  it.each([{}, { score: 70, person_id: 123 }, { other: 70 }])('rejects undeclared/missing values %j', values => {
    const payload = ranked()
    const target = location === 'top' ? payload.top_three[0] : payload[location as 'current' | 'best']
    Object.assign(target, { values })
    expect(itemRankingViewSchema.safeParse(payload).success).toBe(false)
  })
})

it.each(['', 'score-total', 'person.id', '__proto__', 'Score', ' score'])('rejects unsafe metric key %s', key => {
  const payload = ranked()
  payload.metrics[0].key = key
  expect(itemRankingViewSchema.safeParse(payload).success).toBe(false)
})

it.each(['duplicate_metric', 'missing_gap', 'extra_gap', 'unknown_gap', 'negative_gap', 'equal_positive',
  'behind_zero', 'ahead_zero', 'four_rows', 'duplicate_self', 'unordered', 'bad_competition',
  'too_many_ties', 'inconsistent_ties'])('rejects inconsistent state %s', change => {
  const payload = ranked()
  const row = payload.top_three[0]
  const gap = row.gaps[0]
  switch (change) {
    case 'duplicate_metric': payload.metrics.push({ ...payload.metrics[0] }); break
    case 'missing_gap': row.gaps = []; break
    case 'extra_gap': row.gaps.push({ ...gap }); break
    case 'unknown_gap': gap.key = 'latency'; break
    case 'negative_gap': gap.amount = -1; break
    case 'equal_positive': gap.state = 'equal'; break
    case 'behind_zero': gap.amount = 0; break
    case 'ahead_zero': gap.amount = 0; gap.state = 'ahead'; break
    case 'four_rows': payload.top_three.push({ ...row }); break
    case 'duplicate_self': row.is_self = true; break
    case 'unordered': payload.top_three.reverse(); break
    case 'bad_competition': payload.top_three[1].rank = 3; break
    case 'too_many_ties': row.tie_count = 5; break
    case 'inconsistent_ties': payload.top_three[2].tie_count = 1; break
  }
  expect(itemRankingViewSchema.safeParse(payload).success).toBe(false)
})

it('allows a tie cohort larger than the three visible rows', () => {
  const payload = ranked()
  payload.top_three.forEach(row => Object.assign(row, { rank: 1, tie_count: 4, values: { score: 80 } }))
  payload.best.rank = 1
  expect(parseItemRankingView(payload).top_three.map(row => row.tie_count)).toEqual([4, 4, 4])
})

it('accepts one-one-three competition ranks and equal/ahead gaps', () => {
  const payload = ranked()
  Object.assign(payload.top_three[0], { rank: 1, tie_count: 2 })
  Object.assign(payload.top_three[1], { rank: 1, tie_count: 2 })
  Object.assign(payload.top_three[2], { rank: 3, tie_count: 1 })
  Object.assign(payload.top_three[0].gaps[0], { amount: 0, state: 'equal' })
  Object.assign(payload.top_three[1].gaps[0], { amount: 1.5, state: 'ahead' })
  expect(parseItemRankingView(payload).top_three.map(row => row.rank)).toEqual([1, 1, 3])
})

it.each(['quality_warning', 'incomplete_result', 'incompatible_input', 'missing_comparison_metadata',
  'subject_inactive', 'practice_only', 'mode_unverified'] as const)('explains ineligibility %s', reason => {
  expect(parseItemRankingView(ineligible(reason)).reason).toBe(reason)
  expect(itemRankingReasonLabels[reason]).toMatch(/[\u4e00-\u9fff]/)
})

it.each([
  { reason: null }, { reason: 'incomplete_results' }, { reason: 'unknown' }, { participant_count: 1 },
  { current: { rank: 1, values: { score: 80 } } }, { best: { rank: 1, values: { score: 80 } } },
  { top_three: ranked().top_three },
])('rejects fabricated ineligible ranks %j', override => {
  expect(() => parseItemRankingView({ ...ineligible(), ...override })).toThrow('排名数据格式无效')
})

it('accepts generic lower-is-better metrics outside the talent score range', () => {
  const payload = ranked()
  payload.metrics = [{ key: 'latency_ms', label: '反应时间', unit: '毫秒', better: 'lower', decimals: 2 }]
  for (const standing of [payload.current, payload.best, ...payload.top_three]) {
    Object.assign(standing, { values: { latency_ms: 234.56 } })
  }
  payload.top_three.forEach(row => { row.gaps = [{ key: 'latency_ms', amount: 0, state: 'equal' }] })
  expect(parseItemRankingView(payload).metrics[0].better).toBe('lower')
})

it.each([
  ['key', 'score\n'], ['label', ' '], ['unit', 123], ['better', 'descending'],
  ['decimals', -1], ['decimals', true], ['decimals', 1.5],
])('rejects malformed metric metadata %s=%s', (field, value) => {
  const payload = ranked()
  Object.assign(payload.metrics[0], { [String(field)]: value })
  expect(itemRankingViewSchema.safeParse(payload).success).toBe(false)
})

it.each([['is_self', 1], ['is_self', 'false'], ['tie_count', 0], ['tie_count', true]])(
  'rejects coerced row metadata %s=%s', (field, value) => {
    const payload = ranked()
    Object.assign(payload.top_three[0], { [String(field)]: value })
    expect(itemRankingViewSchema.safeParse(payload).success).toBe(false)
  },
)

it.each([1793418000, 1793418000.0, '1793418000'])('rejects epoch calculation time %s', timestamp => {
  expect(itemRankingViewSchema.safeParse({ ...ranked(), calculated_at: timestamp }).success).toBe(false)
})
