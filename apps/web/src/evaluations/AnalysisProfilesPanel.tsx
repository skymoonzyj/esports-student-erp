import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus, X } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { createPortal } from "react-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import {
  formatShanghaiDate,
  todayDateInput,
  dateInputToShanghaiIso,
} from "../teaching/datetime"
import {
  createAnalysisProfile,
  EvaluationDirectoryError,
  evaluationFoundationKeys,
  listAllEvaluationProjects,
  listAnalysisProfiles,
} from "./foundationApi"
import type {
  AnalysisProfileInput,
  EvaluationProjectDirectoryItem,
} from "./foundationApi"
import { useEvaluationAuthorization } from "./EvaluationAuthorization"
import { useEvaluationAccessError } from "./useEvaluationAccessError"

import "./evaluation-foundation.css"

type AnalysisMediaSlotDraft = NonNullable<AnalysisProfileInput["media_slots"]>[number]

const SINGLE_RESULT_SLOT: AnalysisMediaSlotDraft[] = [
  { key: "match_result", label: "战绩截图", required: true },
]

function defaultMediaSlots(adapterId: string): AnalysisMediaSlotDraft[] {
  if (adapterId.trim() === "peace_elite") {
    return [
      { key: "profile", label: "个人资料", required: true },
      { key: "match_result", label: "结算战绩", required: true },
      { key: "team_detail", label: "队伍明细", required: true },
    ]
  }
  return SINGLE_RESULT_SLOT.map((slot) => ({ ...slot }))
}

function isObjectAccessError(error: unknown): error is ProblemDetailsError {
  return (
    error instanceof ProblemDetailsError &&
    (error.problem.status === 403 || error.problem.status === 404) &&
    error.problem.code !== "evaluation_access_denied"
  )
}

function AnalysisDialog({
  pending,
  onClose,
  children,
}: {
  pending: boolean
  onClose: () => void
  children: ReactNode
}) {
  const backdropRef = useRef<HTMLDivElement>(null)
  const dialogRef = useRef<HTMLElement>(null)
  const closeRef = useRef(onClose)
  const pendingRef = useRef(pending)
  const previousFocusRef = useRef(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  )
  closeRef.current = onClose
  pendingRef.current = pending

  useEffect(() => {
    const backdrop = backdropRef.current
    const dialog = dialogRef.current
    if (!backdrop || !dialog) return

    const backgroundStates = Array.from(document.body.children)
      .filter(
        (element): element is HTMLElement =>
          element instanceof HTMLElement && element !== backdrop,
      )
      .map((element) => ({ element, hadInert: element.hasAttribute("inert") }))
    for (const { element } of backgroundStates) element.setAttribute("inert", "")

    const focusableSelector = [
      "button:not([disabled])",
      "a[href]",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      '[tabindex]:not([tabindex="-1"])',
    ].join(",")
    const focusableElements = (): HTMLElement[] =>
      Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (element) => !element.hasAttribute("hidden"),
      )
    if (!dialog.contains(document.activeElement)) focusableElements()[0]?.focus()

    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape" && !pendingRef.current) {
        event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== "Tab") return
      const focusable = focusableElements()
      if (!focusable.length) {
        event.preventDefault()
        dialog?.focus()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !dialog?.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("keydown", onKeyDown)
      for (const { element, hadInert } of backgroundStates) {
        if (!hadInert) element.removeAttribute("inert")
      }
      previousFocusRef.current?.focus()
    }
  }, [])

  return createPortal(
    <div ref={backdropRef} className="foundationDialogBackdrop">
      <section
        ref={dialogRef}
        className="foundationDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="analysis-profile-create-title"
        tabIndex={-1}
      >
        <header><h2 id="analysis-profile-create-title">创建分析配置新版本</h2><button type="button" aria-label="关闭创建分析配置新版本" disabled={pending} onClick={onClose}><X aria-hidden="true" /></button></header>
        {children}
      </section>
    </div>,
    document.body,
  )
}

export function AnalysisProfilesPanel() {
  const { pending: authorizationPending, revision: authorizationRevision } =
    useEvaluationAuthorization()
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const queryClient = useQueryClient()
  const adminKey = [...evaluationFoundationKeys.admin(accountId), "analysis-profiles"] as const
  const [projectId, setProjectId] = useState("")
  const [dialogOpen, setDialogOpen] = useState(false)
  const [effectiveFrom, setEffectiveFrom] = useState("")
  const [reason, setReason] = useState("")
  const [mode, setMode] = useState<"manual_only" | "automatic">("manual_only")
  const [adapterId, setAdapterId] = useState("")
  const [adapterVersion, setAdapterVersion] = useState("")
  const [scoringRuleVersion, setScoringRuleVersion] = useState("")
  const [resultSchemaVersion, setResultSchemaVersion] = useState("")
  const [requiredFieldsText, setRequiredFieldsText] = useState("")
  const [confidenceThreshold, setConfidenceThreshold] = useState("0.8")
  const [publishMode, setPublishMode] = useState<
    AnalysisProfileInput["publish_mode"]
  >("review_required")
  const [mediaSlots, setMediaSlots] = useState<AnalysisMediaSlotDraft[]>(
    defaultMediaSlots(""),
  )
  const [formError, setFormError] = useState<string | null>(null)
  const projectDirectoryKey = [
    ...evaluationFoundationKeys.analysisProjects(accountId),
    "authorization",
    authorizationRevision,
  ] as const
  const currentProjectDirectoryKey = useRef(projectDirectoryKey)
  currentProjectDirectoryKey.current = projectDirectoryKey
  const projects = useQuery({
    queryKey: projectDirectoryKey,
    queryFn: listAllEvaluationProjects,
  })
  useEffect(() => {
    if (!projects.data) return
    const nextProjectId = projects.data.some((project) => project.id === projectId)
      ? projectId
      : (projects.data[0]?.id ?? "")
    if (nextProjectId === projectId) return
    setProjectId(nextProjectId)
    setDialogOpen(false)
    setEffectiveFrom("")
    setReason("")
    setMode("manual_only")
    setAdapterId("")
    setAdapterVersion("")
    setScoringRuleVersion("")
    setResultSchemaVersion("")
    setRequiredFieldsText("")
    setConfidenceThreshold("0.8")
    setPublishMode("review_required")
    setMediaSlots(defaultMediaSlots(""))
    setFormError(null)
  }, [projectId, projects.data])
  const authorizedProjectId =
    projects.data?.some((project) => project.id === projectId) === true
      ? projectId
      : ""
  const profiles = useQuery({
    queryKey: [...adminKey, authorizedProjectId, "authorization", authorizationRevision],
    queryFn: () => listAnalysisProfiles(authorizedProjectId),
    enabled: Boolean(authorizedProjectId),
  })
  const latest = useMemo(
    () =>
      profiles.data?.length
        ? profiles.data.reduce((highest, profile) =>
            profile.version > highest.version ? profile : highest,
          )
        : undefined,
    [profiles.data],
  )

  const requiredFields = requiredFieldsText
    .split(/[\s,，]+/)
    .map((field) => field.trim())
    .filter(Boolean)
  const uniqueRequiredFields = Array.from(new Set(requiredFields))
  const requiredFieldsReady =
    requiredFields.length > 0 &&
    requiredFields.length === uniqueRequiredFields.length &&
    requiredFields.every((field) => /^[a-z][a-z0-9_]{0,79}$/.test(field))
  const numericConfidenceThreshold = Number(confidenceThreshold)
  const mediaSlotKeys = mediaSlots.map((slot) => slot.key.trim())
  const mediaSlotsReady =
    mediaSlots.length >= 1 &&
    mediaSlots.length <= 3 &&
    mediaSlotKeys.length === new Set(mediaSlotKeys).size &&
    mediaSlots.every(
      (slot) =>
        /^[a-z][a-z0-9_]{0,79}$/.test(slot.key.trim()) &&
        slot.label.trim().length > 0,
    )
  const automaticInputReady =
    mode === "automatic" &&
    Boolean(
      adapterId.trim() &&
      adapterVersion.trim() &&
      scoringRuleVersion.trim() &&
      resultSchemaVersion.trim(),
    ) &&
    requiredFieldsReady &&
    Number.isFinite(numericConfidenceThreshold) &&
    numericConfidenceThreshold >= 0 &&
    numericConfidenceThreshold <= 1 &&
    mediaSlotsReady

  function buildProfileInput(): AnalysisProfileInput {
    const selectedPublishMode = mode === "automatic" ? publishMode : "review_required"
    const shared = {
      expected_current_version: latest?.version ?? 1,
      auto_publish_warning_levels: [],
      publish_mode: selectedPublishMode,
      effective_from: dateInputToShanghaiIso(effectiveFrom),
      change_reason: reason.trim(),
    }
    if (mode === "manual_only") {
      return {
        ...shared,
        mode,
        adapter_id: null,
        adapter_version: null,
        scoring_rule_version: null,
        required_fields: uniqueRequiredFields,
        media_slots: mediaSlots.map((slot) => ({
          key: slot.key.trim(),
          label: slot.label.trim(),
          required: slot.required,
        })),
        confidence_thresholds: {},
      }
    }
    return {
      ...shared,
      mode,
      adapter_id: adapterId.trim(),
      adapter_version: adapterVersion.trim(),
      scoring_rule_version: scoringRuleVersion.trim(),
      result_schema_version: resultSchemaVersion.trim(),
      required_fields: uniqueRequiredFields,
      media_slots: mediaSlots.map((slot) => ({
        key: slot.key.trim(),
        label: slot.label.trim(),
        required: slot.required,
      })),
      confidence_thresholds: Object.fromEntries(
        uniqueRequiredFields.map((field) => [field, numericConfidenceThreshold]),
      ),
    }
  }

  const create = useMutation({
    mutationFn: (targetProjectId: string) =>
      createAnalysisProfile(targetProjectId, buildProfileInput()),
    onSuccess: async (_, targetProjectId) => {
      await queryClient.invalidateQueries({ queryKey: [...adminKey, targetProjectId] })
      setDialogOpen(false)
      setReason("")
      setMode("manual_only")
      setAdapterId("")
      setAdapterVersion("")
      setScoringRuleVersion("")
      setResultSchemaVersion("")
      setRequiredFieldsText("")
      setConfidenceThreshold("0.8")
      setPublishMode("review_required")
      setMediaSlots(defaultMediaSlots(""))
      setFormError(null)
    },
    onError: (error, targetProjectId) => {
      if (!isObjectAccessError(error)) {
        setFormError(
          error instanceof ProblemDetailsError
            ? error.problem.detail
            : "分析配置新版本暂时无法创建",
        )
        return
      }
      setDialogOpen(false)
      setEffectiveFrom("")
      setReason("")
      setMode("manual_only")
      setAdapterId("")
      setAdapterVersion("")
      setScoringRuleVersion("")
      setResultSchemaVersion("")
      setRequiredFieldsText("")
      setConfidenceThreshold("0.8")
      setPublishMode("review_required")
      setMediaSlots(defaultMediaSlots(""))
      setFormError(null)
      const directoryKey = currentProjectDirectoryKey.current
      queryClient.setQueryData<EvaluationProjectDirectoryItem[]>(
        directoryKey,
        (current) => current?.filter((project) => project.id !== targetProjectId),
      )
      setProjectId((current) =>
        current === targetProjectId ? "" : current,
      )
      queryClient.removeQueries({ queryKey: [...adminKey, targetProjectId] })
      void queryClient.refetchQueries({ queryKey: directoryKey, exact: true })
    },
  })
  const accessError = useEvaluationAccessError([
    create.error,
    profiles.error,
    projects.error,
  ])
  const protectedDirectoryFetching =
    authorizationPending ||
    projects.isFetching ||
    (projects.isSuccess && projects.data.length > 0 && !authorizedProjectId) ||
    (Boolean(authorizedProjectId) && profiles.isFetching)
  const failedProtectedDirectory = [projects, profiles].find((query) => query.isError)

  async function retryProtectedDirectories(): Promise<void> {
    const retries: Array<Promise<unknown>> = [projects.refetch()]
    if (authorizedProjectId) retries.push(profiles.refetch())
    await Promise.all(retries)
  }

  function openDialog(): void {
    setEffectiveFrom(todayDateInput())
    setReason("")
    setMode("manual_only")
    setAdapterId("")
    setAdapterVersion("")
    setScoringRuleVersion("")
    setResultSchemaVersion("")
    setRequiredFieldsText("")
    setConfidenceThreshold("0.8")
    setPublishMode("review_required")
    setMediaSlots(defaultMediaSlots(""))
    setFormError(null)
    setDialogOpen(true)
  }

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (
      !latest ||
      !authorizedProjectId ||
      !effectiveFrom ||
      !reason.trim() ||
      !requiredFieldsReady ||
      !mediaSlotsReady ||
      (mode === "automatic" && !automaticInputReady) ||
      create.isPending
    ) return
    create.mutate(authorizedProjectId)
  }

  if (accessError.permissionRevoked) {
    return <div className="foundationState">正在更新基础数据权限...</div>
  }
  if (protectedDirectoryFetching) {
    return <div className="foundationState">正在更新分析配置...</div>
  }
  if (failedProtectedDirectory) {
    return (
      <div className="foundationState" role="alert">
        <p>
          {failedProtectedDirectory.error instanceof ProblemDetailsError
            ? failedProtectedDirectory.error.problem.detail
            : failedProtectedDirectory.error instanceof EvaluationDirectoryError
              ? failedProtectedDirectory.error.message
            : "分析配置暂时无法读取"}
        </p>
        <button type="button" onClick={() => void retryProtectedDirectories()}>
          重试分析配置
        </button>
      </div>
    )
  }

  const projectName = latest?.project_name

  return (
    <section className="foundationPanel analysisProfilesPanel" aria-label="分析配置">
      <header className="foundationPanelHeader">
        <div><h2>{projectName ? `${projectName}分析配置` : "分析配置"}</h2><p>版本只追加，不覆盖历史</p></div>
        {latest ? <button type="button" className="foundationPrimaryButton" onClick={openDialog}><Plus aria-hidden="true" />创建新版本</button> : null}
      </header>
      <div className="foundationToolbar analysisToolbar">
        <label><span>项目</span><select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">请选择项目</option>{projects.data?.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        <label><span>分析模式</span><select value={latest?.mode ?? "manual_only"} onChange={() => undefined}><option value="manual_only">人工分析</option><option value="automatic">自动分析</option></select></label>
      </div>
      {profiles.isPending ? <div className="foundationState">正在读取分析配置...</div> : null}
      <div className="profileVersionList">
        {profiles.data?.map((profile) => (
          <article key={profile.id} className={profile.is_current ? "current" : "history"}>
            <header><strong>{profile.is_current ? `当前版本 V${profile.version}` : `历史版本 V${profile.version}`}</strong><span>{formatShanghaiDate(profile.effective_from)}</span></header>
            <dl><div><dt>模式</dt><dd>{profile.mode === "manual_only" ? "人工分析" : "自动分析"}</dd></div><div><dt>必需字段</dt><dd>{profile.required_fields.length ? profile.required_fields.join("、") : "-"}</dd></div><div><dt>发布方式</dt><dd>{profile.publish_mode === "review_required" ? "审核后发布" : "自动发布"}</dd></div><div><dt>配置人</dt><dd>{profile.configured_by_name ?? "系统"}</dd></div></dl>
            <p>{profile.change_reason}</p>
          </article>
        ))}
      </div>

      {dialogOpen && latest && !authorizationPending ? (
        <AnalysisDialog pending={create.isPending} onClose={() => setDialogOpen(false)}>
          <form className="foundationForm" onSubmit={submit}>
            <p>基于最新版本 V{latest.version}</p>
            <label><span>分析模式</span><select value={mode} disabled={create.isPending} onChange={(event) => setMode(event.target.value as typeof mode)}><option value="manual_only">人工分析</option><option value="automatic">自动分析</option></select></label>
            <label><span>必需字段</span><input value={requiredFieldsText} disabled={create.isPending} placeholder="kills, deaths, assists" onChange={(event) => setRequiredFieldsText(event.target.value)} required /></label>
            {mode === "automatic" ? (
              <>
                <label><span>适配器 ID</span><input value={adapterId} disabled={create.isPending} maxLength={80} onChange={(event) => {
                  const nextAdapterId = event.target.value
                  setAdapterId(nextAdapterId)
                  if (["lol", "valorant", "peace_elite"].includes(nextAdapterId.trim())) {
                    setMediaSlots(defaultMediaSlots(nextAdapterId))
                  }
                }} required /></label>
                <label><span>适配器版本</span><input value={adapterVersion} disabled={create.isPending} maxLength={40} pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,39}" onChange={(event) => setAdapterVersion(event.target.value)} required /></label>
                <label><span>评分规则版本</span><input value={scoringRuleVersion} disabled={create.isPending} maxLength={40} pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,39}" onChange={(event) => setScoringRuleVersion(event.target.value)} required /></label>
                <label><span>结果模式版本</span><input value={resultSchemaVersion} disabled={create.isPending} maxLength={40} pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,39}" onChange={(event) => setResultSchemaVersion(event.target.value)} required /></label>
                <label><span>置信度阈值</span><input type="number" min="0" max="1" step="0.01" value={confidenceThreshold} disabled={create.isPending} onChange={(event) => setConfidenceThreshold(event.target.value)} required /></label>
                <label><span>发布方式</span><select value={publishMode} disabled={create.isPending} onChange={(event) => setPublishMode(event.target.value as AnalysisProfileInput["publish_mode"])}><option value="review_required">审核后发布</option><option value="auto_publish">满足质量门禁后自动发布</option></select></label>
              </>
            ) : null}
            <fieldset>
              <legend>截图槽位</legend>
              {mediaSlots.map((slot, index) => (
                <div key={index}>
                  <label><span>{`槽位 ${index + 1} 标识`}</span><input value={slot.key} disabled={create.isPending} maxLength={80} pattern="[a-z][a-z0-9_]{0,79}" onChange={(event) => setMediaSlots((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, key: event.target.value } : item))} required /></label>
                  <label><span>{`槽位 ${index + 1} 名称`}</span><input value={slot.label} disabled={create.isPending} maxLength={80} onChange={(event) => setMediaSlots((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, label: event.target.value } : item))} required /></label>
                  <label><input type="checkbox" checked={slot.required} disabled={create.isPending} onChange={(event) => setMediaSlots((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, required: event.target.checked } : item))} />{`槽位 ${index + 1} 必填`}</label>
                  {mediaSlots.length > 1 ? <button type="button" disabled={create.isPending} onClick={() => setMediaSlots((current) => current.filter((_, itemIndex) => itemIndex !== index))}>{`删除槽位 ${index + 1}`}</button> : null}
                </div>
              ))}
              <button type="button" disabled={create.isPending || mediaSlots.length >= 3} onClick={() => setMediaSlots((current) => [...current, { key: `image_${current.length + 1}`, label: `截图 ${current.length + 1}`, required: false }])}>添加截图槽位</button>
            </fieldset>
            <label><span>生效日期</span><input autoFocus type="date" value={effectiveFrom} disabled={create.isPending} onChange={(event) => setEffectiveFrom(event.target.value)} required /></label>
            <label><span>变更原因</span><textarea value={reason} disabled={create.isPending} onChange={(event) => setReason(event.target.value)} minLength={4} required /></label>
            {formError ? <p className="foundationFormError" role="alert">{formError}</p> : null}
            <footer className="foundationDialogActions"><button type="button" disabled={create.isPending} onClick={() => setDialogOpen(false)}>取消</button><button type="submit" disabled={create.isPending || !requiredFieldsReady || !mediaSlotsReady || (mode === "automatic" && !automaticInputReady)}>确认创建</button></footer>
          </form>
        </AnalysisDialog>
      ) : null}
    </section>
  )
}
