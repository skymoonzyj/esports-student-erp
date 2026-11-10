import { useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { assessmentApi } from './api'
import type { PracticeHistoryItem } from './apiContracts'
import type { Mode } from './contracts'
import { assessmentKeys } from './keys'
import { AuthorizedReportList } from './AssessmentReportsPanel'
import { comparePractice } from './practiceComparison'
import { chinaDate, FrozenSetup, hasReportPermission, Metrics, parseReportFilters, QualityNotice, ReportError, ReportPager, suiteLabels, testLabels, TrackingNote, uniqueBy, useReportPager } from './reportPresentation'
import './reports.css'

function PracticeRecords({ items }: { items: PracticeHistoryItem[] }) {
  const [selected, setSelected] = useState<string[]>([])
  const chosen = selected.flatMap(id => items.filter(item => item.attempt_id === id))
  const comparison = chosen.length === 2 ? comparePractice(chosen[0], chosen[1]) : null
  return (
    <>
      <p className="assessmentReportsHint">仅可选择当前页的两条本人练习记录。差值按选择顺序：第二条减第一条；切页后清除选择。</p>
      {items.length ? items.map((item, index) => (
        <article className="assessmentReportsCard" key={item.attempt_id}>
          <h2>{suiteLabels[item.suite_id]} · {testLabels[item.test_id]}</h2>
          <p>{chinaDate(item.completed_at)}</p>
          <h3>原始指标</h3>
          <Metrics metrics={item.scored.metrics} />
          <TrackingNote testId={item.test_id} />
          <QualityNotice flags={item.scored.quality_flags} />
          <FrozenSetup result={item} />
          <label className="assessmentReportsCheckbox">
            <input type="checkbox" checked={selected.includes(item.attempt_id)}
              disabled={selected.length === 2 && !selected.includes(item.attempt_id)}
              onChange={event => setSelected(value => event.target.checked
                ? [...value, item.attempt_id] : value.filter(id => id !== item.attempt_id))} />
            选择第 {index + 1} 条练习进行比较
          </label>
        </article>
      )) : <p>暂无已完成练习</p>}
      {comparison ? (
        <section className="assessmentReportsComparison" aria-label="练习原始指标比较">
          <h2>练习原始指标比较</h2>
          {comparison.reasons.length ? (
            <><p>条件不一致，仅并列展示，不计算差值。</p><ul>{comparison.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></>
          ) : <><h3>第二条 − 第一条</h3><Metrics metrics={comparison.differences} difference /></>}
          <div className="assessmentReportsColumns">
            {chosen.map((item, index) => (
              <div key={item.attempt_id}><h3>第 {index + 1} 条所选记录</h3><Metrics metrics={item.scored.metrics} /><FrozenSetup result={item} /></div>
            ))}
          </div>
        </section>
      ) : null}
    </>
  )
}
function OwnHistory({ account, mode }: { account: string; mode: Mode }) {
  const pager = useReportPager()
  const query = { mode, cursor: pager.cursor, limit: 20 }
  const history = useQuery({
    queryKey: [...assessmentKeys.history(account, mode), query],
    queryFn: () => assessmentApi.history(query),
    retry: false, gcTime: 0, refetchOnMount: 'always',
  })
  if (history.isError) return <ReportError error={history.error} />
  if (history.isPending || history.isFetching) return <p role="status">正在读取本人记录…</p>
  return (
    <>
      {mode === 'practice' ? (
        <PracticeRecords key={pager.cursor ?? 'first'} items={uniqueBy(history.data.items.filter(item => item.mode === 'practice'), item => item.attempt_id)} />
      ) : !history.data.items.length ? <p>暂无正式轮次</p> : (
        <ul className="assessmentReportsList">
          {uniqueBy(history.data.items.filter(item => item.mode === 'formal'), item => item.session.id).map(({ session, created_at }) => (
            <li key={session.id}>
              <span>{suiteLabels[session.suite_id]} · {chinaDate(created_at)} · {session.status === 'completed' ? '已完成' : session.status === 'abandoned' ? '已放弃' : '进行中'} · {session.results.length}/{session.members.length}</span>
              {session.report_id ? <Link to={`/esports-assessments/reports/${session.report_id}`}>查看报告</Link> : <Link to={`/esports-assessments/sessions/${session.id}`}>查看轮次</Link>}
            </li>
          ))}
        </ul>
      )}
      <ReportPager pager={pager} next={history.data.next_cursor} total={history.data.visible_total} />
    </>
  )
}
function ReportFilters({ search, apply }: { search: URLSearchParams; apply: (params: URLSearchParams) => void }) {
  const [error, setError] = useState(false)
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const next = new URLSearchParams({ view: 'reports' })
    for (const key of ['suite_id', 'person_id', 'date_from', 'date_to']) {
      const value = String(data.get(key) ?? '').trim()
      if (value) next.set(key, value)
    }
    if (!parseReportFilters(next)) { setError(true); return }
    setError(false); apply(next)
  }
  return (
    <form className="assessmentReportsFilters" onSubmit={submit}>
      <label>套系<select name="suite_id" defaultValue={search.get('suite_id') ?? ''}><option value="">全部套系</option>{Object.entries(suiteLabels).map(([id, name]) => <option value={id} key={id}>{name}</option>)}</select></label>
      <label>人员 ID<input name="person_id" defaultValue={search.get('person_id') ?? ''} placeholder="已授权人员 UUID" /></label>
      <label>开始日期<input name="date_from" type="date" defaultValue={search.get('date_from') ?? ''} /></label>
      <label>结束日期<input name="date_to" type="date" defaultValue={search.get('date_to') ?? ''} /></label>
      <button type="submit">应用筛选</button>
      {error ? <p role="alert">筛选条件无效，请检查套系、人员 ID 和日期范围。</p> : null}
    </form>
  )
}
export function AssessmentHistoryPage() {
  const { user } = useAuth()
  const location = useLocation()
  const [search, setSearch] = useSearchParams()
  const allowed = hasReportPermission(user)
  const account = user?.account_id ?? 'anonymous'
  const context = useQuery({
    queryKey: assessmentKeys.context(account), queryFn: assessmentApi.context,
    enabled: allowed, retry: false, gcTime: 0, refetchOnMount: 'always',
  })
  if (!user) return <p role="status">正在确认读取权限…</p>
  if (!allowed) return <p>暂无天赋评测读取权限</p>
  if (context.isError) return <ReportError error={context.error} />
  if (context.isPending || context.isFetching) return <p role="status">正在确认读取权限…</p>
  const { can_read_self: self, can_read_reports: reports } = context.data
  if (!self && !reports) return <p>暂无天赋评测读取权限</p>
  const view = search.get('view') ?? (self ? 'own' : 'reports')
  const mode = search.get('mode') ?? 'formal'
  if (view === 'own' && !self) return <Navigate replace to="/esports-assessments/history?view=reports" state={{ assessmentReadOnly: true }} />
  if (view === 'reports' && !reports) return <p>暂无已完成报告读取权限</p>
  if (!['own', 'reports'].includes(view) || (view === 'own' && !['formal', 'practice'].includes(mode))) return <p role="alert">记录视图无效</p>
  const filters = parseReportFilters(search)
  const identity = `${account}:${search.toString()}`
  return (
    <div className="assessmentReports">
      <header><h1>评测记录</h1><Link to="/esports-assessments">返回天赋评测 2</Link></header>
      {location.state?.assessmentReadOnly ? <p>当前仅可查看已授权的完成报告，不能读取本人练习或进行中轮次。</p> : null}
      <div className="assessmentReportsTabs" role="tablist" aria-label="记录类型">
        {self ? <>
          <button role="tab" aria-selected={view === 'own' && mode === 'formal'} onClick={() => setSearch({ view: 'own', mode: 'formal' })}>本人正式</button>
          <button role="tab" aria-selected={view === 'own' && mode === 'practice'} onClick={() => setSearch({ view: 'own', mode: 'practice' })}>自由练习</button>
        </> : null}
        {reports ? <button role="tab" aria-selected={view === 'reports'} onClick={() => setSearch({ view: 'reports' })}>已完成报告</button> : null}
      </div>
      {view === 'own' ? <OwnHistory key={identity} account={account} mode={mode as Mode} /> : <>
        <ReportFilters key={`filters:${identity}`} search={search} apply={params => setSearch(params)} />
        {filters ? <AuthorizedReportList key={`reports:${identity}`} account={account} filters={filters} /> : <p role="alert">筛选条件无效，请检查套系、人员 ID 和日期范围。</p>}
      </>}
    </div>
  )
}
export default AssessmentHistoryPage
