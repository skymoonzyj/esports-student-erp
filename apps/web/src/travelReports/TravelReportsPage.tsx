import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileArchive,
  FileSpreadsheet,
  LoaderCircle,
  PlaneTakeoff,
  RotateCcw,
  Upload,
} from "lucide-react"
import { useMemo, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { formatAuditDateTime } from "../shared/date"
import {
  downloadTravelReport,
  importTravelReport,
  listTravelReports,
  travelReportKeys,
} from "./api"
import type { TravelReport } from "./api"
import "./travel-reports.css"

const MAX_UPLOAD_BYTES = 20 * 1024 * 1024
const PAGE_SIZE = 30

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ProblemDetailsError) return error.problem.detail
  if (error instanceof Error) return error.message
  return fallback
}

function formatAmount(value: string): string {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return "--"
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount)
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function periodLabel(report: TravelReport): string {
  return `${report.billing_period_start} 至 ${report.billing_period_end}`
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

export function TravelReportsPage({
  canImport,
  canExport,
}: {
  canImport: boolean
  canExport: boolean
}) {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [page, setPage] = useState(1)
  const [message, setMessage] = useState<string | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState<string | null>(null)
  const [billingPeriod, setBillingPeriod] = useState("")
  const [costCenter, setCostCenter] = useState("")

  const [billingPeriodStart, billingPeriodEnd] = billingPeriod.split("|")
  const filters = useMemo(() => ({
    ...(billingPeriodStart && billingPeriodEnd
      ? { billingPeriodStart, billingPeriodEnd }
      : {}),
    ...(costCenter ? { costCenter } : {}),
  }), [billingPeriodEnd, billingPeriodStart, costCenter])
  const hasFilters = billingPeriod !== "" || costCenter !== ""

  const reports = useQuery({
    queryKey: travelReportKeys.page(page, filters),
    queryFn: () => listTravelReports(page, PAGE_SIZE, filters),
    placeholderData: (previousData) => previousData,
  })
  const upload = useMutation({
    mutationFn: importTravelReport,
    onSuccess: async (report) => {
      setLocalError(null)
      setMessage(
        report.warnings.length > 0
          ? `转换完成，发现 ${report.warnings.length} 项需核对内容`
          : "转换完成，账单金额核对一致",
      )
      setPage(1)
      await queryClient.invalidateQueries({ queryKey: travelReportKeys.all })
    },
    onError: (error) => {
      setMessage(null)
      setLocalError(errorMessage(error, "账单转换失败，请稍后重试"))
    },
  })

  const latest = reports.data?.items[0]
  const pageWarnings = useMemo(
    () => reports.data?.items.reduce((total, report) => total + report.warnings.length, 0) ?? 0,
    [reports.data?.items],
  )
  const totalPages = Math.max(1, Math.ceil((reports.data?.total ?? 0) / PAGE_SIZE))

  function chooseFile(): void {
    fileInput.current?.click()
  }

  function selectedFile(file: File | undefined): void {
    if (!file) return
    setMessage(null)
    setLocalError(null)
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setLocalError("仅支持扩展名为 .xlsx 的飞巴商旅账单")
      return
    }
    if (file.size === 0 || file.size > MAX_UPLOAD_BYTES) {
      setLocalError("账单文件必须大于 0，且不能超过 20 MB")
      return
    }
    upload.mutate(file)
    if (fileInput.current) fileInput.current.value = ""
  }

  async function download(report: TravelReport, kind: "source" | "converted"): Promise<void> {
    const key = `${report.id}:${kind}`
    setDownloading(key)
    setLocalError(null)
    try {
      const fallback = kind === "source" ? report.original_filename : report.converted_filename
      const result = await downloadTravelReport(report.id, kind, fallback)
      saveBlob(result.blob, result.filename)
    } catch (error) {
      setLocalError(errorMessage(error, "文件下载失败，请稍后重试"))
    } finally {
      setDownloading(null)
    }
  }

  return (
    <main className="travelReportsPage">
      <header className="travelReportsHeader">
        <div>
          <h1><PlaneTakeoff aria-hidden="true" />订票报表转换</h1>
          <p>飞巴商旅账单</p>
        </div>
        {canImport ? (
          <>
            <input
              ref={fileInput}
              className="travelReportsFileInput"
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(event) => selectedFile(event.target.files?.[0])}
              aria-label="选择飞巴商旅账单"
            />
            <button
              className="travelReportsPrimaryButton"
              type="button"
              disabled={upload.isPending}
              onClick={chooseFile}
            >
              {upload.isPending
                ? <LoaderCircle className="isSpinning" aria-hidden="true" />
                : <Upload aria-hidden="true" />}
              {upload.isPending ? "正在转换" : "上传账单"}
            </button>
          </>
        ) : null}
      </header>

      <section className="travelReportsSummary" aria-label="订票报表概况">
        <div><span>{hasFilters ? "筛选结果" : "历史导入"}</span><strong>{reports.data?.total ?? "--"}</strong><small>份账单</small></div>
        <div><span>最近账期</span><strong>{latest ? periodLabel(latest) : "--"}</strong><small>{latest?.cost_center ?? "暂无记录"}</small></div>
        <div><span>最近账单金额</span><strong>{latest ? formatAmount(latest.source_total_amount) : "--"}</strong><small>{latest?.company_name ?? "暂无记录"}</small></div>
        <div className={pageWarnings > 0 ? "hasWarnings" : undefined}>
          <span>本页待核对</span><strong>{pageWarnings}</strong><small>项警告</small>
        </div>
      </section>

      {message ? (
        <div className="travelReportsNotice" role="status">
          <CheckCircle2 aria-hidden="true" />{message}
        </div>
      ) : null}
      {localError ? (
        <div className="travelReportsNotice travelReportsNotice--error" role="alert">
          <AlertTriangle aria-hidden="true" />{localError}
        </div>
      ) : null}

      <section className="travelReportsHistory">
        <div className="travelReportsSectionHeading">
          <div>
            <h2>导入历史</h2>
            <span>第 {page} / {totalPages} 页</span>
          </div>
        </div>

        <div className="travelReportsFilters" aria-label="筛选订票报表">
          <label>
            <span>账期</span>
            <select
              aria-label="账期"
              value={billingPeriod}
              onChange={(event) => {
                setBillingPeriod(event.currentTarget.value)
                setPage(1)
              }}
            >
              <option value="">全部账期</option>
              {reports.data?.filter_options.billing_periods.map((period) => {
                const value = `${period.billing_period_start}|${period.billing_period_end}`
                return (
                  <option key={value} value={value}>
                    {period.billing_period_start} 至 {period.billing_period_end}
                  </option>
                )
              })}
            </select>
          </label>
          <label>
            <span>成本中心</span>
            <select
              aria-label="成本中心"
              value={costCenter}
              onChange={(event) => {
                setCostCenter(event.currentTarget.value)
                setPage(1)
              }}
            >
              <option value="">全部成本中心</option>
              {reports.data?.filter_options.cost_centers.map((center) => (
                <option key={center} value={center}>{center}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={!hasFilters}
            title="清除全部筛选条件"
            onClick={() => {
              setBillingPeriod("")
              setCostCenter("")
              setPage(1)
            }}
          >
            <RotateCcw aria-hidden="true" />重置筛选
          </button>
          <small>{reports.isFetching ? "正在筛选..." : `共 ${reports.data?.total ?? 0} 份`}</small>
        </div>

        {reports.isPending ? <div className="travelReportsState">正在读取导入记录...</div> : null}
        {reports.isError ? (
          <div className="travelReportsState travelReportsState--error">
            {errorMessage(reports.error, "暂时无法读取导入记录")}
            <button type="button" onClick={() => void reports.refetch()}>重试</button>
          </div>
        ) : null}
        {reports.data?.items.length === 0 ? (
          <div className="travelReportsState">
            <FileSpreadsheet aria-hidden="true" />
            <strong>{hasFilters ? "没有符合筛选条件的订票报表" : "暂无订票报表"}</strong>
          </div>
        ) : null}

        {reports.data?.items.length ? (
          <div className="travelReportsTableWrap">
            <table className="travelReportsTable">
              <thead>
                <tr>
                  <th>账期 / 成本中心</th>
                  <th>公司</th>
                  <th>转换明细</th>
                  <th>金额核对</th>
                  <th>导入信息</th>
                  <th>状态</th>
                  <th>文件</th>
                </tr>
              </thead>
              <tbody>
                {reports.data.items.map((report) => (
                  <tr key={report.id}>
                    <td>
                      <strong>{periodLabel(report)}</strong>
                      <small>{report.cost_center}</small>
                    </td>
                    <td><span>{report.company_name}</span></td>
                    <td>
                      <span>国内机票 {report.domestic_air_count}</span>
                      <span>国际机票 {report.international_air_count}</span>
                      <span>火车 {report.train_count}</span>
                    </td>
                    <td>
                      <strong>{formatAmount(report.source_total_amount)}</strong>
                      <small>转换 {formatAmount(report.converted_total_amount)}</small>
                    </td>
                    <td>
                      <span>{report.imported_by_name}</span>
                      <small>{formatAuditDateTime(report.created_at)}</small>
                      <small>原文件 {formatBytes(report.source_byte_size)}</small>
                    </td>
                    <td>
                      {report.warnings.length === 0 ? (
                        <span className="travelReportsStatus travelReportsStatus--ok">
                          <CheckCircle2 aria-hidden="true" />金额一致
                        </span>
                      ) : (
                        <details className="travelReportsWarnings">
                          <summary>
                            <AlertTriangle aria-hidden="true" />{report.warnings.length} 项待核对
                          </summary>
                          <ul>
                            {report.warnings.map((warning) => <li key={warning}>{warning}</li>)}
                          </ul>
                        </details>
                      )}
                    </td>
                    <td>
                      {canExport ? (
                        <div className="travelReportsActions">
                          <button
                            type="button"
                            disabled={downloading !== null}
                            onClick={() => void download(report, "converted")}
                          >
                            {downloading === `${report.id}:converted`
                              ? <LoaderCircle className="isSpinning" aria-hidden="true" />
                              : <Download aria-hidden="true" />}
                            转换结果
                          </button>
                          <button
                            type="button"
                            disabled={downloading !== null}
                            onClick={() => void download(report, "source")}
                          >
                            {downloading === `${report.id}:source`
                              ? <LoaderCircle className="isSpinning" aria-hidden="true" />
                              : <FileArchive aria-hidden="true" />}
                            原始账单
                          </button>
                        </div>
                      ) : <span className="travelReportsMuted">无下载权限</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {totalPages > 1 ? (
          <div className="travelReportsPagination">
            <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
            <span>{page} / {totalPages}</span>
            <button type="button" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>下一页</button>
          </div>
        ) : null}
      </section>
    </main>
  )
}
