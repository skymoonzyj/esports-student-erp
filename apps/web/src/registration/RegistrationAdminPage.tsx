import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"
import type { FormEvent } from "react"
import { useNavigate } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import {
  getRegistrationChannelSettings,
  getRegistrationConflict,
  listRegistrationConflicts,
  rejectRegistrationConflict,
  resolveRegistrationConflict,
  saveRegistrationChannelSettings,
} from "./api"
import type {
  RegistrationChannelSettings,
  RegistrationAdminDetail,
  RegistrationAdminListItem,
  RegistrationPurpose,
} from "./api"
import { registrationKeys } from "./keys"
import "./registration.css"

const PURPOSE_LABELS: Record<RegistrationPurpose, string> = {
  student: "学员",
  guardian: "监护人",
  staff_applicant: "工作人员申请",
  visitor: "临时访客",
}
const REGISTRATION_ADMIN_ROOT = registrationKeys.adminRoot()

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

export function RegistrationAdminPage() {
  const { status, user, refreshUser } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const root = REGISTRATION_ADMIN_ROOT
  const canReview = user?.permissions.includes("registration.review") === true
  const canReadSensitive = user?.permissions.includes("registration.sensitive.read") === true
  const [detail, setDetail] = useState<RegistrationAdminDetail | null>(null)
  const [reason, setReason] = useState("")
  const [candidatePersonId, setCandidatePersonId] = useState("")
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
    queryKey: registrationKeys.adminApplications(),
    queryFn: listRegistrationConflicts,
    retry: false,
    enabled: !accessRevoked && !refreshingAccess && !refreshFailed,
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  })
  const refetchApplications = applications.refetch
  const channelSettingsQuery = useQuery({
    queryKey: registrationKeys.adminChannelSettings(),
    queryFn: getRegistrationChannelSettings,
    retry: false,
    enabled: !accessRevoked && !refreshingAccess && !refreshFailed,
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  })
  const refetchChannelSettings = channelSettingsQuery.refetch
  const [channelForm, setChannelForm] = useState({
    phone_enabled: false,
    email_enabled: true,
  })
  const [channelFormInitialized, setChannelFormInitialized] = useState(false)
  const [channelNotice, setChannelNotice] = useState<string | null>(null)
  const [channelError, setChannelError] = useState<string | null>(null)

  useEffect(() => {
    const settings = channelSettingsQuery.data
    if (!settings || channelFormInitialized) return
    setChannelForm({
      phone_enabled: settings.phone_enabled,
      email_enabled: settings.email_enabled,
    })
    setChannelFormInitialized(true)
  }, [channelFormInitialized, channelSettingsQuery.data])

  const saveChannels = useMutation({
    mutationFn: saveRegistrationChannelSettings,
    onSuccess: (settings) => {
      queryClient.setQueryData<RegistrationChannelSettings>(
        registrationKeys.adminChannelSettings(),
        settings,
      )
      setChannelForm({
        phone_enabled: settings.phone_enabled,
        email_enabled: settings.email_enabled,
      })
      setChannelNotice("账号申请渠道已保存")
      setChannelError(null)
    },
    onError: (caught) => {
      const message = caught instanceof ProblemDetailsError
        ? caught.problem.detail
        : "保存账号申请渠道失败，请稍后重试"
      setChannelError(message)
      setChannelNotice(null)
    },
  })

  const revokeAccess = useCallback(() => {
    clearAccessRefresh()
    requestVersion.current += 1
    setAccessRevoked(true)
    setRefreshFailed(false)
    setQueuedRefresh(null)
    setPending(false)
    setDetail(null)
    setReason("")
    setCandidatePersonId("")
    const cancellation = queryClient.cancelQueries({ queryKey: root })
    queryClient.removeQueries({ queryKey: root })
    navigate("/", { replace: true })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey: root }))
  }, [navigate, queryClient, root])

  useEffect(() => {
    if (isPrivateFailure(applications.error)) revokeAccess()
  }, [applications.error, revokeAccess])

  useEffect(() => {
    if (isPrivateFailure(channelSettingsQuery.error)) revokeAccess()
  }, [channelSettingsQuery.error, revokeAccess])

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
    setCandidatePersonId("")
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
    setCandidatePersonId("")
    setError(null)
    setRefreshFailed(false)
    setChannelNotice(null)
    setChannelError(null)
    setChannelFormInitialized(false)
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
        const [listResult, channelResult] = await Promise.all([
          refetchApplications(),
          refetchChannelSettings(),
        ])
        if (!active || getAccessRefresh()?.id !== refreshId) return
        if (isPrivateFailure(listResult.error) || isPrivateFailure(channelResult.error)) {
          revokeAccess()
          return
        }
        if (!applicationId || !canReadSensitive) return
        const version = ++requestVersion.current
        setPending(true)
        try {
          const refreshedDetail = await getRegistrationConflict(applicationId)
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
          else setError("暂时无法读取注册申请")
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
  }, [
    accessRefresh,
    canReadSensitive,
    canReview,
    refetchApplications,
    refetchChannelSettings,
    revokeAccess,
  ])

  useEffect(() => {
    const handleFocus = () => {
      void refreshAccess()
    }
    window.addEventListener("focus", handleFocus)
    return () => window.removeEventListener("focus", handleFocus)
  }, [refreshAccess])

  async function open(application: RegistrationAdminListItem): Promise<void> {
    const version = ++requestVersion.current
    setPending(true)
    setError(null)
    try {
      const nextDetail = await getRegistrationConflict(application.application_id)
      if (requestVersion.current !== version) return
      setDetail(nextDetail)
      setReason("")
      setCandidatePersonId("")
    } catch (caught) {
      if (requestVersion.current !== version) return
      if (isPrivateFailure(caught)) revokeAccess()
      else setError("暂时无法读取注册申请")
    } finally {
      if (requestVersion.current === version) setPending(false)
    }
  }

  async function reject(): Promise<void> {
    if (!detail || reason.trim().length < 2) return
    const version = ++requestVersion.current
    setPending(true)
    setError(null)
    try {
      await rejectRegistrationConflict(detail.application_id, reason.trim())
      if (requestVersion.current !== version) return
      queryClient.setQueryData<RegistrationAdminListItem[]>(
        registrationKeys.adminApplications(),
        (current = []) => current.filter((item) => item.application_id !== detail.application_id),
      )
      setDetail(null)
      setReason("")
    } catch (caught) {
      if (requestVersion.current !== version) return
      if (isPrivateFailure(caught)) revokeAccess()
      else setError("审核操作失败，请检查原因后重试")
    } finally {
      if (requestVersion.current === version) setPending(false)
    }
  }

  async function resolve(): Promise<void> {
    if (!detail || !candidatePersonId || reason.trim().length < 2) return
    const version = ++requestVersion.current
    setPending(true)
    setError(null)
    try {
      await resolveRegistrationConflict(detail.application_id, {
        candidate_person_id: candidatePersonId,
        reason: reason.trim(),
      })
      if (requestVersion.current !== version) return
      queryClient.setQueryData<RegistrationAdminListItem[]>(
        registrationKeys.adminApplications(),
        (current = []) => current.filter((item) => item.application_id !== detail.application_id),
      )
      setDetail(null)
      setReason("")
      setCandidatePersonId("")
    } catch (caught) {
      if (requestVersion.current !== version) return
      if (isPrivateFailure(caught)) revokeAccess()
      else setError("关联开通失败，请重新核对资料")
    } finally {
      if (requestVersion.current === version) setPending(false)
    }
  }

  function submitChannelSettings(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (
      channelForm.phone_enabled &&
      channelSettingsQuery.data &&
      !channelSettingsQuery.data.phone_signup_allowed_by_deployment
    ) {
      setChannelError("当前部署未开放手机号申请，请先启用手机号注册能力")
      setChannelNotice(null)
      return
    }
    saveChannels.mutate(channelForm)
  }

  if (accessRevoked) return null

  return (
    <div className="registrationAdminPage">
      <header className="registrationAdminHeader">
        <div>
          <p className="registrationEyebrow">REGISTRATION REVIEW</p>
          <h1>注册处理</h1>
          <p>处理实名冲突；列表默认只展示遮罩联系方式。</p>
        </div>
        <button type="button" onClick={() => void refreshAccess()} disabled={applications.isFetching || refreshingAccess}>
          刷新
        </button>
      </header>

      <section className="registrationChannelPanel" aria-labelledby="registration-channel-title">
        <div className="registrationSectionHeading">
          <div>
            <h2 id="registration-channel-title">账号申请渠道</h2>
            <p>控制普通自助注册是否开放手机号或邮箱申请。监护邀请不受此设置影响。</p>
          </div>
          <span>管理员可随时调整</span>
        </div>
        {channelSettingsQuery.isPending ? (
          <p className="registrationChannelState" role="status">正在读取渠道设置…</p>
        ) : null}
        {channelSettingsQuery.isError && !isPrivateFailure(channelSettingsQuery.error) ? (
          <div className="registrationChannelState" role="alert">
            <p>暂时无法读取账号申请渠道</p>
            <button type="button" onClick={() => void refetchChannelSettings()}>重新读取</button>
          </div>
        ) : null}
        {channelSettingsQuery.data ? (
          <form className="registrationChannelForm" onSubmit={submitChannelSettings}>
            <label className="registrationChannelToggle">
              <input
                type="checkbox"
                aria-label="手机号申请账号"
                checked={channelForm.phone_enabled}
                disabled={
                  saveChannels.isPending ||
                  !channelSettingsQuery.data.phone_signup_allowed_by_deployment
                }
                onChange={(event) => {
                  setChannelForm((current) => ({
                    ...current,
                    phone_enabled: event.target.checked,
                  }))
                  setChannelNotice(null)
                  setChannelError(null)
                }}
              />
              <span>
                <strong>手机号申请账号</strong>
                <small>
                  {channelSettingsQuery.data.phone_signup_allowed_by_deployment
                    ? "开放后可通过手机号验证创建账号"
                    : "当前部署未启用手机号注册能力"}
                </small>
              </span>
              <em>{channelForm.phone_enabled ? "开放" : "关闭"}</em>
            </label>
            <label className="registrationChannelToggle">
              <input
                type="checkbox"
                aria-label="邮箱申请账号"
                checked={channelForm.email_enabled}
                disabled={saveChannels.isPending}
                onChange={(event) => {
                  setChannelForm((current) => ({
                    ...current,
                    email_enabled: event.target.checked,
                  }))
                  setChannelNotice(null)
                  setChannelError(null)
                }}
              />
              <span>
                <strong>邮箱申请账号</strong>
                <small>开放后可通过邮箱验证码创建账号</small>
              </span>
              <em>{channelForm.email_enabled ? "开放" : "关闭"}</em>
            </label>
            {!channelForm.phone_enabled && !channelForm.email_enabled ? (
              <p className="registrationChannelWarning" role="status">
                两种申请方式均已关闭，普通用户将无法自助申请账号。
              </p>
            ) : null}
            {channelNotice ? <p className="registrationChannelNotice" role="status">{channelNotice}</p> : null}
            {channelError ? <p className="registrationError" role="alert">{channelError}</p> : null}
            <div className="registrationChannelActions">
              <button className="primaryButton" type="submit" disabled={saveChannels.isPending}>
                {saveChannels.isPending ? "保存中…" : "保存渠道设置"}
              </button>
            </div>
          </form>
        ) : null}
      </section>

      {applications.isPending ? <p role="status">正在读取注册申请…</p> : null}
      {applications.isError && !isPrivateFailure(applications.error) ? (
        <p className="registrationError" role="alert">注册申请读取失败</p>
      ) : null}
      {error ? <p className="registrationError" role="alert">{error}</p> : null}

      {applications.data ? (
        <section className="registrationAdminList" aria-label="冲突注册申请">
          {applications.data.length === 0 ? <p>当前没有待处理的冲突申请。</p> : null}
          {applications.data.map((application) => (
            <article key={application.application_id}>
              <div>
                <strong>{application.masked_identifier}</strong>
                <span>{PURPOSE_LABELS[application.primary_purpose]} · 候选档案 {application.candidate_count} 个</span>
                <small>冲突类型：{application.conflict_code ?? "待核对"}</small>
              </div>
              <button type="button" onClick={() => void open(application)} disabled={pending || refreshingAccess}>
                {canReadSensitive ? "查看完整资料" : "处理申请"}
              </button>
            </article>
          ))}
        </section>
      ) : null}

      {detail ? (
        <section className="registrationReviewPanel" aria-labelledby="registration-review-title">
          <div className="registrationSectionHeading">
            <h2 id="registration-review-title">审核注册申请</h2>
            <button type="button" onClick={() => setDetail(null)}>关闭</button>
          </div>
          <dl className="registrationDetailGrid">
            <div><dt>联系方式</dt><dd>{detail.masked_identifier}</dd></div>
            <div><dt>用途</dt><dd>{PURPOSE_LABELS[detail.primary_purpose]}</dd></div>
            {canReadSensitive && detail.full_name ? <div><dt>姓名</dt><dd>{detail.full_name}</dd></div> : null}
            {canReadSensitive && detail.identity_number ? <div><dt>身份证</dt><dd>{detail.identity_number}</dd></div> : null}
            {canReadSensitive && detail.verified_identifier ? <div><dt>已验证联系方式</dt><dd>{detail.verified_identifier}</dd></div> : null}
          </dl>

          {canReadSensitive && detail.candidates ? (
            <fieldset className="registrationCandidateList">
              <legend>选择完全一致的既有档案</legend>
              {detail.candidates.map((candidate) => (
                <label key={candidate.person_id}>
                  <input
                    type="radio"
                    name="registration-candidate"
                    checked={candidatePersonId === candidate.person_id}
                    onChange={() => setCandidatePersonId(candidate.person_id)}
                    aria-label={`${candidate.full_name} ${candidate.deleted ? "已删除" : "可用"}`}
                  />
                  <span>
                    <strong>{candidate.full_name}</strong>
                    <small>{candidate.identity_number} · {candidate.date_of_birth}</small>
                  </span>
                </label>
              ))}
            </fieldset>
          ) : null}

          <div className="registrationFieldGroup">
            <label htmlFor="registration-review-reason">审核原因</label>
            <textarea
              id="registration-review-reason"
              rows={4}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              minLength={2}
              maxLength={500}
              required
            />
          </div>
          <div className="registrationReviewActions">
            <button type="button" onClick={() => void reject()} disabled={pending || reason.trim().length < 2}>
              驳回申请
            </button>
            {canReadSensitive ? (
              <button
                className="primaryButton"
                type="button"
                onClick={() => void resolve()}
                disabled={pending || !candidatePersonId || reason.trim().length < 2}
              >
                确认关联并开通
              </button>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  )
}
