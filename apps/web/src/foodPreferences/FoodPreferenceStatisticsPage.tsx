import { useMutation, useQuery } from "@tanstack/react-query"
import { useMemo, useRef, useState, type ReactElement } from "react"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import {
  exportFoodPreferences,
  getFoodPreferenceStatistics,
  type FoodExportFormat,
  type FoodPopulation,
  type FoodPreferenceStatistics,
} from "./api"
import { foodPreferenceKeys } from "./keys"
import "./food-preferences.css"

const PAGE_SIZE = 20

function messageFor(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

function dateTime(value: string | null): string {
  if (!value) return "未填写"
  const parsed = new Date(value)
  if (Number.isNaN(parsed.valueOf())) return value
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(parsed)
}

function statusLabel(status: FoodPreferenceStatistics["people"]["items"][number]["status"]): string {
  return {
    active: "有效",
    draft: "待完善",
    inactive: "停用",
    deleted: "已删除",
  }[status]
}

function typeLabel(type: FoodPreferenceStatistics["people"]["items"][number]["person_types"][number]): string {
  return {
    student: "学员",
    guardian: "监护人",
    staff: "工作人员",
    visitor: "访客",
  }[type]
}

function rankList(label: string, rows: FoodPreferenceStatistics["cuisines"]): ReactElement {
  return (
    <section className="foodPreferenceStatistics__rank" aria-label={label}>
      <header><h2>{label}</h2><span>{rows.length} 项</span></header>
      {rows.length === 0 ? <p className="foodPreferenceStatistics__muted">暂无填报数据</p> : (
        <ol>
          {rows.map((row) => (
            <li key={row.label}>
              <div><strong>{row.label}</strong><span>{row.count} 人 · {percent(row.ratio)}</span></div>
              <span className="foodPreferenceStatistics__bar" aria-hidden="true"><i style={{ width: `${Math.max(0, Math.min(100, row.ratio * 100))}%` }} /></span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

type ExportDialogProps = {
  population: FoodPopulation
  includeInactive: boolean
  pending: boolean
  onClose: () => void
  onSubmit: (purpose: string, format: FoodExportFormat) => void
}

function ExportDialog({ population, includeInactive, pending, onClose, onSubmit }: ExportDialogProps) {
  const [purpose, setPurpose] = useState("")
  const [format, setFormat] = useState<FoodExportFormat>("xlsx")
  const valid = purpose.trim().length >= 4 && purpose.trim().length <= 200
  return (
    <div className="foodPreferenceStatistics__backdrop" role="presentation">
      <section className="foodPreferenceStatistics__dialog" role="dialog" aria-modal="true" aria-labelledby="food-export-title">
        <header><div><p className="pageEyebrow">EXPORT</p><h2 id="food-export-title">导出餐饮偏好统计</h2></div><button type="button" aria-label="关闭导出窗口" onClick={onClose} disabled={pending}>×</button></header>
        <p>导出当前统计范围的人员偏好明细，请填写用途以留下审计记录。</p>
        <label>导出用途<input aria-label="导出用途" value={purpose} maxLength={200} onChange={(event) => setPurpose(event.target.value)} /></label>
        <label>文件格式<select aria-label="文件格式" value={format} onChange={(event) => setFormat(event.target.value as FoodExportFormat)}><option value="xlsx">Excel（XLSX）</option><option value="csv_zip">CSV 压缩包</option></select></label>
        <p className="foodPreferenceStatistics__muted">范围：{population === "students" ? "仅学员" : "所有人员"}{includeInactive ? "（含停用或历史人员）" : ""}</p>
        <footer><button type="button" onClick={onClose} disabled={pending}>取消</button><button type="button" className="foodPreferenceStatistics__primary" disabled={!valid || pending} onClick={() => onSubmit(purpose.trim(), format)}>{pending ? "导出中…" : "确认导出"}</button></footer>
      </section>
    </div>
  )
}

export function FoodPreferenceStatisticsPage() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const headingRef = useRef<HTMLHeadingElement>(null)
  const [population, setPopulation] = useState<FoodPopulation>("students")
  const [includeInactive, setIncludeInactive] = useState(false)
  const [page, setPage] = useState(1)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const [exportedFilename, setExportedFilename] = useState<string | null>(null)
  // The API's revision is returned with each snapshot; using a stable pending
  // root keeps the initial query deterministic while the response is loading.
  const pendingRevision = "current"
  const filters = useMemo(() => ({ population, include_inactive: includeInactive, page, page_size: PAGE_SIZE }), [population, includeInactive, page])
  const statistics = useQuery({
    queryKey: foodPreferenceKeys.statistics(accountId, pendingRevision, filters),
    queryFn: ({ signal }) => getFoodPreferenceStatistics(filters, signal),
    enabled: Boolean(user?.account_id),
    retry: false,
  })
  const exportMutation = useMutation({ mutationFn: ({ purpose, format }: { purpose: string; format: FoodExportFormat }) => exportFoodPreferences({ population, include_inactive: includeInactive, format, purpose }) })
  const data = statistics.data
  const totalPages = data ? Math.max(1, Math.ceil(data.people.total / data.people.page_size)) : 1

  async function submitExport(purpose: string, format: FoodExportFormat): Promise<void> {
    setExportError(null)
    try {
      const result = await exportMutation.mutateAsync({ purpose, format })
      const url = URL.createObjectURL(result.blob)
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = result.filename
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
      setDialogOpen(false)
      setExportedFilename(result.filename)
      setNotice(`已下载 ${result.filename}`)
    } catch (error) {
      setExportError(messageFor(error, "导出暂时失败，请稍后重试"))
    }
  }

  function changePopulation(value: FoodPopulation): void {
    setPopulation(value)
    setPage(1)
  }

  function changeInactive(value: boolean): void {
    setIncludeInactive(value)
    setPage(1)
  }

  return (
    <main className="foodPreferenceStatisticsPage">
      <header className="foodPreferenceStatistics__header">
        <div><p className="pageEyebrow">人员资料 · 敏感权限</p><h1 ref={headingRef} tabIndex={-1}>餐饮偏好统计</h1><p>按人员范围查看菜系、菜品填报情况与明细。</p></div>
        <div className="foodPreferenceStatistics__actions"><button type="button" className="foodPreferenceStatistics__primary" onClick={() => { setExportError(null); setDialogOpen(true) }}>导出统计</button><button type="button" aria-label="刷新餐饮偏好统计" disabled={statistics.isFetching} onClick={() => void statistics.refetch()}>刷新</button></div>
      </header>

      <form className="foodPreferenceStatistics__filters" aria-label="统计筛选" onSubmit={(event) => event.preventDefault()}>
        <label>统计范围<select aria-label="统计范围" value={population} onChange={(event) => changePopulation(event.target.value as FoodPopulation)}><option value="students">仅学员</option><option value="all_people">所有人员</option></select></label>
        <label className="foodPreferenceStatistics__check"><input type="checkbox" aria-label="包含停用或历史人员" checked={includeInactive} onChange={(event) => changeInactive(event.target.checked)} />包含停用或历史人员</label>
      </form>

      {notice ? <p className="foodPreferenceStatistics__notice" role="status">{notice}</p> : null}
      {exportError ? <p className="foodPreferenceStatistics__error" role="alert">{exportError}</p> : null}
      {statistics.isPending ? <section className="foodPreferenceStatistics__state"><p>正在读取餐饮偏好统计…</p></section> : null}
      {statistics.isError ? <section className="foodPreferenceStatistics__state foodPreferenceStatistics__state--error" role="alert"><strong>暂时无法读取餐饮偏好统计</strong><p>{messageFor(statistics.error, "请稍后重试")}</p><button type="button" onClick={() => void statistics.refetch()}>重试</button></section> : null}

      {data ? <>
        <section className="foodPreferenceStatistics__summary" aria-label="餐饮偏好汇总"><article><span>总人数</span><strong>{data.total_people}</strong></article><article><span>填报人数</span><strong>{data.filled_people}</strong></article><article><span>未填报人数</span><strong>{data.unfilled_people}</strong></article><article><span>填报率</span><strong>{percent(data.filled_ratio)}</strong></article></section>
        <div className="foodPreferenceStatistics__ranks">{rankList("菜系偏好排行", data.cuisines)}{rankList("菜品偏好排行", data.dishes)}</div>
        {data.people.items.length === 0 ? <section className="foodPreferenceStatistics__state"><h2>暂无人员明细</h2><p>当前范围没有可显示的人员。</p></section> : <>
          <div className="foodPreferenceStatistics__tableWrap"><table className="foodPreferenceStatistics__table" aria-label="人员餐饮偏好明细"><thead><tr><th>人员</th><th>类型</th><th>状态</th><th>喜欢的菜系</th><th>最喜欢的菜</th><th>最近更新</th></tr></thead><tbody>{data.people.items.map((person) => <tr key={person.person_id}><td><strong>{person.full_name}</strong></td><td>{person.person_types.map(typeLabel).join("、")}</td><td>{statusLabel(person.status)}</td><td>{person.cuisines.join("、") || "未填写"}</td><td>{person.dishes.join("、") || "未填写"}</td><td>{dateTime(person.updated_at)}</td></tr>)}</tbody></table></div>
          <section className="foodPreferenceStatistics__cards" aria-label="人员餐饮偏好卡片">{data.people.items.map((person) => <article key={person.person_id}><header><h2>{person.full_name}</h2><span>{statusLabel(person.status)}</span></header><dl><div><dt>类型</dt><dd>{person.person_types.map(typeLabel).join("、")}</dd></div><div><dt>喜欢的菜系</dt><dd>{person.cuisines.join("、") || "未填写"}</dd></div><div><dt>最喜欢的菜</dt><dd>{person.dishes.join("、") || "未填写"}</dd></div><div><dt>最近更新</dt><dd>{dateTime(person.updated_at)}</dd></div></dl></article>)}</section>
        </>}
        {data.people.total > data.people.page_size ? <nav className="foodPreferenceStatistics__pagination" aria-label="人员明细分页"><button type="button" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>上一页</button><span>{page} / {totalPages}</span><button type="button" disabled={page >= totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>下一页</button></nav> : null}
      </> : null}

      {dialogOpen ? <ExportDialog population={population} includeInactive={includeInactive} pending={exportMutation.isPending} onClose={() => { if (!exportMutation.isPending) { setDialogOpen(false); setExportError(null) } }} onSubmit={(purpose, format) => void submitExport(purpose, format)} /> : null}
      {exportedFilename ? <span className="foodPreferenceStatistics__srOnly">{exportedFilename}</span> : null}
    </main>
  )
}
