import { useQuery } from "@tanstack/react-query"
import { useEffect, useMemo, useRef, useState } from "react"
import type { FormEvent, RefObject } from "react"

import { ProblemDetailsError } from "../api/client"
import {
  getHousingCohortOptions,
  listHousingResidentOptions,
} from "./api"
import type {
  HousingResidentOption,
  HousingRoom,
  HousingStayCreate,
  HousingStayDurationMode,
  HousingStayPurpose,
} from "./api"
import { HousingDialogShell } from "./HousingDialogShell"
import { housingKeys } from "./housingKeys"
import { fromShanghaiDateInput, toShanghaiDateInput } from "./housingDateTime"

const PERSON_TYPE_LABELS = {
  student: "学员",
  player: "队员",
  student_player: "学员兼队员",
  coach: "教练",
  life_teacher: "生活老师",
  customer_service: "客服",
  administration: "行政人员",
  other: "其他工作人员",
  visitor: "临时访客",
} as const

const ACCOUNT_STATUS_HINTS = {
  active: "",
  not_activated: "未激活，不影响已审核人员入住",
  disabled: "账号已停用，不影响已审核人员入住",
  deleted: "账号已删除，不影响已审核人员入住",
} as const

function trimmedOrNull(value: string): string | null {
  return value.trim() || null
}

function residentReady(resident: HousingResidentOption): boolean {
  return resident.housing_ready
}

export function HousingStayDialog({
  accountId,
  accessRevision,
  room,
  pending,
  error,
  focusFallbackRef,
  now = new Date(),
  onClose,
  onSubmit,
  onAccessRevisionMismatch,
  onPrivateFailure,
}: {
  accountId: string
  accessRevision: string
  room: HousingRoom
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  now?: Date
  onClose: () => void
  onSubmit: (input: HousingStayCreate) => void
  onAccessRevisionMismatch: () => void
  onPrivateFailure?: (error: unknown) => void
}) {
  const [query, setQuery] = useState("")
  const [residents, setResidents] = useState<HousingResidentOption[]>([])
  const [residentLoading, setResidentLoading] = useState(false)
  const [residentError, setResidentError] = useState<string | null>(null)
  const [residentSearched, setResidentSearched] = useState(false)
  const [personId, setPersonId] = useState("")
  const [purpose, setPurpose] = useState<HousingStayPurpose>("camp")
  const [durationMode, setDurationMode] = useState<HousingStayDurationMode>("fixed_term")
  const [cohortId, setCohortId] = useState("")
  const [plannedCheckIn, setPlannedCheckIn] = useState(() => (
    toShanghaiDateInput(now.toISOString())
  ))
  const [plannedCheckOut, setPlannedCheckOut] = useState("")
  const [visitReason, setVisitReason] = useState("")
  const [notes, setNotes] = useState("")
  const mismatchReportedRef = useRef(false)
  const allowed = room.allowed_actions.create_stay
  const cohortOptions = useQuery({
    queryKey: housingKeys.cohortOptions(accountId, accessRevision, room.id),
    queryFn: ({ signal }) => getHousingCohortOptions(room.id, signal),
    enabled: allowed,
  })

  const reportMismatch = () => {
    if (mismatchReportedRef.current) return
    mismatchReportedRef.current = true
    setResidents([])
    setPersonId("")
    onAccessRevisionMismatch()
  }

  useEffect(() => {
    const response = cohortOptions.data
    if (response && response.access_revision !== accessRevision) reportMismatch()
  }, [accessRevision, cohortOptions.data])

  useEffect(() => {
    if (
      cohortOptions.error instanceof ProblemDetailsError
      && (cohortOptions.error.problem.status === 403 || cohortOptions.error.problem.status === 404)
    ) onPrivateFailure?.(cohortOptions.error)
  }, [cohortOptions.error, onPrivateFailure])

  useEffect(() => {
    const normalized = query.trim()
    if (!allowed || normalized.length < 1 || mismatchReportedRef.current) {
      setResidents([])
      setPersonId("")
      setResidentLoading(false)
      setResidentError(null)
      setResidentSearched(false)
      return
    }
    setResidents([])
    setPersonId("")
    setResidentError(null)
    setResidentSearched(false)
    const controller = new AbortController()
    const timeout = window.setTimeout(() => {
      setResidentLoading(true)
      setResidentError(null)
      void listHousingResidentOptions(room.id, normalized, controller.signal)
        .then((response) => {
          if (controller.signal.aborted) return
          if (response.access_revision !== accessRevision) {
            reportMismatch()
            return
          }
          setResidents(response.items)
          setResidentSearched(true)
          setPersonId((current) => response.items.some((item) => item.person_id === current && residentReady(item)) ? current : "")
        })
        .catch((caught: unknown) => {
          if (controller.signal.aborted) return
          setResidents([])
          setPersonId("")
          setResidentSearched(true)
          if (
            caught instanceof ProblemDetailsError
            && (caught.problem.status === 403 || caught.problem.status === 404)
          ) {
            onPrivateFailure?.(caught)
            return
          }
          setResidentError(caught instanceof Error ? caught.message : "入住人员暂时无法搜索")
        })
        .finally(() => {
          if (!controller.signal.aborted) setResidentLoading(false)
        })
    }, 300)
    return () => {
      window.clearTimeout(timeout)
      controller.abort()
    }
  }, [accessRevision, accountId, allowed, onPrivateFailure, query, room.id])

  const fixedTerm = durationMode === "fixed_term"
  const validRange = Boolean(
    plannedCheckIn && (!fixedTerm || (
      plannedCheckOut > plannedCheckIn
    )),
  )
  const validPurpose = purpose !== "camp" || Boolean(cohortId)
  const validVisit = purpose !== "temporary" || Boolean(visitReason.trim())
  const canSubmit = Boolean(allowed && personId && validRange && validPurpose && validVisit && !mismatchReportedRef.current && !pending)
  const actionLabel = plannedCheckIn && Date.parse(fromShanghaiDateInput(plannedCheckIn)) > now.getTime() ? "创建预约" : "确认入住"
  const cohortItems = allowed && cohortOptions.data?.access_revision === accessRevision ? cohortOptions.data.items : []
  const selectedResident = useMemo(() => residents.find((item) => item.person_id === personId), [personId, residents])
  const visitorSelected = selectedResident?.person_type === "visitor"

  function changePurpose(next: HousingStayPurpose): void {
    setPurpose(next)
    if (next !== "camp") setCohortId("")
    if (next !== "temporary") setVisitReason("")
    if (next === "camp" || next === "temporary") setDurationMode("fixed_term")
  }

  function selectResident(resident: HousingResidentOption): void {
    setPersonId(resident.person_id)
    if (resident.person_type === "visitor") changePurpose("temporary")
  }

  function changeCohort(next: string): void {
    setCohortId(next)
    const cohort = cohortItems.find((item) => item.id === next)
    if (!cohort) return
    setPlannedCheckIn(toShanghaiDateInput(cohort.default_check_in_at))
    setPlannedCheckOut(toShanghaiDateInput(cohort.default_check_out_at))
  }

  function submit(event: FormEvent): void {
    event.preventDefault()
    if (!canSubmit || !selectedResident) return
    onSubmit({
      person_id: selectedResident.person_id,
      stay_purpose: purpose,
      duration_mode: durationMode,
      cohort_id: purpose === "camp" ? cohortId : null,
      visit_reason: purpose === "temporary" ? trimmedOrNull(visitReason) : null,
      planned_check_in_at: fromShanghaiDateInput(plannedCheckIn),
      planned_check_out_at: fixedTerm ? fromShanghaiDateInput(plannedCheckOut) : null,
      notes: trimmedOrNull(notes),
    })
  }

  return (
    <HousingDialogShell title={`安排入住 · ${room.room_number}`} pending={pending} error={error ?? residentError} focusFallbackRef={focusFallbackRef} onClose={onClose}>
      <form onSubmit={submit}>
        <fieldset disabled={pending || mismatchReportedRef.current || !allowed}>
          <label>搜索入住人员<input data-autofocus value={query} onChange={(event) => setQuery(event.currentTarget.value)} autoComplete="off" /></label>
          {residentLoading ? <p className="housingDialog__hint" aria-live="polite">正在搜索人员</p> : null}
          {residentSearched && !residentLoading && !residentError && residents.length === 0 ? <p className="housingDialog__hint" role="status">未找到匹配人员，请确认姓名、拼音首字母及档案审核状态；原有账号仍需绑定并验证邮箱</p> : null}
          {residents.length > 0 ? <fieldset className="housingResidentOptions"><legend>选择入住人员</legend>{residents.map((resident) => <label key={resident.person_id} aria-disabled={!residentReady(resident)}><input type="radio" name="resident" aria-label={`选择 ${resident.full_name}`} value={resident.person_id} checked={personId === resident.person_id} disabled={!residentReady(resident)} onChange={() => selectResident(resident)} /><span><strong>{resident.full_name}</strong><small>{PERSON_TYPE_LABELS[resident.person_type]} · {resident.sex === "male" ? "男" : "女"}</small>{resident.masked_phone ? <small>{resident.masked_phone}</small> : null}{resident.housing_ready && resident.account_status && ACCOUNT_STATUS_HINTS[resident.account_status] ? <small>{ACCOUNT_STATUS_HINTS[resident.account_status]}</small> : null}</span></label>)}</fieldset> : null}
          <label>住宿类型<select aria-label="住宿类型" value={purpose} disabled={visitorSelected} onChange={(event) => changePurpose(event.currentTarget.value as HousingStayPurpose)}><option value="camp">随营</option><option value="permanent">常住</option><option value="temporary">临时住宿</option></select></label>
          <label>时长类型<select aria-label="时长类型" value={durationMode} disabled={purpose !== "permanent"} onChange={(event) => setDurationMode(event.currentTarget.value as HousingStayDurationMode)}><option value="fixed_term">固定期限</option><option value="long_term">长期居住</option></select></label>
          {purpose === "camp" ? <label>关联营期<select aria-label="关联营期" required value={cohortId} onChange={(event) => changeCohort(event.currentTarget.value)}><option value="">请选择营期</option>{cohortItems.map((cohort) => <option key={cohort.id} value={cohort.id}>{cohort.name}</option>)}</select></label> : null}
          {purpose === "temporary" ? <label>来访原因<textarea required maxLength={500} value={visitReason} onChange={(event) => setVisitReason(event.currentTarget.value)} /></label> : null}
          <label>计划入住日期<input required type="date" value={plannedCheckIn} onChange={(event) => setPlannedCheckIn(event.currentTarget.value)} /></label>
          {fixedTerm ? <label>计划离开日期<input required type="date" value={plannedCheckOut} onChange={(event) => setPlannedCheckOut(event.currentTarget.value)} /></label> : null}
          <label>备注<textarea maxLength={500} value={notes} onChange={(event) => setNotes(event.currentTarget.value)} /></label>
          <footer><button type="button" onClick={onClose}>取消</button><button type="submit" disabled={!canSubmit}>{pending ? "正在保存" : actionLabel}</button></footer>
        </fieldset>
      </form>
    </HousingDialogShell>
  )
}
