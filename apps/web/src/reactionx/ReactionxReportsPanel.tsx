import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { formatShanghaiDateTime } from '../teaching/datetime'
import { canReadReports, errorText, identityKey, modeLabels, privateError, reactionxApi } from './api'
import './reactionx.css'

export function ReactionxReportsPanel({ personId }: { personId?: string }) {
  const { user } = useAuth()
  return <section className="rx-card rx-reports" aria-label="实体灯报告"><h2>实体灯报告</h2>{!user ? <p>正在确认读取权限…</p> : !canReadReports(user) ? <p>暂无实体灯报告读取权限</p> : <SavedReports key={`${identityKey(user)}:${personId ?? 'all'}`} account={identityKey(user)} personId={personId} />}</section>
}
function SavedReports({ account, personId }: { account: string; personId?: string }) {
  const client = useQueryClient(), [denied, setDenied] = useState(false)
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined])
  const cursor = cursors.at(-1)
  const reports = useQuery({ queryKey: ['reactionx', account, 'reports', personId, cursor], queryFn: ({ signal }) => reactionxApi.reports(personId, cursor, signal), enabled: !denied, gcTime: 0, retry: false, staleTime: 0, refetchOnMount: 'always', refetchOnWindowFocus: 'always' })
  useEffect(() => { if (privateError(reports.error)) setDenied(true) }, [reports.error])
  useEffect(() => {
    if (!denied) return
    const queryKey = ['reactionx', account, 'reports', personId]
    void client.cancelQueries({ queryKey }); client.removeQueries({ queryKey })
  }, [account, client, denied, personId])
  if (denied || reports.isError) return <div><p role="alert">{denied ? '记录不存在或无权操作，请重新确认当前账号权限。' : errorText(reports.error)}</p><button onClick={() => { if (denied) { setCursors([undefined]); setDenied(false) } else void reports.refetch() }}>重试实体灯报告</button></div>
  if (!reports.data || reports.isFetching) return <p role="status">正在读取已保存报告…</p>
  return <>{reports.data.items.length ? <ul className="rx-report-list">{reports.data.items.map(item => <li key={item.receipt.result_id}><div><strong>{modeLabels[item.mode]}</strong><p>{item.mode === 'manual' ? '自由训练 · 不参与排名' : item.ranking_eligible ? '已保存 · 排名以服务器报告为准' : '已保存 · 暂不参与排名'}</p><time dateTime={item.receipt.saved_at}>{formatShanghaiDateTime(item.receipt.saved_at)}（中国时间）</time></div><Link to={`/talent-tests/reactionx/reports/${item.receipt.result_id}`}>查看实体灯报告</Link></li>)}</ul> : <p>暂无已保存的实体灯报告</p>}
    <nav className="rx-actions" aria-label="实体灯报告分页"><button disabled={cursors.length <= 1} onClick={() => setCursors(old => old.slice(0, -1))}>上一页</button><span>第 {cursors.length} 页</span><button disabled={!reports.data.next_cursor} onClick={() => setCursors(old => [...old, reports.data.next_cursor ?? undefined])}>下一页</button><button onClick={() => void reports.refetch()}>刷新已保存报告</button></nav></>
}
