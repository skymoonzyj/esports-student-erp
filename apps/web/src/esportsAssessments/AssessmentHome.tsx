import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'
import { ProblemDetailsError } from '../api/client'

import { useAuth } from '../auth/AuthProvider'
import { assessmentApi } from './api'
import { ASSESSMENT_SUITE_AVAILABILITY } from './availability'
import type { SuiteId } from './contracts'
import { assessmentKeys } from './keys'
import { hasVolatilePending } from './pending'
import { assessmentTestLabels } from './hostState'
import './assessment.css'

const suites: ReadonlyArray<{
  id: SuiteId
  name: string
  description: string
}> = [
  { id: 'pc-fps', name: '端游 FPS', description: '鼠标精度、跟踪、切换与敌我识别。' },
  { id: 'mobile-fps', name: '手游 FPS', description: '横屏多指移动、瞄准与射击。' },
  { id: 'pc-moba', name: '端游 MOBA', description: '补刀、走砍、躲避与小地图注意。' },
  { id: 'mobile-moba', name: '手游 MOBA', description: '多指躲避、技能瞄准、移动攻击与地图注意。' },
]

export function AssessmentHome() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? 'anonymous'
  const navigate = useNavigate()
  const owner = useRef({ account: accountId, generation: 0 })
  if (owner.current.account !== accountId) owner.current = { account: accountId, generation: owner.current.generation + 1 }
  const alive = useRef(true)
  const requests = useRef(new Map<string, string>())
  const [creating, setCreating] = useState(false)
  const [failure, setFailure] = useState<{ owner: string; message: string } | null>(null)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => { setCreating(false); setFailure(null) }, [accountId])
  const context = useQuery({
    queryKey: assessmentKeys.context(accountId),
    queryFn: assessmentApi.context,
    enabled: Boolean(user),
  })
  const [recoveryCursor, setRecoveryCursor] = useState<{ account: string; cursor?: string }>({ account: accountId })
  const cursor = recoveryCursor.account === accountId ? recoveryCursor.cursor : undefined
  const recovery = useQuery({ queryKey: [...assessmentKeys.recovery(accountId), cursor], queryFn: () => assessmentApi.recovery(cursor ? { cursor } : {}), enabled: !!user && context.data?.can_read_self === true })

  if (!user || context.isPending) {
    return <p className="assessmentState">正在确认天赋评测权限与开放范围…</p>
  }
  if (context.isError || !context.data) {
    return (
      <section className="assessmentState" role="alert">
        <p>暂时无法确认天赋评测状态，请稍后重试。</p>
        <button type="button" onClick={() => void context.refetch()}>重试</button>
      </section>
    )
  }

  const canParticipate = context.data.can_take && context.data.can_read_self
  const canPreview = user.is_super_admin === true
  const serverAvailable = new Set(context.data.available_suites)
  const activeRun = context.data.active_run
  async function create(suite_id: SuiteId) {
    if (creating || !canParticipate || (!ASSESSMENT_SUITE_AVAILABILITY[suite_id] && !canPreview) || !serverAvailable.has(suite_id)) return
    const captured = accountId
    const generation = owner.current.generation
    const current = () => alive.current && owner.current.account === captured && owner.current.generation === generation
    const identity = `${captured}:${suite_id}`
    const operation_key = requests.current.get(identity) ?? crypto.randomUUID()
    requests.current.set(identity, operation_key)
    setCreating(true); setFailure(null)
    try {
      const session = await assessmentApi.createSession({ suite_id, operation_key })
      if (current()) navigate(`/esports-assessments/sessions/${session.id}`)
    } catch (error) {
      if (current()) setFailure({ owner: captured, message: error instanceof ProblemDetailsError && error.problem.status === 429 ? `请求过于频繁，请等待 ${error.retryAfterSeconds ?? 60} 秒后手动重试。` : '创建失败，请手动重试；将保留原操作键。' })
    } finally { if (current()) setCreating(false) }
  }

  return (
    <div className="assessmentHome">
      <header className="assessmentHero">
        <p className="assessmentEyebrow">独立训练模块</p>
        <h1>天赋评测 2</h1>
        <p>四套专项评测彼此独立；正式结果由服务器校验并保存，页面不会自行生成成绩。</p>
        <div className="assessmentActions">
          {context.data.can_read_self || context.data.can_read_reports ? (
            <Link to="/esports-assessments/history">查看评测记录</Link>
          ) : null}
        </div>
      </header>
      {failure?.owner === accountId && <p role="alert">{failure.message}</p>}
      {canPreview && canParticipate && suites.some(suite => !ASSESSMENT_SUITE_AVAILABILITY[suite.id] && serverAvailable.has(suite.id)) ? (
        <p className="assessmentNotice">当前为超管试测，尚未向其他账号开放。正式成绩保存到本人名下，完成四项后生成报告；自由练习单独保存。端游请用电脑，手游请用手机横屏。</p>
      ) : null}

      {canPreview && canParticipate && serverAvailable.size > 0 ? (
        <section className="assessmentOngoing" aria-label="听声辨位超管试听">
          <h2>听声辨位 · 超管试听</h2>
          <p>戴好双耳耳机，先检查左右声道，再完成 8 个方向的判断。试听结果仅在本次页面显示，关闭或刷新后清空。</p>
          <div className="assessmentActions">
            <a href="/api/v1/esports-assessments/sound-preview/index.html" target="_blank" rel="noopener noreferrer">听声辨位（试听）</a>
          </div>
        </section>
      ) : null}

      {!canParticipate ? (
        <p className="assessmentNotice">当前账号仅可查看已授权记录，不能开始评测。</p>
      ) : null}
      {activeRun ? (
        <p className="assessmentNotice assessmentNoticeWarning" role="alert">
          已有一项评测正在进行，请先恢复、提交或结束该尝试。
          <Link to={activeRun.mode === 'formal' ? `/esports-assessments/sessions/${activeRun.session_id}?test=${activeRun.test_id}` : `/esports-assessments/practice?suite=${activeRun.suite_id}&test=${activeRun.test_id}&practice_id=${activeRun.attempt_id}`}>恢复当前尝试</Link>
        </p>
      ) : null}
      {hasVolatilePending(user.account_id) ? (
        <p className="assessmentNotice assessmentNoticeWarning" role="status">
          结果暂存仅在当前页面有效，刷新或关闭页面后可能无法恢复。
        </p>
      ) : null}
      {recovery.data && recovery.data.items.length > 0 && <section className="assessmentOngoing" aria-label="待恢复尝试"><h2>待恢复尝试</h2><p>无完整本地结果时，只能结束旧尝试，不能恢复计时。</p><ul>{recovery.data.items.map(item => <li key={item.attempt_id}><Link to={item.mode === 'formal' ? `/esports-assessments/sessions/${item.session_id}?test=${item.test_id}` : `/esports-assessments/practice?suite=${item.suite_id}&test=${item.test_id}&practice_id=${item.attempt_id}`}>恢复{item.mode === 'formal' ? '正式' : '练习'} · {suites.find(suite => suite.id === item.suite_id)?.name} · {assessmentTestLabels[item.test_id]}</Link></li>)}</ul>{recovery.data.next_cursor && <button onClick={() => setRecoveryCursor({ account: accountId, cursor: recovery.data.next_cursor! })}>更多待恢复尝试</button>}{cursor && <button onClick={() => setRecoveryCursor({ account: accountId })}>返回恢复首页</button>}</section>}
      {recovery.isError && <p role="alert">恢复列表暂不可用。<button onClick={() => void recovery.refetch()}>重试恢复查询</button></p>}

      {context.data.own_in_progress_sessions.length > 0 ? (
        <section className="assessmentOngoing" aria-labelledby="assessment-ongoing-title">
          <h2 id="assessment-ongoing-title">未完成的正式轮次</h2>
          <div className="assessmentOngoingLinks">
            {context.data.own_in_progress_sessions.map((session) => (
              <Link key={session.id} to={`/esports-assessments/sessions/${session.id}`}>
                继续 {suites.find((suite) => suite.id === session.suite_id)?.name ?? session.suite_id}
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      <section className="assessmentCatalog" aria-label="评测套系">
        {suites.map((suite) => {
          const locallyReady = ASSESSMENT_SUITE_AVAILABILITY[suite.id] || canPreview
          const remotelyReady = serverAvailable.has(suite.id)
          const usable = locallyReady && remotelyReady && canParticipate
          const status = !locallyReady ? '开发中' : !remotelyReady ? '暂未开放'
            : !canParticipate ? '无测试权限' : !ASSESSMENT_SUITE_AVAILABILITY[suite.id] ? '超管试测' : '可进行'
          return (
            <section className="assessmentSuiteCard" aria-label={suite.name} key={suite.id}>
              <div className="assessmentSuiteHeading">
                <h2>{suite.name}</h2>
                <span data-state={usable ? 'available' : 'unavailable'}>{status}</span>
              </div>
              <p>{suite.description}</p>
              {usable ? (
                <div className="assessmentActions"><button type="button" disabled={Boolean(activeRun) || creating} onClick={() => void create(suite.id)}>{suite.name} 正式测试</button>
                  <button type="button" disabled={Boolean(activeRun) || creating} onClick={() => navigate(`/esports-assessments/practice?suite=${suite.id}`)}>{suite.name} 自由练习</button></div>
              ) : null}
            </section>
          )
        })}
      </section>
    </div>
  )
}

export default AssessmentHome
