import { z } from 'zod'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ProblemDetailsError } from '../api/client'
import type { AuthUser } from '../auth/AuthProvider'
import type { ReportsQuery } from './api'
import type { AssessmentReportSummary } from './apiContracts'
import { suiteIdSchema, type QualityFlag, type ReportResult, type SuiteId, type TaskConfig, type TestId } from './contracts'

export const suiteLabels: Record<SuiteId, string> = { 'pc-fps': '端游 FPS', 'mobile-fps': '手游 FPS', 'pc-moba': '端游 MOBA', 'mobile-moba': '手游 MOBA' }
export const testLabels: Record<TestId, string> = { precision: '精准定位与微调', tracking: '持续跟枪', switching: '多目标切换', recognition: '敌我识别', 'move-fire': '移动与射击协同', 'last-hit': '补刀时机', 'orb-walk': '走 A 节奏', dodge: '技能躲避', 'map-awareness': '小地图注意力', 'skill-aim': '技能方向控制', 'move-attack': '移动与攻击协同' }
export function reportTaskLabel(config: TaskConfig): string {
  return config.report_version === `${config.suite_id}-v2` && config.test_id === 'dodge'
    ? '危险区域识别与躲避'
    : testLabels[config.test_id]
}
export const metricLabels: Record<string, string> = {
  opportunities: '机会数', hits: '命中次数', hit_rate: '命中率', shots: '射击次数', accuracy: '射击准确率', wrong_target_shots: '错误目标射击', successful_average_latency_ms: '成功平均用时', average_latency_ms: '平均用时', coverage_ms: '实际覆盖时长', coverage_rate: '实际覆盖率', loss_count: '采样离靶次数', average_reacquisition_ms: '采样重捕获平均用时',
  enemy_opportunities: '敌方机会数', enemy_correct: '敌方正确命中', enemy_correct_rate: '敌方正确率', enemy_missed_or_wrong: '敌方漏击或错误', friend_opportunities: '友方机会数', friend_wrong_shots: '友方误射', friend_wrong_rate: '友方误射率', enemy_correct_average_latency_ms: '敌方正确平均用时', enemy_scoring_average_latency_ms: '敌方计分平均用时', reached: '到位次数', joint_successes: '同时完成次数', joint_rate: '同时完成率', successes: '成功次数', success_rate: '成功率', early_attacks: '过早攻击', late_attacks: '过晚攻击', successful_average_offset_ms: '成功平均窗口偏移', attacks_completed: '完成攻击次数', cancelled_attacks: '取消攻击次数', avoided: '躲避次数', hits_taken: '受击次数', avoid_rate: '躲避率', average_response_ms: '每轮首次移动平均用时', released: '释放次数', unreleased: '未释放次数', average_release_angle_error_deg: '平均释放角度误差', average_release_latency_ms: '平均释放用时', average_hit_latency_ms: '平均命中用时', main_opportunities: '主任务机会数', main_hits: '主任务命中', main_hit_rate: '主任务命中率', map_opportunities: '地图事件数', map_correct: '地图正确响应', map_wrong: '地图错误响应', map_missed: '地图漏报', unprompted_count: '无提示点击', map_attention_rate: '地图注意率', map_correct_average_latency_ms: '地图正确平均用时',
  switching_group_successes: '完成两靶组数', switching_group_rate: '两靶组完成率', acquisition_success_average_ms: '首靶成功获取平均用时', switching_success_average_ms: '第二靶成功切换平均用时', switching_scoring_average_ms: '第二靶计分平均用时', moving_hits: '有效移动命中次数', safe_entries: '安全区进入次数', safe_entry_average_ms: '安全区进入用时',
}
const qualityLabels: Record<QualityFlag, string> = { incomplete_duration: '有效时长不足', frame_gap: '单次帧间隙过长', long_frames: '长帧累计超限', mixed_input: '混合输入', unconfirmed_input: '输入未确认', hidden: '页面曾进入后台', interrupted: '评测中断' }
export const reasonLabels: Record<string, string> = { config_changed: '规则配置不同', settings_changed: '控制设置不同', environment_changed: '设备或环境不同', previous_quality_warning: '既往记录存在质量警告', current_quality_warning: '本次存在质量警告', no_comparable_history: '暂无可比历史记录' }
export const isUuid = (value: string) => z.uuid().safeParse(value).success
export function hasReportPermission(user: AuthUser | null): boolean { return Boolean(user?.permissions?.includes('esports_assessments.read') || user?.scoped_permissions?.includes('esports_assessments.read')) }

export function ReportError({ error }: { error?: unknown }) {
  const privateError = error instanceof ProblemDetailsError && [403, 404].includes(error.problem.status)
  return <p role="alert">{privateError ? '报告不存在或无权查看' : '记录暂时不可用，请稍后重试'}</p>
}
export function QualityNotice({ flags }: { flags: readonly QualityFlag[] }) {
  return flags.length ? <p className="assessmentReportsWarning" role="status">质量提示：{flags.map(flag => qualityLabels[flag]).join('；')}</p> : null
}
export function Metrics({ metrics, difference = false, emptyLabel = '暂无有效样本' }: { metrics: Record<string, number | null>; difference?: boolean; emptyLabel?: string }) {
  return <dl className="assessmentReportsMetrics">{Object.entries(metrics).map(([key, value]) => <div key={key}><dt>{metricLabels[key] ?? key}</dt><dd>{value === null ? emptyLabel : formatMetric(key, value, difference)}</dd></div>)}</dl>
}
export function FrozenSetup({ result }: { result: Pick<ReportResult, 'config' | 'settings' | 'environment'> }) {
  const { config, settings, environment: env } = result
  return <details className="assessmentReportsSetup"><summary>锁定设置、规则与设备</summary>
    <dl><dt>难度</dt><dd>{config.difficulty}</dd><dt>目录 / 引擎 / 评分 / 报告版本</dt><dd>{[config.catalog_version, config.engine_version, config.scoring_version, config.report_version].join(' / ')}</dd>
      <dt>灵敏度</dt><dd>{settings.sensitivity}</dd><dt>输入</dt><dd>{settings.input_kind === 'touch' ? '触摸' : '鼠标'} · {settings.mouse_mode === 'raw' ? '原始鼠标' : settings.mouse_mode === 'relative' ? '相对鼠标' : '无鼠标模式'}</dd><dt>场地</dt><dd>{env.width_css_px} × {env.height_css_px} CSS px</dd><dt>像素比</dt><dd>{env.device_pixel_ratio}</dd></dl>
    <details><summary>完整冻结参数</summary><pre>{JSON.stringify({ config, settings, environment: env }, null, 2)}</pre></details>
  </details>
}
export function TrackingNote({ testId }: { testId: TestId }) {
  return testId === 'tracking' ? <p className="assessmentReportsHint">覆盖率按真实时长积分；120 Hz 每个时间桶最多记录首个真实离靶及其首次重捕获。采样次数不是全部离靶事件数。</p> : null
}
export function uniqueBy<T>(items: readonly T[], key: (item: T) => string): T[] {
  const seen = new Set<string>()
  return items.filter(item => { const id = key(item); if (seen.has(id)) return false; seen.add(id); return true })
}
export function ReportRows({ items }: { items: readonly AssessmentReportSummary[] }) {
  return items.length ? <ul className="assessmentReportsList">{uniqueBy(items, item => item.id).map(item => <li key={item.id}><span>{item.person.display_name} · {suiteLabels[item.suite_id]} · {chinaDate(item.completed_at)}</span><Link to={`/esports-assessments/reports/${item.id}`}>查看报告</Link></li>)}</ul> : <p>暂无已完成报告</p>
}
export function useReportPager() {
  const [cursors, setCursors] = useState<Array<string | undefined>>([undefined])
  return { cursor: cursors.at(-1), page: cursors.length, previous: () => setCursors(value => value.length > 1 ? value.slice(0, -1) : value), next: (cursor: string) => setCursors(value => [...value, cursor]) }
}
export function ReportPager({ pager, next, total }: { pager: ReturnType<typeof useReportPager>; next: string | null; total: number }) {
  return <nav className="assessmentReportsPager" aria-label="记录分页"><span>可见记录 {total} 条 · 第 {pager.page} 页</span><button type="button" disabled={pager.page === 1} onClick={pager.previous}>上一页</button><button type="button" disabled={!next || next === pager.cursor} onClick={() => next && pager.next(next)}>下一页</button></nav>
}

export function chinaDate(value: string): string {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value))
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)?.value).join('-')
}
export function formatMetric(key: string, value: number | null, difference = false): string {
  if (value === null) return '暂无有效样本'
  const rate = key.endsWith('_rate') || key === 'accuracy'
  const displayed = Number((value * (rate ? 100 : 1)).toFixed(2))
  const sign = difference && displayed > 0 ? '+' : ''
  const unit = rate ? (difference ? ' 个百分点' : '%') : key.endsWith('_ms') ? ' ms' : key.endsWith('_deg') ? '°' : ' 次'
  return `${sign}${displayed}${unit}`
}
const filtersSchema = z.object({ person_id: z.uuid().optional(), suite_id: suiteIdSchema.optional(), date_from: z.iso.date().optional(), date_to: z.iso.date().optional() }).refine(value => !value.date_from || !value.date_to || value.date_from <= value.date_to)
export function parseReportFilters(params: URLSearchParams): ReportsQuery | null {
  const entries = Object.fromEntries(['person_id', 'suite_id', 'date_from', 'date_to'].flatMap(key => params.has(key) ? [[key, params.get(key)]] : []))
  const result = filtersSchema.safeParse(entries)
  return result.success ? result.data : null
}
