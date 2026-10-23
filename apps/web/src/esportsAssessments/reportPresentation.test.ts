import { expect, it } from 'vitest'
import { formatMetric, chinaDate, parseReportFilters } from './reportPresentation'

it('formats China dates across UTC day boundaries and metric units without changing null to zero', () => {
  expect(chinaDate('2026-09-21T18:30:00Z')).toBe('2026-09-22')
  expect(formatMetric('coverage_rate', .5)).toBe('50%')
  expect(formatMetric('loss_count', 4)).toBe('4 次')
  expect(formatMetric('average_response_ms', 12.345)).toBe('12.35 ms')
  expect(formatMetric('average_release_angle_error_deg', 1.5)).toBe('1.5°')
  expect(formatMetric('successful_average_offset_ms', -12)).toBe('-12 ms')
  expect(formatMetric('average_reacquisition_ms', null)).toBe('暂无有效样本')
  expect(formatMetric('coverage_rate', .1, true)).toBe('+10 个百分点')
})

it('rejects invalid suite/person/dates before issuing a filtered list query', () => {
  expect(parseReportFilters(new URLSearchParams('suite_id=pc-fps&date_from=2026-09-21'))).toEqual({ suite_id: 'pc-fps', date_from: '2026-09-21' })
  for (const query of ['suite_id=unknown', 'person_id=private-name', 'date_from=2026-02-30', 'date_from=2026-09-22&date_to=2026-09-21']) {
    expect(parseReportFilters(new URLSearchParams(query))).toBeNull()
  }
})
