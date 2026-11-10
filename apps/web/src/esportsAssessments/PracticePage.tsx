import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { z } from 'zod'
import { useAuth } from '../auth/AuthProvider'
import { assessmentApi } from './api'
import { assessmentKeys } from './keys'
import { ASSESSMENT_SUITE_AVAILABILITY } from './availability'
import { isLaneTaskConfig, isSupportedSuiteTest, previewTaskConfig, suiteIdSchema, testIdSchema, type AttackMode } from './contracts'
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
  const recovered = useQuery({ queryKey: [...assessmentKeys.root(account), 'practice-recovery-config', practiceId], queryFn: () => assessmentApi.readPractice(practiceId!), enabled: !!account && valid && !!practiceId && context.data?.can_take === true && context.data.can_read_self })
  if (!valid || !suiteId.success) return <p role="alert">练习地址无效，请从评测入口选择专项。</p>
  if (catalog.isError) return <section role="alert"><p>评测目录版本无法识别或暂时不可用，请刷新页面后重试。</p><button onClick={() => void catalog.refetch()}>刷新目录</button></section>
  if (context.isError) return <section role="alert"><p>无法确认练习权限。</p><button onClick={() => void context.refetch()}>重试</button></section>
  if (!context.data || !catalog.data) return <p role="status">正在加载练习目录…</p>
  if (!context.data.can_take || !context.data.can_read_self) return <p role="alert">当前账号没有参加本专项的权限。</p>
  const suite = catalog.data.items.find(item => item.suite_id === suiteId.data)
  const requested = testIdSchema.safeParse(search.get('test'))
  const selected = requested.success && isSupportedSuiteTest(suiteId.data, requested.data) ? requested.data : undefined
  const locallyReady = ASSESSMENT_SUITE_AVAILABILITY[suiteId.data] || user?.is_super_admin === true
  if (!suite || !locallyReady || !context.data.available_suites.includes(suiteId.data)) return selected ? <AssessmentRecovery key={`${account}:${practiceId}:${selected}`} identity={{ mode: 'practice', suite_id: suiteId.data, practice_id: practiceId, test_id: selected }} /> : <p>此专项暂未开放。<Link to="/esports-assessments">返回入口</Link></p>
  const rawLevel = search.get('difficulty') ?? '3'
  if (!/^[1-5]$/.test(rawLevel)) return <p role="alert">练习难度无效，必须是 1—5。</p>
  const difficulty = Number(rawLevel)
  if (practiceId) {
    if (recovered.isError) return <p role="alert">练习不存在或无权恢复。</p>
    if (!recovered.data) return <p role="status">正在读取练习冻结配置…</p>
    const attempt = recovered.data.attempt
    if (attempt.suite_id !== suiteId.data || (selected && attempt.test_id !== selected)) return <p role="alert">练习地址与服务器记录不一致。</p>
    return <AssessmentTask key={`${account}:${practiceId}`} identity={{ mode: 'practice', suite_id: attempt.suite_id, practice_id: practiceId, test_id: attempt.test_id }} config={attempt.config} onBack={() => setSearch({ suite: suiteId.data })} />
  }
  const lane = Boolean(suite.tasks['last-hit'] && isLaneTaskConfig(suite.tasks['last-hit']))
  const rawAttackMode = search.get('attack_mode') ?? 'melee'
  if (lane && !['melee', 'ranged'].includes(rawAttackMode)) return <p role="alert">人物模式无效，请选择近战或远程。</p>
  if (lane && difficulty !== 3) return <p role="alert">兵线补刀只提供标准配置（难度 3）。</p>
  const attackMode = rawAttackMode as AttackMode
  const modeSearch: Record<string, string> = lane ? { attack_mode: attackMode } : {}
  const base = selected && suite.test_ids.includes(selected) ? suite.tasks[selected] : undefined
  const back = () => setSearch({ suite: suiteId.data, difficulty: String(difficulty), ...modeSearch })
  if (base) return <AssessmentTask key={`${account}:${suiteId.data}:${selected}:${practiceId ?? ''}:${difficulty}:${lane ? attackMode : ''}`} identity={{ mode: 'practice', suite_id: suiteId.data, practice_id: practiceId, test_id: selected, ...(lane ? { attack_mode: attackMode } : {}) }} config={previewTaskConfig(base, difficulty, lane ? attackMode : undefined)} onBack={back} />
  return <section className="assessmentPractice"><h1>自由练习</h1><p>{lane ? '五分钟兵线补刀只提供标准配置（难度 3）。' : '五档练习只显示原始指标；难度 3 与正式参数相同。'}练习不补正式进度、不生成正式评分或报告。</p>{lane ? <label>练习人物模式 <select value={attackMode} onChange={event => setSearch({ suite: suiteId.data, difficulty: '3', attack_mode: event.target.value })}><option value="melee">近战 · 剑盾</option><option value="ranged">远程 · 弓弩</option></select></label> : <label>练习难度 <select value={difficulty} onChange={event => setSearch({ suite: suiteId.data, difficulty: event.target.value })}>{[1, 2, 3, 4, 5].map(level => <option value={level} key={level}>{level}</option>)}</select></label>}<div className="assessmentActions">{suite.test_ids.map(test => <button key={test} onClick={() => setSearch({ suite: suiteId.data, test, difficulty: String(difficulty), ...modeSearch })}>{assessmentTaskLabel(suite.tasks[test]!)}</button>)}</div><Link to="/esports-assessments">返回评测入口</Link></section>
}
export default PracticePage
