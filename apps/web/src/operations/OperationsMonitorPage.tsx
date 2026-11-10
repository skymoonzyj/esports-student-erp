import { useQuery } from "@tanstack/react-query"
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Cpu,
  Database,
  Gauge,
  MemoryStick,
  RefreshCw,
  ServerCog,
  ShieldAlert,
  TimerReset,
} from "lucide-react"
import { useEffect, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { SystemAppearancePanel } from "../appearance/SystemAppearancePanel"
import { formatAuditDateTime } from "../shared/date"
import {
  getOperationsOverview,
  operationsKeys,
} from "./api"
import type {
  HealthStatus,
  OperationsOverview,
} from "./api"
import "./operations.css"
import { OperationsHistoryPanel } from "./OperationsHistoryPanel"
import { OperationsRequestErrorsPanel } from "./OperationsRequestErrorsPanel"

const REFRESH_INTERVAL_SECONDS = 30
const windowOptions = [
  { value: 5, label: "近 5 分钟" },
  { value: 60, label: "近 1 小时" },
  { value: 360, label: "近 6 小时" },
  { value: 1440, label: "近 24 小时" },
]

const statusLabels: Record<HealthStatus, string> = {
  healthy: "正常",
  warning: "需关注",
  critical: "异常",
  unknown: "等待数据",
}

function errorMessage(error: unknown): string {
  if (error instanceof ProblemDetailsError) return error.problem.detail
  if (error instanceof Error) return error.message
  return "运维数据暂时无法读取"
}

function formatMetric(value: number, digits = 0): string {
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value)
}

function formatLatency(value: number | null): string {
  if (value === null) return "--"
  if (value >= 1_000) return `${formatMetric(value / 1_000, 2)} s`
  return `${formatMetric(value, value < 10 ? 1 : 0)} ms`
}

function formatDuration(seconds: number): string {
  const days = Math.floor(seconds / 86_400)
  const hours = Math.floor((seconds % 86_400) / 3_600)
  const minutes = Math.floor((seconds % 3_600) / 60)
  if (days > 0) return `${days} 天 ${hours} 小时`
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟`
  return `${minutes} 分钟`
}

function formatChartTime(value: string, windowMinutes: number): string {
  const options: Intl.DateTimeFormatOptions = {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }
  if (windowMinutes >= 1440) {
    options.month = "2-digit"
    options.day = "2-digit"
  }
  return new Intl.DateTimeFormat("zh-CN", options).format(new Date(value))
}

function StatusBadge({ status }: { status: HealthStatus }) {
  const Icon = status === "healthy"
    ? CheckCircle2
    : status === "critical"
      ? ShieldAlert
      : AlertTriangle
  return (
    <span className={`operationsStatus operationsStatus--${status}`}>
      <Icon aria-hidden="true" />
      {statusLabels[status]}
    </span>
  )
}

function HealthGauge({ score, status }: { score: number; status: HealthStatus }) {
  const radius = 50
  const circumference = Math.PI * 2 * radius
  const offset = circumference * (1 - score / 100)
  return (
    <div className={`healthGauge healthGauge--${status}`}>
      <svg viewBox="0 0 120 120" role="img" aria-label={`服务健康评分 ${score} 分`}>
        <circle className="healthGaugeTrack" cx="60" cy="60" r={radius} />
        <circle
          className="healthGaugeValue"
          cx="60"
          cy="60"
          r={radius}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      <strong>{score}</strong>
      <span>健康评分</span>
    </div>
  )
}

function TrafficChart({ overview }: { overview: OperationsOverview }) {
  const series = overview.traffic.series
  const width = 720
  const height = 164
  const padding = 12
  const observedMaximum = Math.max(0, ...series.map((point) => point.request_count))
  const axisMaximum = Math.max(2, Math.ceil(observedMaximum / 2) * 2)
  const chartPoints = series.map((point, index) => {
    const x = padding + (index / Math.max(1, series.length - 1)) * (width - padding * 2)
    const y = height - padding
      - (point.request_count / axisMaximum) * (height - padding * 2)
    return { x, y, point }
  })
  const points = chartPoints.map(({ x, y }) => `${x},${y}`).join(" ")
  const errorPoints = chartPoints.filter(({ point }) => point.error_count > 0)
  const yTicks = [axisMaximum, axisMaximum / 2, 0]
  const xTickIndexes = series.length === 0
    ? []
    : [...new Set([0, Math.floor((series.length - 1) / 2), series.length - 1])]
  const hasTraffic = series.some((point) => point.request_count > 0)

  return (
    <div
      className="trafficChart"
      role="img"
      aria-label={`请求量趋势，纵轴最高 ${formatMetric(axisMaximum)} 次，红点表示存在服务端错误`}
    >
      <div
        className="trafficChartYAxis"
        aria-label={`请求数刻度：0 到 ${formatMetric(axisMaximum)} 次`}
      >
        <small>次数</small>
        {yTicks.map((tick) => <span key={tick}>{formatMetric(tick)}</span>)}
      </div>
      <div className="trafficChartPlot">
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
          <g className="trafficChartGrid">
            {[padding, height / 2, height - padding].map((y) => (
              <line key={y} x1={padding} y1={y} x2={width - padding} y2={y} />
            ))}
          </g>
          {hasTraffic ? <polyline points={points} /> : null}
          {chartPoints.map(({ x, y, point }) => (
            <circle
              className="trafficChartHitPoint"
              key={point.starts_at}
              cx={x}
              cy={y}
              r="9"
            >
              <title>
                {formatChartTime(point.starts_at, overview.window_minutes)}：
                {point.request_count} 次请求，{point.error_count} 次服务端错误
              </title>
            </circle>
          ))}
          {errorPoints.map(({ x, y, point }) => (
            <circle
              className="trafficChartErrorPoint"
              key={`error-${point.starts_at}`}
              cx={x}
              cy={y}
              r="4"
            />
          ))}
        </svg>
        {!hasTraffic ? <div className="trafficChartEmpty">当前时间范围内暂无业务请求</div> : null}
      </div>
      <div className="trafficChartXAxis" aria-label="时间刻度">
        {xTickIndexes.map((index) => (
          <span key={series[index].starts_at}>
            {formatChartTime(series[index].starts_at, overview.window_minutes)}
          </span>
        ))}
      </div>
    </div>
  )
}

function MetricPair({ label, value }: { label: string; value: string }) {
  return (
    <div className="operationsMetricPair">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

export function OperationsMonitorPage() {
  const [windowMinutes, setWindowMinutes] = useState(60)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [clockTick, setClockTick] = useState(Date.now())
  const overview = useQuery({
    queryKey: operationsKeys.overview(windowMinutes),
    queryFn: () => getOperationsOverview(windowMinutes),
    refetchInterval: autoRefresh ? REFRESH_INTERVAL_SECONDS * 1_000 : false,
    refetchIntervalInBackground: false,
  })

  useEffect(() => {
    const interval = window.setInterval(() => setClockTick(Date.now()), 1_000)
    return () => window.clearInterval(interval)
  }, [])

  const secondsToRefresh = autoRefresh && overview.dataUpdatedAt
    ? Math.max(
      0,
      REFRESH_INTERVAL_SECONDS
        - Math.floor(Math.max(0, clockTick - overview.dataUpdatedAt) / 1_000),
    )
    : null

  if (overview.isPending) {
    return (
      <main className="operationsPage">
        <SystemAppearancePanel key="system-appearance" />
        <div className="operationsInitialState" aria-live="polite">
          <Activity aria-hidden="true" />
          <p>正在汇总运行状态...</p>
        </div>
      </main>
    )
  }

  if (overview.isError || !overview.data) {
    return (
      <main className="operationsPage">
        <SystemAppearancePanel key="system-appearance" />
        <div className="operationsInitialState operationsInitialState--error" role="alert">
          <ShieldAlert aria-hidden="true" />
          <strong>运维数据读取失败</strong>
          <p>{errorMessage(overview.error)}</p>
          <button type="button" onClick={() => void overview.refetch()}>
            <RefreshCw aria-hidden="true" />重试
          </button>
        </div>
      </main>
    )
  }

  const data = overview.data
  const background = data.background_tasks
  return (
    <main className="operationsPage">
      <header className="operationsHeader">
        <div>
          <p className="pageEyebrow">SYSTEM OPERATIONS</p>
          <h1><Activity aria-hidden="true" />运维监控</h1>
          <div className="operationsLiveLine">
            <span className={`operationsLiveDot operationsLiveDot--${data.health.status}`} />
            <strong>{statusLabels[data.health.status]}</strong>
            <span>刷新于 {formatAuditDateTime(data.generated_at)}</span>
            {secondsToRefresh !== null ? <span>剩余 {secondsToRefresh} 秒</span> : null}
          </div>
        </div>
        <div className="operationsControls">
          <label>
            <span>时间范围</span>
            <select
              value={windowMinutes}
              onChange={(event) => setWindowMinutes(Number(event.target.value))}
            >
              {windowOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="operationsToggle">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
            />
            <span aria-hidden="true" />
            自动刷新
          </label>
          <button
            type="button"
            className="operationsRefreshButton"
            aria-label="立即刷新运维数据"
            title="立即刷新"
            disabled={overview.isFetching}
            onClick={() => void overview.refetch()}
          >
            <RefreshCw className={overview.isFetching ? "isSpinning" : ""} aria-hidden="true" />
          </button>
        </div>
      </header>

      <SystemAppearancePanel key="system-appearance" />
      <section className="operationsSummaryGrid" aria-label="服务运行概览">
        <article className="operationsPanel operationsHealthPanel">
          <HealthGauge score={data.health.score} status={data.health.status} />
          <div className="operationsRealtime">
            <div className="operationsPanelHeading">
              <div><Gauge aria-hidden="true" /><span>实时负载</span></div>
              <StatusBadge status={data.health.status} />
            </div>
            <div className="operationsRealtimeValues">
              <div><strong>{formatMetric(data.traffic.requests_per_second, 3)}</strong><span>QPS</span></div>
              <div><strong>{formatLatency(data.traffic.average_latency_ms)}</strong><span>平均响应</span></div>
            </div>
            <MetricPair label="进程运行时长" value={formatDuration(data.runtime.uptime_seconds)} />
          </div>
        </article>

        <article className="operationsPanel operationsKpiPanel">
          <div className="operationsPanelHeading">
            <div><Activity aria-hidden="true" /><span>请求</span></div>
          </div>
          <strong className="operationsKpiValue">{formatMetric(data.traffic.request_count)}</strong>
          <MetricPair label="客户端错误（4xx）" value={formatMetric(data.traffic.client_error_count)} />
          {data.traffic.client_error_details.length ? (
            <ul className="operationsClientErrorList" aria-label="客户端错误明细">
              {data.traffic.client_error_details.map((error) => (
                <li key={error.status_code}>
                  <code>{error.status_code}</code>
                  <span>{error.reason}</span>
                  <strong>{formatMetric(error.count)} 次</strong>
                </li>
              ))}
            </ul>
          ) : null}
          <MetricPair label="服务端错误（5xx）" value={formatMetric(data.traffic.server_error_count)} />
        </article>

        <article className={`operationsPanel operationsKpiPanel operationsTone--${data.traffic.error_rate_percent >= 2 ? "critical" : "healthy"}`}>
          <div className="operationsPanelHeading">
            <div><ShieldAlert aria-hidden="true" /><span>错误率</span></div>
          </div>
          <strong className="operationsKpiValue">{formatMetric(data.traffic.error_rate_percent, 2)}%</strong>
          <MetricPair label="统计口径" value="HTTP 5xx" />
          <MetricPair label="统计窗口" value={windowOptions.find((item) => item.value === windowMinutes)?.label ?? "自定义"} />
        </article>

        <article className="operationsPanel operationsLatencyPanel">
          <div className="operationsPanelHeading">
            <div><Clock3 aria-hidden="true" /><span>请求耗时</span></div>
          </div>
          <strong className="operationsKpiValue">{formatLatency(data.traffic.p99_latency_ms)} <small>P99</small></strong>
            <div className="operationsLatencyGrid">
            <MetricPair label="P95" value={formatLatency(data.traffic.p95_latency_ms)} />
            <MetricPair label="P50" value={formatLatency(data.traffic.p50_latency_ms)} />
            <MetricPair label="平均" value={formatLatency(data.traffic.average_latency_ms)} />
            <MetricPair label="最大" value={formatLatency(data.traffic.max_latency_ms)} />
            </div>
            <small className="operationsHelp">历史百分位耗时按分段统计估算</small>
        </article>
      </section>

      <section className="operationsDetailGrid" aria-label="请求趋势与热点">
        <article className="operationsPanel operationsTrafficPanel">
          <div className="operationsPanelHeading">
            <div><Activity aria-hidden="true" /><span>请求量趋势</span></div>
            <span className="operationsLegend"><i />请求量 <i />服务端错误</span>
          </div>
          <TrafficChart overview={data} />
        </article>
        <article className="operationsPanel operationsRoutesPanel">
          <div className="operationsPanelHeading">
            <div><ServerCog aria-hidden="true" /><span>请求热点</span></div>
          </div>
          {data.traffic.top_routes.length ? (
            <ol className="operationsRouteList">
              {data.traffic.top_routes.map((route) => (
                <li key={`${route.method}-${route.route}`}>
                  <code><b>{route.method}</b>{route.route}</code>
                  <span>{route.request_count} 次 · {formatLatency(route.average_latency_ms)}</span>
                </li>
              ))}
            </ol>
          ) : (
            <div className="operationsCompactEmpty">暂无热点接口</div>
          )}
        </article>
      </section>

      <section className="operationsServiceGrid" aria-label="基础设施状态">
        <article className={`operationsPanel operationsServicePanel operationsTone--${data.runtime.status}`}>
          <div className="operationsPanelHeading">
            <div><Cpu aria-hidden="true" /><span>CPU</span></div>
            <StatusBadge status={data.runtime.status} />
          </div>
          <strong>{formatMetric(data.runtime.cpu_percent, 1)}%</strong>
          <div className="operationsProgress"><i style={{ width: `${Math.min(100, data.runtime.cpu_percent)}%` }} /></div>
          <span>API 进程当前占用</span>
        </article>

        <article className={`operationsPanel operationsServicePanel operationsTone--${data.runtime.status}`}>
          <div className="operationsPanelHeading">
            <div><MemoryStick aria-hidden="true" /><span>内存</span></div>
            <StatusBadge status={data.runtime.status} />
          </div>
          <strong>{data.runtime.memory_percent === null ? "--" : `${formatMetric(data.runtime.memory_percent, 1)}%`}</strong>
          <div className="operationsProgress"><i style={{ width: `${Math.min(100, data.runtime.memory_percent ?? 0)}%` }} /></div>
          <span>{formatMetric(data.runtime.memory_used_mb, 1)} MB / {data.runtime.memory_limit_mb === null ? "上限未知" : `${formatMetric(data.runtime.memory_limit_mb, 0)} MB`}</span>
        </article>

        <article className={`operationsPanel operationsServicePanel operationsTone--${data.database.status}`}>
          <div className="operationsPanelHeading">
            <div><Database aria-hidden="true" /><span>PostgreSQL</span></div>
            <StatusBadge status={data.database.status} />
          </div>
          <strong>{data.database.total_connections ?? "--"}<small> / {data.database.max_connections ?? "--"} 连接</small></strong>
          <div className="operationsProgress"><i style={{ width: `${Math.min(100, data.database.utilization_percent ?? 0)}%` }} /></div>
          <span>活跃 {data.database.active_connections ?? "--"} · 响应 {formatLatency(data.database.response_time_ms)}</span>
        </article>

        <article className={`operationsPanel operationsServicePanel operationsTone--${background.status}`}>
          <div className="operationsPanelHeading">
            <div><TimerReset aria-hidden="true" /><span>后台任务</span></div>
            <StatusBadge status={background.status} />
          </div>
          <strong>{background.queued_tasks + background.processing_tasks}<small> 个执行中</small></strong>
          <div className="operationsTaskFacts">
            <span>待审核 {background.pending_review_tasks}</span>
            <span>失败 {background.failed_tasks}</span>
            <span>需处理 {background.needs_attention_tasks}</span>
          </div>
          <span>调度器：{statusLabels[background.scheduler_status]}{background.scheduler_lag_seconds === null ? "" : ` · ${background.scheduler_lag_seconds} 秒前`}</span>
        </article>
      </section>
      <OperationsHistoryPanel autoRefresh={autoRefresh} />
      <OperationsRequestErrorsPanel />
    </main>
  )
}
