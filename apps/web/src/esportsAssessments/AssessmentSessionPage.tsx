import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { z } from 'zod'
import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { assessmentApi } from './api'
import { assessmentKeys } from './keys'
import { ASSESSMENT_SUITE_AVAILABILITY } from './availability'
import { AssessmentHost } from './AssessmentHost'
import { assessmentTaskLabel } from './hostState'
import { useAssessmentSession, type SessionIdentity } from './useAssessmentSession'
import { pcFpsFactory } from './suites/pcFps'
import { mobileFpsFactory } from './suites/mobileFps'
import { pcMobaFactory } from './suites/pcMoba'
import { mobileMobaFactory } from './suites/mobileMoba'
import type { SessionView, TaskConfig } from './contracts'
import { Metrics, QualityNotice } from './reportPresentation'
import { loadPending } from './pending'
import './reports.css'

const factories = { 'pc-fps': pcFpsFactory, 'mobile-fps': mobileFpsFactory, 'pc-moba': pcMobaFactory, 'mobile-moba': mobileMobaFactory }
function legacyRoundNotice(config: Pick<TaskConfig, 'suite_id' | 'catalog_version'>): string | undefined {
  if ((config.suite_id === 'pc-fps' || config.suite_id === 'mobile-fps') && config.catalog_version === `${config.suite_id}-v1`) {
    return '本轮使用旧版目标规则。完成本轮后新建，或放弃本轮后新建，可使用屏内刷新。放弃会保留已保存项目，但本轮不再生成报告。'
  }
  if (config.suite_id.endsWith('moba') && config.catalog_version === `${config.suite_id}-v1`) {
    return '本轮使用旧版 MOBA 操作。完成本轮后新建，或主动放弃后新建，可使用新版操作反馈（端游支持左键兼容操作）。已保存成绩保留，放弃的轮次不再生成报告。'
  }
}
export function AssessmentTask({ identity, config, onBack, allowStart = true }: { identity: SessionIdentity; config: TaskConfig; onBack: () => void; allowStart?: boolean }) {
  const session = useAssessmentSession(identity)
  const currentConfig = session.serverView?.config ?? config
  return <AssessmentHost config={currentConfig} factory={factories[config.suite_id]} session={session} mode={identity.mode} allowStart={allowStart} preparationNotice={identity.mode === 'formal' ? legacyRoundNotice(currentConfig) : undefined} onBack={onBack} />
}

/** Suite withdrawal blocks allocation, not authorization-checked reconciliation of existing raw. */
export function AssessmentRecovery({ identity }: { identity: SessionIdentity }) {
  const session = useAssessmentSession(identity)
  const emptyLabel = session.serverView?.config.report_version === `${session.serverView?.config.suite_id}-v2` ? '—' : undefined
  return <section aria-label="恢复与保存"><h1>此专项暂未开放</h1><p>不能开始新尝试，已有结果仍可与服务器对账。</p>
    {session.error && <p role="alert">{session.error}</p>}
    {session.volatile && <p role="alert">当前结果仅保留在本页，刷新可能无法恢复。</p>}
    {session.saved && <section className="assessmentReports" aria-label="结果已保存"><h2>结果已保存</h2><Metrics metrics={session.saved.scored.metrics} emptyLabel={emptyLabel} /><QualityNotice flags={session.saved.scored.quality_flags} /></section>}
    <button disabled={session.busy || session.revoked} onClick={() => void session.reconcile()}>重新对账</button>
    {session.pending && <button disabled={session.busy || session.revoked} onClick={() => void session.retry()}>手动重试保存</button>}
    {session.serverView && ['running', 'awaiting_submission'].includes(session.serverView.state) && <button disabled={session.busy || session.revoked} onClick={() => void session.interrupt()}>结束未完成尝试并重新开始</button>}
    <Link to="/esports-assessments">返回入口</Link></section>
}

function AbandonRound({ sessionId, account, onSaved }: { sessionId: string; account: string; onSaved: (view: SessionView) => void }) {
  const { user } = useAuth()
  const owner = useRef(user?.account_id)
  owner.current = user?.account_id
  const alive = useRef(true)
  const key = useRef<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  async function abandon() {
    if (busy || owner.current !== account) return
    key.current ??= crypto.randomUUID()
    setBusy(true); setError(false)
    try {
      const view = await assessmentApi.abandonSession(sessionId, { operation_key: key.current })
      if (alive.current && owner.current === account) onSaved(view)
    } catch { if (alive.current && owner.current === account) setError(true) }
    finally { if (alive.current && owner.current === account) setBusy(false) }
  }
  return <section aria-label="放弃正式轮次">{error && <p role="alert">放弃请求未确认，请使用原操作键重试，或刷新对账。</p>}{confirm ? <><p>放弃后保留已保存项目，但本轮不再生成报告；未提交尝试将结束。</p><button disabled={busy} onClick={() => void abandon()}>确认放弃本轮</button><button disabled={busy} onClick={() => setConfirm(false)}>保留本轮</button></> : <button onClick={() => setConfirm(true)}>放弃本轮</button>}</section>
}

export function AssessmentSessionPage() {
  const { user } = useAuth()
  const { sessionId = '' } = useParams<{ sessionId: string }>()
  const [search, setSearch] = useSearchParams()
  const account = user?.account_id ?? ''
  const validId = z.uuid().safeParse(sessionId).success
  const client = useQueryClient()
  const activeTask = useRef<{ key: string; config: TaskConfig } | null>(null)
  const context = useQuery({ queryKey: assessmentKeys.context(account), queryFn: assessmentApi.context, enabled: !!account && validId })
  const session = useQuery({ queryKey: [...assessmentKeys.root(account), 'formal-session', sessionId], queryFn: () => assessmentApi.readSession(sessionId), enabled: !!account && validId })
  const catalog = useQuery({ queryKey: assessmentKeys.suites(account), queryFn: assessmentApi.suites, enabled: !!account && validId })
  if (!validId || session.isError) return <p role="alert">轮次不存在或无权访问。</p>
  if (catalog.isError) return <section role="alert"><p>评测目录版本无法识别或暂时不可用，请刷新页面后重试。</p><button onClick={() => void catalog.refetch()}>刷新目录</button></section>
  if (context.isError) return <section role="alert"><p>无法确认权限，请重试。</p><button onClick={() => void context.refetch()}>重试</button></section>
  if (!context.data || !session.data || !catalog.data) return <p role="status">正在加载正式轮次…</p>
  const view = session.data
  const selected = view.members.find(test => test === search.get('test'))
  const suite = catalog.data.items.find(item => item.suite_id === view.suite_id)
  const available = Boolean(suite) && (ASSESSMENT_SUITE_AVAILABILITY[view.suite_id] || user?.is_super_admin === true)
    && context.data.available_suites.includes(view.suite_id)
  const config = selected ? view.suite_config.tasks[selected] : undefined
  const roundNotice = legacyRoundNotice(view.suite_config)
  const completed = selected && view.results.some(result => result.test_id === selected)
  const activeKey = `${account}:${sessionId}:${selected}`
  const back = () => {
    activeTask.current = null
    setSearch({})
    void client.invalidateQueries({ queryKey: assessmentKeys.root(account) })
  }
  // A fresh reload can already see the result in the session while its lost-response
  // pending payload remains. Mount its owner so attempt GET confirms and clears it.
  const recoverSaved = completed && loadPending(account).some(pending => pending.mode === 'formal'
    && pending.suite_id === view.suite_id && pending.session_id === sessionId && pending.test_id === selected)
  if (config && (recoverSaved || (!completed && view.status === 'in_progress' && available))) activeTask.current = { key: activeKey, config }
  // Saving invalidates this parent query. Keep the current writer alive through its
  // second identity check and authoritative report navigation, including result 4/4.
  if (context.data.can_take && context.data.can_read_self && activeTask.current?.key === activeKey) {
    return <AssessmentTask key={activeKey} identity={{ mode: 'formal', suite_id: view.suite_id, session_id: sessionId, test_id: selected }} config={activeTask.current.config} allowStart={available && !completed && view.status === 'in_progress'} onBack={back} />
  }
  if (view.status === 'completed' && view.report_id) return <section><h1>正式评测已完成</h1><Link to={`/esports-assessments/reports/${view.report_id}`}>查看正式报告</Link></section>
  if (view.status === 'abandoned') return <p>此轮次已放弃。<Link to="/esports-assessments">返回入口</Link></p>
  if (!context.data.can_take || !context.data.can_read_self) return <p role="alert">当前账号没有参加本专项的权限。</p>
  if (!suite || !available) return selected ? <AssessmentRecovery key={activeKey} identity={{ mode: 'formal', suite_id: view.suite_id, session_id: sessionId, test_id: selected }} />
    : <section><p role="status">此专项暂未开放。请选择项目查看已有尝试。</p>{view.members.map(test => <button key={test} onClick={() => setSearch({ test })}>{assessmentTaskLabel(view.suite_config.tasks[test]!)}</button>)}<Link to="/esports-assessments">返回入口</Link></section>
  return <section className="assessmentSession"><h1>正式评测项目</h1><p aria-live="polite">已保存 {view.results.length} / 4</p>{roundNotice && <p className="assessmentNotice">{roundNotice}</p>}<p>可以任意顺序完成；只以服务器保存为准。每项正式运行 60 秒，试练最多 20 秒，试练不保存成绩。</p><div className="assessmentActions">{view.members.map(test => <button key={test} disabled={view.results.some(result => result.test_id === test)} onClick={() => setSearch({ test })}>{assessmentTaskLabel(view.suite_config.tasks[test]!)}{view.results.some(result => result.test_id === test) ? '（已保存）' : ''}</button>)}</div><AbandonRound key={`${account}:${sessionId}`} account={account} sessionId={sessionId} onSaved={value => { client.setQueryData([...assessmentKeys.root(account), 'formal-session', sessionId], value); void client.invalidateQueries({ queryKey: assessmentKeys.root(account) }) }} /><Link to="/esports-assessments">返回评测入口</Link></section>
}
export default AssessmentSessionPage
