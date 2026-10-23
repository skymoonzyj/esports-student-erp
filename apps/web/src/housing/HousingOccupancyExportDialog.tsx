import { useState } from "react"
import type { FormEvent, RefObject } from "react"

import { ProblemDetailsError } from "../api/client"
import type {
  HousingFiltersValue,
  HousingOccupancyExportInput,
} from "./api"
import { HousingDialogShell } from "./HousingDialogShell"

type HousingOccupancyExportDialogProps = {
  filters: HousingFiltersValue
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (input: HousingOccupancyExportInput) => Promise<void>
}

function exportInput(filters: HousingFiltersValue, purpose: string, format: "xlsx" | "csv"):
  HousingOccupancyExportInput {
  return {
    format,
    purpose,
    building_id: filters.buildingId,
    floor_id: filters.floorId,
    query: filters.query,
    room_types: filters.roomTypes,
    verification_statuses: filters.verificationStatuses,
    service_statuses: filters.serviceStatuses,
    current_gender: filters.currentGender,
    occupancy: filters.occupancy,
    assignability: filters.assignability,
  }
}

export function HousingOccupancyExportDialog({
  filters,
  pending,
  error,
  focusFallbackRef,
  onClose,
  onSubmit,
}: HousingOccupancyExportDialogProps) {
  const [purpose, setPurpose] = useState("")
  const [format, setFormat] = useState<"xlsx" | "csv">("xlsx")
  const [formError, setFormError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const normalized = purpose.trim()
    if (normalized.length < 2) {
      setFormError("请填写至少 2 个字的导出用途")
      return
    }
    setFormError(null)
    try {
      await onSubmit(exportInput(filters, normalized, format))
    } catch (caught) {
      setFormError(
        caught instanceof ProblemDetailsError ? caught.problem.detail : "房态导出暂时失败",
      )
    }
  }

  return (
    <HousingDialogShell
      title="导出房态入住名单"
      pending={pending}
      error={error ?? formError}
      focusFallbackRef={focusFallbackRef}
      onClose={onClose}
    >
      <form className="housingReportDialogForm" onSubmit={(event) => void submit(event)}>
        <p className="housingDialog__hint">
          导出字段：房号、入住人、性别、身份证号、手机号、入住时间、离开时间。手机号未绑定时留空；日期只保留年月日。
        </p>
        <label>
          导出用途（必填）
          <input
            aria-label="导出用途（必填）"
            value={purpose}
            minLength={2}
            maxLength={200}
            placeholder="例如：住宿台账核对"
            required
            onChange={(event) => setPurpose(event.target.value)}
          />
        </label>
        <label>
          文件格式
          <select
            aria-label="文件格式"
            value={format}
            onChange={(event) => setFormat(event.target.value as "xlsx" | "csv")}
          >
            <option value="xlsx">Excel（XLSX）</option>
            <option value="csv">CSV</option>
          </select>
        </label>
        {formError && !error ? <p className="housingDialog__error" role="alert">{formError}</p> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={pending}>取消</button>
          <button type="submit" disabled={pending}>{pending ? "正在导出…" : "开始导出"}</button>
        </footer>
      </form>
    </HousingDialogShell>
  )
}
