import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { formatAuditDateTime } from "../shared/date"
import {
  acknowledgeOperationsAlert,
  getOperationsAlerts,
  getOperationsHistory,
} from "./api"
import type { AlertFilter } from "./api"
import { OperationsBackupPanel } from "./OperationsBackupPanel"

function Pager({ page, pageSize, total, busy, onChange }: {
  page: number; pageSize: number; total: number; busy: boolean; onChange: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return <div className="operationsPager">
    <span>共 {total} 条 · 第 {page} / {pages} 页</span>
    <button type="button" disabled={page <= 1 || busy} onClick={() => onChange(page - 1)}>上一页</button>
    <button type="button" disabled={page >= pages || busy} onClick={() => onChange(page + 1)}>下一页</button>
  </div>
}

function Failure({ onRetry }: { onRetry: () => void }) {
  return <p className="formError" role="alert">记录读取失败，请稍后重试。 <button type="button" onClick={onRetry}>重新读取</button></p>
}

export function OperationsHistoryPanel({ autoRefresh }: { autoRefresh: boolean }) {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<AlertFilter>("active")
  const [alertPage, setAlertPage] = useState(1)
  const [historyPage, setHistoryPage] = useState(1)
  const [range, setRange] = useState(() => ({ days: 1, end: Date.now() }))
  const startsAt = new Date(range.end - range.days * 86_400_000).toISOString()
  const endsAt = new Date(range.end).toISOString()
  const alerts = useQuery({
    queryKey: ["operations", "alerts", filter, alertPage],
    queryFn: () => getOperationsAlerts(filter, alertPage),
    refetchInterval: autoRefresh ? 30_000 : false,
  })
  const history = useQuery({
    queryKey: ["operations", "history", startsAt, endsAt, historyPage],
    queryFn: () => getOperationsHistory(startsAt, endsAt, historyPage),
  })
  const acknowledge = useMutation({
    mutationFn: acknowledgeOperationsAlert,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["operations", "alerts"] }),
  })
  return <div className="operationsReliability">
    <OperationsBackupPanel autoRefresh={autoRefresh} />
    <section className="operationsPanel" aria-label="运维告警记录">
      <div className="operationsPanelHeading">
        <h2>运维告警</h2>
        <label>告警状态 <select aria-label="告警状态" value={filter} onChange={(event) => {
          setFilter(event.target.value as AlertFilter)
          setAlertPage(1)
        }}><option value="active">当前异常</option><option value="resolved">已恢复</option><option value="all">全部记录</option></select></label>
      </div>
      <p className="operationsHelp">确认表示已知晓；系统检测恢复后才会关闭告警。历史记录会保留。</p>
      {acknowledge.isError ? <p className="formError" role="alert">确认失败，请重试。</p> : null}
      {alerts.isPending ? <p>正在读取告警…</p> : alerts.isError ? <Failure onRetry={() => void alerts.refetch()} /> : <>
        <div className="operationsAlerts">
          {alerts.data.items.length === 0 ? <p className="operationsCompactEmpty">当前筛选下暂无告警</p> : alerts.data.items.map((item) => <article key={item.id} className={`operationsAlert operationsAlert--${item.status === "resolved" ? "resolved" : item.severity}`}>
            <div className="operationsAlertTitle"><strong>{item.title}</strong><span>{item.status === "resolved" ? "已恢复" : item.severity === "critical" ? "异常" : "需关注"}</span></div>
            <p>{item.detail}</p>
            <div className="operationsAlertTimes"><span>首次发现 {formatAuditDateTime(item.first_seen_at)}</span><span>最近检测 {formatAuditDateTime(item.last_seen_at)}</span>{item.resolved_at ? <span>恢复于 {formatAuditDateTime(item.resolved_at)}</span> : null}</div>
            {item.acknowledged_at ? <small>已确认 · {formatAuditDateTime(item.acknowledged_at)}</small> : item.status === "active" ? <button type="button" disabled={acknowledge.isPending} onClick={() => acknowledge.mutate(item.id)}>确认已知晓</button> : null}
          </article>)}
        </div>
        <Pager page={alertPage} pageSize={alerts.data.page_size} total={alerts.data.total} busy={alerts.isFetching} onChange={setAlertPage} />
      </>}
    </section>
    <section className="operationsPanel" aria-label="历史请求记录">
      <div className="operationsPanelHeading">
        <h2>历史请求记录</h2>
        <div className="operationsHistoryControls"><label>历史范围 <select aria-label="历史范围" value={range.days} onChange={(event) => {
          setRange({ days: Number(event.target.value), end: Date.now() })
          setHistoryPage(1)
        }}><option value={1}>近 1 天</option><option value={7}>近 7 天</option><option value={30}>近 30 天</option></select></label>
          <button type="button" onClick={() => { setRange((value) => ({ ...value, end: Date.now() })); setHistoryPage(1) }}>刷新历史</button>
        </div>
      </div>
      <p className="operationsHelp">按分钟汇总，有请求时产生记录。仅保留启用后的数据，默认保存 {history.data?.retention_days ?? 30} 天。</p>
      {history.isPending ? <p>正在读取历史…</p> : history.isError ? <Failure onRetry={() => void history.refetch()} /> : <>
        <div className="operationsHistoryTable"><table><thead><tr><th>时间（北京时间）</th><th>请求数</th><th>客户端错误</th><th>服务端错误</th><th>平均耗时</th><th>最大耗时</th></tr></thead><tbody>
          {history.data.items.map((point) => <tr key={point.starts_at}><td>{formatAuditDateTime(point.starts_at)}</td><td>{point.request_count}</td><td>{point.client_error_count}</td><td>{point.server_error_count}</td><td>{point.average_latency_ms.toFixed(1)} ms</td><td>{point.max_latency_ms.toFixed(1)} ms</td></tr>)}
          {history.data.items.length === 0 ? <tr><td colSpan={6}>该时间范围内暂无历史记录</td></tr> : null}
        </tbody></table></div>
        <Pager page={historyPage} pageSize={history.data.page_size} total={history.data.total} busy={history.isFetching} onChange={setHistoryPage} />
      </>}
    </section>
  </div>
}
