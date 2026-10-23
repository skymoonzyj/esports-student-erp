import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Download, FilePlus2, Filter, RefreshCw } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { formatShanghaiDate } from "../teaching/datetime"
import { dateInputToShanghaiEndExclusiveIso, dateInputToShanghaiIso, isoToDateInput } from "../shared/date"
import {
  GenerateHousingReportDialog,
  HousingReportDetailDialog,
  MarkHousingReportSubmittedDialog,
  VoidHousingReportDialog,
} from "./HousingReportDialogs"
import {
  downloadHousingReport,
  generateHousingReport,
  getHousingReport,
  listHousingReports,
  markHousingReportSubmitted,
  saveDownloadedBlob,
  voidHousingReport,
} from "./reportingApi"
import type {
  GenerateHousingReportInput,
  HousingReportBatchStatus,
  HousingReportBatchSummary,
  HousingReportFormat,
  HousingReportListFilters,
  HousingReportListResponse,
  MarkHousingReportSubmittedInput,
  VoidHousingReportInput,
} from "./reportingApi"
import { housingReportInvalidationKeys, reportingKeys } from "./reportingKeys"

import "./housing.css"

const PAGE_SIZE = 20
const STATUS_LABELS: Record<HousingReportBatchStatus, string> = {
  generated: "待人工报送",
  marked_submitted: "已报送",
  voided: "已作废",
  failed: "生成失败",
}

type DialogState =
  | { type: "generate"; initial?: Partial<GenerateHousingReportInput> }
  | { type: "submitted"; batch: HousingReportBatchSummary }
  | { type: "void"; batch: HousingReportBatchSummary }
  | { type: "detail"; batch: HousingReportBatchSummary }

function useMobile(): boolean {
  const [mobile, setMobile] = useState(() => window.innerWidth < 720)
  useEffect(() => {
    const update = () => setMobile(window.innerWidth < 720)
    window.addEventListener("resize", update)
    return () => window.removeEventListener("resize", update)
  }, [])
  return mobile
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function scopeLabel(batch: HousingReportBatchSummary): string {
  const labels = batch.scopes.map((scope) => `${scope.building_name} · ${scope.floor_name}`)
  if (labels.length <= 2) return labels.join("、") || "无范围"
  return `${labels.slice(0, 2).join("、")} 等 ${labels.length} 个楼层`
}

function correctionInitial(batch: HousingReportBatchSummary): Partial<GenerateHousingReportInput> {
  return {
    range_start: batch.range_start,
    range_end: batch.range_end,
    scope_mode: "floors",
    building_ids: [],
    floor_ids: batch.scopes.map((scope) => scope.floor_id),
    cohort_ids: batch.cohort_filter,
    person_types: batch.person_type_filter,
    purpose: batch.purpose,
    corrects_batch_id: batch.id,
  }
}

function ReportActions({
  batch,
  busy,
  onDownload,
  onDialog,
}: {
  batch: HousingReportBatchSummary
  busy: boolean
  onDownload: (batch: HousingReportBatchSummary, format: HousingReportFormat) => void
  onDialog: (dialog: DialogState) => void
}) {
  return (
    <div className="housingReportActions" data-housing-dialog-owner="true" tabIndex={-1}>
      {batch.allowed_actions.download ? (
        <>
          <button type="button" disabled={busy} aria-label={`下载 ${batch.batch_number} Excel`} onClick={() => onDownload(batch, "xlsx")}><Download aria-hidden="true" />Excel</button>
          <button type="button" disabled={busy} aria-label={`下载 ${batch.batch_number} CSV`} onClick={() => onDownload(batch, "csv")}><Download aria-hidden="true" />CSV</button>
        </>
      ) : null}
      {batch.allowed_actions.mark_submitted ? <button type="button" disabled={busy} aria-label={`标记 ${batch.batch_number} 已报送`} onClick={() => onDialog({ type: "submitted", batch })}>标记报送</button> : null}
      {batch.allowed_actions.void ? <button type="button" disabled={busy} aria-label={`作废 ${batch.batch_number}`} onClick={() => onDialog({ type: "void", batch })}>作废</button> : null}
      {batch.allowed_actions.correct ? <button type="button" disabled={busy} aria-label={`更正 ${batch.batch_number}`} onClick={() => onDialog({ type: "generate", initial: correctionInitial(batch) })}>创建更正</button> : null}
      <button type="button" disabled={busy} aria-label={`查看 ${batch.batch_number} 详情`} onClick={() => onDialog({ type: "detail", batch })}>详情</button>
    </div>
  )
}

function ReportFilters({
  value,
  onApply,
}: {
  value: HousingReportListFilters
  onApply: (value: HousingReportListFilters) => void
}) {
  const [statuses, setStatuses] = useState(value.statuses ?? [])
  const [rangeStart, setRangeStart] = useState(value.rangeStart ? isoToDateInput(value.rangeStart) : "")
  const [rangeEnd, setRangeEnd] = useState(value.rangeEnd ? isoToDateInput(value.rangeEnd) : "")
  const availableStatuses = Object.entries(STATUS_LABELS) as Array<[HousingReportBatchStatus, string]>
  return (
    <form className="housingReportFilters" onSubmit={(event) => {
      event.preventDefault()
      onApply({
        statuses,
        rangeStart: rangeStart ? dateInputToShanghaiIso(rangeStart) : undefined,
        rangeEnd: rangeEnd ? dateInputToShanghaiEndExclusiveIso(rangeEnd) : undefined,
        page: 1,
        pageSize: PAGE_SIZE,
      })
    }}>
      <label>入住开始<input type="date" value={rangeStart} onChange={(event) => setRangeStart(event.target.value)} /></label>
      <label>入住结束<input type="date" value={rangeEnd} onChange={(event) => setRangeEnd(event.target.value)} /></label>
      <fieldset><legend>批次状态</legend>{availableStatuses.map(([status, label]) => (
        <label key={status}><input type="checkbox" checked={statuses.includes(status)} onChange={(event) => setStatuses(event.target.checked ? [...statuses, status] : statuses.filter((item) => item !== status))} />{label}</label>
      ))}</fieldset>
      <button type="submit">应用筛选</button>
    </form>
  )
}

export function HousingReportsPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const mobile = useMobile()
  const accountId = user?.account_id ?? "anonymous"
  const pageFocusRef = useRef<HTMLHeadingElement>(null)
  const [filters, setFilters] = useState<HousingReportListFilters>({ page: 1, pageSize: PAGE_SIZE })
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [dialog, setDialog] = useState<DialogState | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [downloadBusy, setDownloadBusy] = useState(false)
  const pendingRevision = "current"
  const listKey = reportingKeys.list(accountId, pendingRevision, filters)
  const reports = useQuery({
    queryKey: listKey,
    queryFn: () => listHousingReports(filters),
    enabled: Boolean(user?.account_id),
  })
  const accessRevision = reports.data?.access_revision ?? pendingRevision
  const details = useQuery({
    queryKey: reportingKeys.detail(
      accountId,
      accessRevision,
      dialog?.type === "detail" ? dialog.batch.id : "closed",
    ),
    queryFn: () => getHousingReport(
      dialog?.type === "detail" ? dialog.batch.id : "closed",
    ),
    enabled: dialog?.type === "detail",
  })
  const generateMutation = useMutation({ mutationFn: generateHousingReport })
  const submittedMutation = useMutation({
    mutationFn: ({ batchId, input }: { batchId: string; input: MarkHousingReportSubmittedInput }) =>
      markHousingReportSubmitted(batchId, input),
  })
  const voidMutation = useMutation({
    mutationFn: ({ batchId, input }: { batchId: string; input: VoidHousingReportInput }) =>
      voidHousingReport(batchId, input),
  })
  const mutationPending = generateMutation.isPending || submittedMutation.isPending || voidMutation.isPending
  const summary = reports.data?.status_counts ?? {
    generated: 0,
    marked_submitted: 0,
    voided: 0,
    failed: 0,
  }
  const totalPages = reports.data ? Math.max(1, Math.ceil(reports.data.total / reports.data.page_size)) : 1
  const currentPage = filters.page ?? 1
  const problemErrors = actionError instanceof ProblemDetailsError
    ? actionError.problem.errors
    : undefined

  async function refreshMutation(batchIds: readonly string[] = []): Promise<void> {
    const [listKey, ...detailKeys] = housingReportInvalidationKeys(
      accountId,
      pendingRevision,
      accessRevision,
      batchIds,
    )
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: listKey }),
      ...detailKeys.map((queryKey) => queryClient.invalidateQueries({
        queryKey,
        exact: true,
      })),
    ])
  }

  async function submitGenerate(input: GenerateHousingReportInput): Promise<void> {
    setActionError(null)
    try {
      const result = await generateMutation.mutateAsync(input)
      setDialog(null)
      setNotice(`已创建导出批次 ${result.batch_number}，可下载 Excel 或 CSV`)
      await refreshMutation([
        result.id,
        ...(input.corrects_batch_id ? [input.corrects_batch_id] : []),
      ])
    } catch (error) {
      setActionError(error)
      await refreshMutation()
    }
  }

  async function submitMarked(input: MarkHousingReportSubmittedInput): Promise<void> {
    if (dialog?.type !== "submitted") return
    setActionError(null)
    try {
      const result = await submittedMutation.mutateAsync({ batchId: dialog.batch.id, input })
      reconcileBatch(result)
      setDialog(null)
      setNotice(`批次 ${dialog.batch.batch_number} 已标记报送`)
      await refreshMutation([dialog.batch.id])
    } catch (error) {
      setActionError(error)
    }
  }

  async function submitVoid(input: VoidHousingReportInput): Promise<void> {
    if (dialog?.type !== "void") return
    setActionError(null)
    try {
      const result = await voidMutation.mutateAsync({ batchId: dialog.batch.id, input })
      reconcileBatch(result)
      setDialog(null)
      setNotice(`批次 ${dialog.batch.batch_number} 已作废`)
      await refreshMutation([dialog.batch.id])
    } catch (error) {
      setActionError(error)
    }
  }

  function reconcileBatch(result: HousingReportBatchSummary): void {
    // Publish the committed version before another dialog can capture this row.
    // Keep the normal refresh for filters, counts, and authorization changes.
    queryClient.setQueryData<HousingReportListResponse>(listKey, (current) => current ? {
      ...current,
      items: current.items.map((batch) => (
        batch.id === result.id && batch.version <= result.version ? result : batch
      )),
    } : current)
  }

  async function download(batch: HousingReportBatchSummary, format: HousingReportFormat): Promise<void> {
    setDownloadBusy(true)
    setActionError(null)
    try {
      const result = await downloadHousingReport(batch.id, format)
      saveDownloadedBlob(result.blob, result.filename)
      setNotice(`已下载 ${batch.batch_number}`)
    } catch (error) {
      setActionError(error)
    } finally {
      setDownloadBusy(false)
    }
  }

  function openDialog(next: DialogState): void {
    setActionError(null)
    setDialog(next)
  }

  const tableRows = useMemo(() => reports.data?.items ?? [], [reports.data?.items])
  return (
    <main className="housingReportsPage">
      <header className="housingReportsHeader">
        <div><p className="pageEyebrow">房态运营 · 敏感权限</p><h1 ref={pageFocusRef} tabIndex={-1}>实名入住报送</h1><p>按实际入住时段导出不可变实名名单，并记录人工报送与更正链。</p></div>
        <div className="housingReportsHeader__actions">
          {mobile ? <button type="button" onClick={() => setFiltersOpen((open) => !open)}><Filter aria-hidden="true" />筛选</button> : null}
          <button type="button" className="housingReportsPrimary" onClick={() => openDialog({ type: "generate" })}><FilePlus2 aria-hidden="true" />导出实名名单</button>
          <button type="button" aria-label="刷新实名批次" disabled={reports.isFetching} onClick={() => void reports.refetch()}><RefreshCw aria-hidden="true" /></button>
        </div>
      </header>

      {(!mobile || filtersOpen) ? <ReportFilters value={filters} onApply={(next) => { setFilters(next); setFiltersOpen(false) }} /> : null}

      <section className="housingReportSummary" aria-label="实名报送汇总">
        <article><span>批次总数</span><strong>{reports.data?.total ?? 0}</strong></article>
        <article><span>待人工报送</span><strong>{summary.generated}</strong></article>
        <article><span>已报送</span><strong>{summary.marked_submitted}</strong></article>
        <article><span>已作废</span><strong>{summary.voided}</strong></article>
      </section>

      {notice ? <p className="housingReportNotice" role="status">{notice}</p> : null}
      {actionError && dialog === null ? <p className="housingDialog__error" role="alert">{errorMessage(actionError, "操作暂时失败，请稍后重试")}</p> : null}
      {reports.isPending ? <section className="housingState"><span className="housingState__pulse" /><p>正在读取实名报送批次…</p></section> : null}
      {reports.isError ? <section className="housingState housingState--error" role="alert"><strong>{errorMessage(reports.error, "暂时无法读取实名报送批次")}</strong><button type="button" onClick={() => void reports.refetch()}>重试</button></section> : null}
      {reports.data && tableRows.length === 0 ? <section className="housingState"><h2>暂无实名报送批次</h2><p>填写范围与用途后生成第一份实名名单。</p></section> : null}

      {reports.data && tableRows.length > 0 && !mobile ? (
        <div className="housingReportTableWrap"><table className="housingReportTable" aria-label="实名报送批次"><thead><tr><th>批次编号</th><th>创建时间</th><th>范围</th><th>用途</th><th>人数 / 住宿</th><th>状态</th><th>操作</th></tr></thead><tbody>{tableRows.map((batch) => (
          <tr key={batch.id}><td><strong>{batch.batch_number}</strong></td><td>{formatShanghaiDate(batch.created_at)}</td><td>{scopeLabel(batch)}</td><td>{batch.purpose}</td><td>{batch.resident_count} 人 / {batch.stay_row_count} 条</td><td><span className={`housingReportStatus housingReportStatus--${batch.status}`}>{STATUS_LABELS[batch.status]}</span></td><td><ReportActions batch={batch} busy={downloadBusy || mutationPending} onDownload={(target, format) => void download(target, format)} onDialog={openDialog} /></td></tr>
        ))}</tbody></table></div>
      ) : null}

      {reports.data && tableRows.length > 0 && mobile ? (
        <section className="housingReportCards" aria-label="实名报送批次卡片">{tableRows.map((batch) => (
          <article className="housingReportCard" key={batch.id} aria-label={`实名批次 ${batch.batch_number}`}>
            <header><div><span>{formatShanghaiDate(batch.created_at)}</span><h2>{batch.batch_number}</h2></div><span className={`housingReportStatus housingReportStatus--${batch.status}`}>{STATUS_LABELS[batch.status]}</span></header>
            <dl><div><dt>范围</dt><dd>{scopeLabel(batch)}</dd></div><div><dt>用途</dt><dd>{batch.purpose}</dd></div><div><dt>统计</dt><dd>{batch.resident_count} 人 / {batch.stay_row_count} 条住宿</dd></div></dl>
            <ReportActions batch={batch} busy={downloadBusy || mutationPending} onDownload={(target, format) => void download(target, format)} onDialog={openDialog} />
          </article>
        ))}</section>
      ) : null}

      {reports.data && reports.data.total > reports.data.page_size ? <nav className="housingPagination" aria-label="实名批次分页"><button type="button" disabled={currentPage <= 1} onClick={() => setFilters((current) => ({ ...current, page: currentPage - 1 }))}>上一页</button><span>{currentPage} / {totalPages}</span><button type="button" disabled={currentPage >= totalPages} onClick={() => setFilters((current) => ({ ...current, page: currentPage + 1 }))}>下一页</button></nav> : null}

      {dialog?.type === "generate" && reports.data ? <GenerateHousingReportDialog options={reports.data.filter_options} initial={dialog.initial} pending={generateMutation.isPending} error={actionError ? errorMessage(actionError, "导出失败") : null} problemErrors={problemErrors} focusFallbackRef={pageFocusRef} onClose={() => setDialog(null)} onSubmit={submitGenerate} /> : null}
      {dialog?.type === "submitted" ? <MarkHousingReportSubmittedDialog batchNumber={dialog.batch.batch_number} version={dialog.batch.version} pending={submittedMutation.isPending} error={actionError ? errorMessage(actionError, "标记报送失败") : null} focusFallbackRef={pageFocusRef} onClose={() => setDialog(null)} onSubmit={submitMarked} /> : null}
      {dialog?.type === "void" ? <VoidHousingReportDialog batchNumber={dialog.batch.batch_number} version={dialog.batch.version} pending={voidMutation.isPending} error={actionError ? errorMessage(actionError, "作废失败") : null} focusFallbackRef={pageFocusRef} onClose={() => setDialog(null)} onSubmit={submitVoid} /> : null}
      {dialog?.type === "detail" ? <HousingReportDetailDialog detail={details.data ?? null} pending={details.isPending} error={details.isError ? errorMessage(details.error, "详情读取失败") : null} focusFallbackRef={pageFocusRef} onClose={() => setDialog(null)} /> : null}
    </main>
  )
}
