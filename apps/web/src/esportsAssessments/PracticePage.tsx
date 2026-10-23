import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { z } from 'zod'
import { useAuth } from '../auth/AuthProvider'
import { assessmentApi } from './api'
import { assessmentKeys } from './keys'
import { ASSESSMENT_SUITE_AVAILABILITY } from './availability'
import { previewTaskConfig, suiteIdSchema, suiteMembers } from './contracts'
import { assessmentTaskLabel } from './hostState'
import { AssessmentTask, AssessmentRecovery } from './AssessmentSessionPage'

export function PracticePage() {
  const { user } = useAuth()
  const account = user?.account_id ?? ''
  const [search, setSearch] = useSearchParams()
  const suiteId = suiteIdSchema.safeParse(search.get('suite'))
  const practiceId = search.get('practice_id') ?? undefined
  const valid = suiteId.success && (!practiceId || z.uuid().safeParse(practiceId).success) && !search.has('session_id')
  const context = useQuery({ queryKey: assessmentKeys.context(account), queryFn: assessmentApi.context, enabled: !!account && valid })
  const catalog = useQuery({ queryKey: assessmentKeys.suites(account), queryFn: assessmentApi.suites, enabled: !!account && valid })
  if (!valid || !suiteId.success) return <p role="alert">练习地址无效，请从评测入口选择专项。</p>
  if (catalog.isError) return <section role="alert"><p>评测目录版本无法识别或暂时不可用，请刷新页面后重试。</p><button onClick={() => void catalog.refetch()}>刷新目录</button></section>
  if (context.isError) return <section role="alert"><p>无法确认练习权限。</p><button onClick={() => void context.refetch()}>重试</button></section>
  if (!context.data || !catalog.data) return <p role="status">正在加载练习目录…</p>
  if (!context.data.can_take || !context.data.can_read_self) return <p role="alert">当前账号没有参加本专项的权限。</p>
  const suite = catalog.data.items.find(item => item.suite_id === suiteId.data)
  const selected = suiteMembers[suiteId.data].find(test => test === search.get('test'))
  const locallyReady = ASSESSMENT_SUITE_AVAILABILITY[suiteId.data] || user?.is_super_admin === true
  if (!suite || !locallyReady || !context.data.available_suites.includes(suiteId.data)) return selected ? <AssessmentRecovery key={`${account}:${practiceId}:${selected}`} identity={{ mode: 'practice', suite_id: suiteId.data, practice_id: practiceId, test_id: selected }} /> : <p>此专项暂未开放。<Link to="/esports-assessments">返回入口</Link></p>
  const rawLevel = search.get('difficulty') ?? '3'
  if (!/^[1-5]$/.test(rawLevel)) return <p role="alert">练习难度无效，必须是 1—5。</p>
  const difficulty = Number(rawLevel)
  const base = selected ? suite.tasks[selected] : undefined
  const back = () => setSearch({ suite: suiteId.data, difficulty: String(difficulty) })
  if (base) return <AssessmentTask key={`${account}:${suiteId.data}:${selected}:${practiceId ?? ''}:${difficulty}`} identity={{ mode: 'practice', suite_id: suiteId.data, practice_id: practiceId, test_id: selected }} config={previewTaskConfig(base, difficulty)} onBack={back} />
  return <section className="assessmentPractice"><h1>自由练习</h1><p>五档练习只显示原始指标，不补正式进度、不生成正式评分或报告。难度 3 与正式参数相同。</p><label>练习难度 <select value={difficulty} onChange={event => setSearch({ suite: suiteId.data, difficulty: event.target.value })}>{[1, 2, 3, 4, 5].map(level => <option value={level} key={level}>{level}</option>)}</select></label><div className="assessmentActions">{suite.test_ids.map(test => <button key={test} onClick={() => setSearch({ suite: suiteId.data, test, difficulty: String(difficulty), ...(practiceId ? { practice_id: practiceId } : {}) })}>{assessmentTaskLabel(suite.tasks[test]!)}</button>)}</div><Link to="/esports-assessments">返回评测入口</Link></section>
}
export default PracticePage
