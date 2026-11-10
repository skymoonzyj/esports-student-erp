import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { ItemRankingPanel } from '../assessmentRankings/ItemRankingPanel'
import { formatShanghaiDateTime } from '../teaching/datetime'
import { canReadReports, errorText, identityKey, modeLabels, privateError, reactionxApi, type ResultReport } from './api'
import './reactionx.css'

export function ReactionxReportPage() {
  const { user } = useAuth(), { resultId } = useParams()
  if (!user || !canReadReports(user)) return <p>暂无实体灯报告读取权限</p>
  if (!resultId || !/^[0-9a-f-]{36}$/i.test(resultId)) return <p role="alert">报告不存在或无权查看</p>
  return <SavedReport key={`${identityKey(user)}:${resultId}`} account={identityKey(user)} id={resultId} />
}
function SavedReport({ account, id }: { account: string; id: string }) {
  const client = useQueryClient(), [denied, setDenied] = useState(false)
  const report = useQuery({ queryKey: ['reactionx', account, 'result', id], queryFn: ({ signal }) => reactionxApi.report(id, signal), enabled: !denied, gcTime: 0, retry: false, staleTime: 0, refetchOnMount: 'always', refetchOnWindowFocus: 'always' })
  useEffect(() => { if (privateError(report.error)) setDenied(true) }, [report.error])
  useEffect(() => {
    if (!denied) return
    const predicate = (query: { queryKey: readonly unknown[] }) => query.queryKey[0] === 'reactionx' && query.queryKey[1] === account && query.queryKey[3] === id && (query.queryKey[2] === 'result' || query.queryKey[4] === 'ranking')
    void client.cancelQueries({ predicate }); client.removeQueries({ predicate })
  }, [account, client, denied, id])
  return <div className="rx-page"><header className="rx-heading"><p className="rx-eyebrow">REACTIONX · SAVED RECORD</p><h1>实体灯训练报告</h1><Link to="/talent-tests/reactionx">返回实体灯专项</Link></header>{denied || report.isError ? <section className="rx-card"><p role="alert">{denied ? '记录不存在或无权操作，请重新确认当前账号权限。' : errorText(report.error)}</p><button onClick={() => { if (denied) setDenied(false); else void report.refetch() }}>重试读取报告</button></section> : !report.data || report.isFetching ? <p role="status">正在核对服务器保存回执…</p> : <><ReportDetails report={report.data} /><SavedRanking key={`${account}:${report.data.subject_person_id}:${id}`} account={account} report={report.data} onAccessRevoked={() => setDenied(true)} /></>}</div>
}
export function ReportDetails({ report }: { report: ResultReport }) {
  const interrupted = report.envelope.end.kind === 'process_interrupted'
  const labels: Record<string, string> = { hit_count: '命中数', miss_count: '漏击数', false_hit_count: '误击数', correct_ignore_count: '正确忽略数', correct_count: '正确数', host_timeout_count: '主机超时数', hardware_timeout_count: '设备超时数', mean_hit_ms: '平均有效命中时间（毫秒）', mean_group_ms: '平均组时间（毫秒）', mean_cycle_ms: '平均周期时间（毫秒）', survival_ms: '存活时间（毫秒）' }
  return <section className="rx-card"><div className="rx-section-heading"><h2>{modeLabels[report.manifest.mode]}</h2><span className="rx-badge">已保存</span></div><p>{report.manifest.mode === 'manual' ? '自由训练 · 不参与排名' : '固定规则专项 · 不计入原八项轮次'}</p><p>服务器保存时间：{formatShanghaiDateTime(report.receipt.saved_at)}（中国时间）</p>
    <p className={interrupted ? 'rx-warning' : ''}>{interrupted ? '进程中断，记录不完整；最后观测时长下界' : '记录的活动时长'}：{report.envelope.end.active_elapsed_ms} ms{interrupted ? '，不代表完整完成时长。' : ''}</p><p>设备数量：{report.manifest.devices.length} · 布局：{({ standing: '站立', seated: '坐姿', custom: '自定义', unregistered: '未登记' })[report.manifest.layout.posture]}</p>
    <dl className="rx-metrics">{Object.entries(report.metrics.values).filter(([key]) => key !== 'active_elapsed_ms').map(([key, value]) => <div key={key}><dt>{labels[key] ?? key}</dt><dd>{value === null ? '无有效数据' : Number.isInteger(value) ? value : value.toFixed(2)}</dd></div>)}</dl>
    <p>原始成绩不可被排名刷新覆盖；有效性及排名由服务器判定。</p>
  </section>
}
function SavedRanking({ account, report, onAccessRevoked }: { account: string; report: ResultReport; onAccessRevoked: () => void }) {
  const ranking = useQuery({ queryKey: ['reactionx', account, report.subject_person_id, report.receipt.result_id, 'ranking'], queryFn: ({ signal }) => reactionxApi.ranking(report.receipt.result_id, signal), gcTime: 0, retry: false, staleTime: 0, refetchOnMount: 'always', refetchOnWindowFocus: 'always' })
  useEffect(() => { if (privateError(ranking.error)) onAccessRevoked() }, [onAccessRevoked, ranking.error])
  return <ItemRankingPanel view={ranking.isError || ranking.isFetching ? null : ranking.data} loading={ranking.isFetching} error={ranking.error} onRetry={() => void ranking.refetch()} />
}
