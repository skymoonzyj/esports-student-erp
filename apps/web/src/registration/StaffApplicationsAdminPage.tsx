import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"
import { useNavigate } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import {
  getStaffApplication,
  listStaffApplications,
  reviewStaffApplication,
} from "./api"
import type {
  StaffApplicationAdminDetail,
  StaffApplicationAdminListItem,
  StaffType,
} from "./api"
import { registrationKeys } from "./keys"
import { formatDateOnly } from "../shared/date"
import "./registration.css"

const STAFF_TYPE_LABELS: Record<StaffType, string> = {
  coach: "教练",
  life_teacher: "生活老师",
  customer_service: "客服",
  administration: "行政",
  other: "其他岗位",
}
const STAFF_ADMIN_ROOT = registrationKeys.staffAdminRoot()

type AccessRefreshIntent = {
  id: number
  applicationId: string | null
  outcome: "pending" | "succeeded" | "failed"
}

let accessRefreshIntent: AccessRefreshIntent | null = null
let accessRefreshSequence = 0
let accessRefreshExpiry: ReturnType<typeof setTimeout> | null = null
const accessRefreshListeners = new Set<() => void>()

function publishAccessRefresh(intent: AccessRefreshIntent | null): void {
  accessRefreshIntent = intent
  for (const listener of accessRefreshListeners) listener()
}

function clearAccessRefresh(id?: number): void {
  if (id !== undefined && accessRefreshIntent?.id !== id) return
  if (accessRefreshExpiry !== null) clearTimeout(accessRefreshExpiry)
  accessRefreshExpiry = null
  publishAccessRefresh(null)
}

function settleAccessRefresh(
  id: number,
  outcome: "succeeded" | "failed",
): void {
  if (accessRefreshIntent?.id !== id) return
  publishAccessRefresh({ ...accessRefreshIntent, outcome })
  if (accessRefreshExpiry !== null) clearTimeout(accessRefreshExpiry)
  accessRefreshExpiry = setTimeout(() => {
    if (accessRefreshIntent?.id === id && accessRefreshListeners.size === 0) {
      clearAccessRefresh(id)
    }
  }, 1000)
}

function startAccessRefresh(
  applicationId: string | null,
  refreshUser: () => Promise<void>,
): void {
  const id = ++accessRefreshSequence
  publishAccessRefresh({ id, applicationId, outcome: "pending" })
  let request: Promise<void>
  try {
    request = refreshUser()
  } catch {
    settleAccessRefresh(id, "failed")
    return
  }
  void request.then(
    () => settleAccessRefresh(id, "succeeded"),
    () => settleAccessRefresh(id, "failed"),
  )
}

function subscribeAccessRefresh(listener: () => void): () => void {
  accessRefreshListeners.add(listener)
  return () => accessRefreshListeners.delete(listener)
}

function getAccessRefresh(): AccessRefreshIntent | null {
  return accessRefreshIntent
}

function isPrivateFailure(error: unknown): boolean {
  return error instanceof ProblemDetailsError &&
    (error.problem.status === 403 || error.problem.status === 404)
}

export function StaffApplicationsAdminPage() {
  const { status, user, refreshUser } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const root = STAFF_ADMIN_ROOT
  const canReview = user?.permissions.includes("staff_identity_applications.review") === true
  const canReadSensitive = user?.permissions.includes("registration.sensitive.read") === true
  const [detail, setDetail] = useState<StaffApplicationAdminDetail | null>(null)
  const [reason, setReason] = useState("")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [accessRevoked, setAccessRevoked] = useState(false)
  const [refreshFailed, setRefreshFailed] = useState(false)
  const [queuedRefresh, setQueuedRefresh] = useState<{
    applicationId: string | null
  } | null>(null)
  const requestVersion = useRef(0)
  const previousCanReadSensitive = useRef(canReadSensitive)
  const refreshUserRef = useRef(refreshUser)
  refreshUserRef.current = refreshUser
  const accessRefresh = useSyncExternalStore(
    subscribeAccessRefresh,
    getAccessRefresh,
    getAccessRefresh,
  )
  const refreshingAccess = queuedRefresh !== null || accessRefresh !== null
  const applications = useQuery({
    queryKey: registrationKeys.staffAdminApplications(),
    queryFn: listStaffApplications,
    retry: false,
    enabled: !accessRevoked && !refreshingAccess && !refreshFailed,
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  })
  const refetchApplications = applications.refetch

  const revokeAccess = useCallback(() => {
    clearAccessRefresh()
    requestVersion.current += 1
    setAccessRevoked(true)
    setRefreshFailed(false)
    setQueuedRefresh(null)
    setPending(false)
    setDetail(null)
    setReason("")
    const cancellation = queryClient.cancelQueries({ queryKey: root })
    queryClient.removeQueries({ queryKey: root })
    navigate("/", { replace: true })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey: root }))
  }, [navigate, queryClient, root])

  useEffect(() => {
    if (isPrivateFailure(applications.error)) revokeAccess()
  }, [applications.error, revokeAccess])

  useEffect(() => {
    if (status === "authenticated" && user && !canReview) revokeAccess()
  }, [canReview, revokeAccess, status, user])

  useEffect(() => {
    const lostSensitiveAccess = previousCanReadSensitive.current && !canReadSensitive
    previousCanReadSensitive.current = canReadSensitive
    if (!lostSensitiveAccess) return
    requestVersion.current += 1
    setPending(false)
    setDetail(null)
    setReason("")
  }, [canReadSensitive])

  useEffect(() => () => {
    requestVersion.current += 1
    queryClient.removeQueries({ queryKey: root })
  }, [queryClient, root])

  const refreshAccess = useCallback((): void => {
    if (accessRevoked || queuedRefresh || accessRefresh) return
    requestVersion.current += 1
    const applicationId = detail?.application_id ?? null
    setPending(false)
    setDetail(null)
    setReason("")
    setError(null)
    setRefreshFailed(false)
    setQueuedRefresh({ applicationId })
  }, [accessRefresh, accessRevoked, detail?.application_id, queuedRefresh])

  useEffect(() => {
    if (!queuedRefresh || accessRefresh) return
    const request = queuedRefresh
    setQueuedRefresh(null)
    startAccessRefresh(request.applicationId, refreshUserRef.current)
  }, [accessRefresh, queuedRefresh])

  useEffect(() => {
    if (!accessRefresh || accessRefresh.outcome === "pending") return
    const refreshId = accessRefresh.id
    if (accessRefresh.outcome === "failed") {
      setRefreshFailed(true)
      clearAccessRefresh(refreshId)
      setError("暂时无法刷新权限，请重试")
      return
    }
    if (!canReview) {
      clearAccessRefresh(refreshId)
      revokeAccess()
      return
    }
    let active = true
    const applicationId = accessRefresh.applicationId
    async function reload(): Promise<void> {
      try {
        const listResult = await refetchApplications()
        if (!active || getAccessRefresh()?.id !== refreshId) return
        if (isPrivateFailure(listResult.error)) {
          revokeAccess()
          return
        }
        if (!applicationId || !canReadSensitive) return
        const version = ++requestVersion.current
        setPending(true)
        try {
          const refreshedDetail = await getStaffApplication(applicationId)
          if (
            active &&
            getAccessRefresh()?.id === refreshId &&
            requestVersion.current === version
          ) {
            setDetail(refreshedDetail)
          }
        } catch (caught) {
          if (!active || getAccessRefresh()?.id !== refreshId) return
          if (isPrivateFailure(caught)) revokeAccess()
          else setError("工作人员申请读取失败")
        } finally {
          if (active && requestVersion.current === version) setPending(false)
        }
      } finally {
        if (active) clearAccessRefresh(refreshId)
      }
    }
    void reload()
    return () => {
      active = false
    }
  }, [accessRefresh, canReadSensitive, canReview, refetchApplications, revokeAccess])

  useEffect(() => {
    const handleFocus = () => {
      refreshAccess()
    }
    window.addEventListener("focus", handleFocus)
    return () => window.removeEventListener("focus", handleFocus)
  }, [refreshAccess])

  async function open(application: StaffApplicationAdminListItem): Promise<void> {
    const version = ++requestVersion.current
    setPending(true)
    setError(null)
    try {
      const nextDetail = await getStaffApplication(application.application_id)
      if (requestVersion.current !== version) return
      setDetail(nextDetail)
      setReason("")
    } catch (caught) {
      if (requestVersion.current !== version) return
      if (isPrivateFailure(caught)) revokeAccess()
      else setError("工作人员申请读取失败")
    } finally {
      if (requestVersion.current === version) setPending(false)
    }
  }

  async function review(action: "approve" | "reject"): Promise<void> {
    const normalizedReason = reason.trim()
    if (
      !detail ||
      (action === "reject" && normalizedReason.length < 2) ||
      (action === "approve" && normalizedReason.length === 1)
    ) return
    const version = ++requestVersion.current
    setPending(true)
    setError(null)
    try {
      await reviewStaffApplication(
        detail.application_id,
        action,
        normalizedReason || null,
      )
      if (requestVersion.current !== version) return
      queryClient.setQueryData<StaffApplicationAdminListItem[]>(
        registrationKeys.staffAdminApplications(),
        (current = []) => current.filter((item) => item.application_id !== detail.application_id),
      )
      setDetail(null)
      setReason("")
    } catch (caught) {
      if (requestVersion.current !== version) return
      if (isPrivateFailure(caught)) revokeAccess()
      else setError("身份审核失败，请检查审核备注后重试")
    } finally {
      if (requestVersion.current === version) setPending(false)
    }
  }

  if (accessRevoked) return null

  return (
    <div className="registrationAdminPage">
      <header className="registrationAdminHeader">
        <div>
          <p className="registrationEyebrow">STAFF APPLICATION REVIEW</p>
          <h1>身份申请</h1>
          <p>审核工作人员身份申请；岗位权限在批准前不会生效。</p>
        </div>
        <button type="button" onClick={refreshAccess} disabled={applications.isFetching || refreshingAccess}>
          刷新
        </button>
      </header>

      {applications.isPending ? <p role="status">正在读取身份申请…</p> : null}
      {applications.isError && !isPrivateFailure(applications.error) ? (
        <p className="registrationError" role="alert">身份申请读取失败</p>
      ) : null}
      {error ? <p className="registrationError" role="alert">{error}</p> : null}

      {applications.data ? (
        <section className="registrationAdminList" aria-label="工作人员身份申请">
          {applications.data.length === 0 ? <p>当前没有待处理的身份申请。</p> : null}
          {applications.data.map((application) => (
            <article key={application.application_id}>
              <div>
                <strong>{STAFF_TYPE_LABELS[application.requested_staff_type]}</strong>
                <span>状态：{application.status === "pending" ? "待审核" : application.status}</span>
                <small>{formatDateOnly(application.created_at)}</small>
              </div>
              <button type="button" onClick={() => void open(application)} disabled={pending || refreshingAccess}>
                {canReadSensitive ? "查看完整资料" : "处理申请"}
              </button>
            </article>
          ))}
        </section>
      ) : null}

      {detail ? (
        <section className="registrationReviewPanel" aria-labelledby="staff-review-title">
          <div className="registrationSectionHeading">
            <h2 id="staff-review-title">审核身份申请</h2>
            <button type="button" onClick={() => setDetail(null)}>关闭</button>
          </div>
          <dl className="registrationDetailGrid">
            <div><dt>申请岗位</dt><dd>{STAFF_TYPE_LABELS[detail.requested_staff_type]}</dd></div>
            <div><dt>申请说明</dt><dd>{detail.application_reason}</dd></div>
            {canReadSensitive && detail.full_name ? <div><dt>姓名</dt><dd>{detail.full_name}</dd></div> : null}
            {canReadSensitive && detail.identity_number ? <div><dt>身份证</dt><dd>{detail.identity_number}</dd></div> : null}
            {canReadSensitive && detail.phone ? <div><dt>手机号</dt><dd>{detail.phone}</dd></div> : null}
            {canReadSensitive && detail.email ? <div><dt>邮箱</dt><dd>{detail.email}</dd></div> : null}
          </dl>
          <div className="registrationFieldGroup">
            <label htmlFor="staff-review-reason">审核备注（批准选填，驳回必填）</label>
            <textarea
              id="staff-review-reason"
              rows={4}
              minLength={2}
              maxLength={500}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <div className="registrationReviewActions">
            <button type="button" onClick={() => void review("reject")} disabled={pending || reason.trim().length < 2}>
              驳回申请
            </button>
            <button className="primaryButton" type="button" onClick={() => void review("approve")} disabled={pending || reason.trim().length === 1}>
              批准申请
            </button>
          </div>
        </section>
      ) : null}
    </div>
  )
}
