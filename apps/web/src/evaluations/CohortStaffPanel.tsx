import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus, Square, X } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { createPortal } from "react-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import {
  formatShanghaiDateTime,
  isoToShanghaiLocal,
  shanghaiLocalToIso,
} from "../teaching/datetime"
import {
  createCohortStaffAssignment,
  createStaffRole,
  deactivateStaffRole,
  endStaffAssignment,
  EvaluationDirectoryError,
  evaluationFoundationKeys,
  listAllEvaluationStaffScopes,
  listCohortStaffAssignments,
  listAllEvaluationCohorts,
  listStaffDirectory,
  listStaffRoles,
} from "./foundationApi"
import type { EvaluationCohortDirectoryItem, StaffServiceItem } from "./foundationApi"
import { useEvaluationAuthorization } from "./EvaluationAuthorization"
import { useEvaluationAccessError } from "./useEvaluationAccessError"

import "./evaluation-foundation.css"

const permissionLabels: Record<string, string> = {
  "evaluation_media.upload": "上传评价媒体",
  "evaluation_media.original.read": "查看评价媒体原图",
}

type StaffAccessRevocationScope = "cohort" | "evaluation"

function message(error: unknown, fallback: string): string {
  if (error instanceof ProblemDetailsError) return error.problem.detail
  if (error instanceof EvaluationDirectoryError) return error.message
  return fallback
}

function isCohortObjectAccessDenied(error: unknown): boolean {
  return (
    error instanceof ProblemDetailsError &&
    error.problem.code !== "evaluation_access_denied" &&
    (error.problem.status === 403 || error.problem.status === 404)
  )
}

function StaffDialog({
  kind,
  title,
  pending,
  onClose,
  returnFocus,
  children,
}: {
  kind: string
  title: string
  pending: boolean
  onClose: () => void
  returnFocus?: () => void
  children: ReactNode
}) {
  const titleId = `staff-${kind}-title`
  const backdropRef = useRef<HTMLDivElement>(null)
  const dialogRef = useRef<HTMLElement>(null)
  const closeRef = useRef(onClose)
  const returnFocusRef = useRef(returnFocus)
  const pendingRef = useRef(pending)
  const previousFocusRef = useRef(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  )
  closeRef.current = onClose
  returnFocusRef.current = returnFocus
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
      if (returnFocusRef.current) returnFocusRef.current()
      else previousFocusRef.current?.focus()
    }
  }, [])
  return createPortal(
    <div ref={backdropRef} className="foundationDialogBackdrop">
      <section
        ref={dialogRef}
        className="foundationDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header>
          <h2 id={titleId}>{title}</h2>
          <button type="button" aria-label={`关闭${title}`} disabled={pending} onClick={onClose}>
            <X aria-hidden="true" />
          </button>
        </header>
        {children}
      </section>
    </div>,
    document.body,
  )
}

export function CohortStaffPanel({
  cohortId: fixedCohortId,
  onAccessRevoked,
}: {
  cohortId?: string
  onAccessRevoked?: (scope: StaffAccessRevocationScope) => void
}) {
  const { pending: authorizationPending, revision: authorizationRevision } =
    useEvaluationAuthorization()
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const canManageGlobalRoles = user?.permissions?.includes("cohort_staff.manage") === true
  const queryClient = useQueryClient()
  const [selectedCohortId, setSelectedCohortId] = useState(fixedCohortId ?? "")
  const [assignmentOpen, setAssignmentOpen] = useState(false)
  const [roleOpen, setRoleOpen] = useState(false)
  const [ending, setEnding] = useState<StaffServiceItem | null>(null)
  const [staffQuery, setStaffQuery] = useState("")
  const [staffId, setStaffId] = useState("")
  const [roleId, setRoleId] = useState("")
  const [scopeKind, setScopeKind] = useState<"project" | "class">("project")
  const [scopeId, setScopeId] = useState("")
  const [validFrom, setValidFrom] = useState("")
  const [validUntil, setValidUntil] = useState("")
  const [permissions, setPermissions] = useState<string[]>([])
  const [roleKey, setRoleKey] = useState("")
  const [roleName, setRoleName] = useState("")
  const [roleCategory, setRoleCategory] = useState<"teaching" | "life_service" | "operations">("life_service")
  const [endReason, setEndReason] = useState("")
  const [formError, setFormError] = useState<string | null>(null)
  const [evaluationAccessRevoked, setEvaluationAccessRevoked] = useState(false)
  const assignmentOpenerRef = useRef<HTMLButtonElement>(null)
  const adminKey = [...evaluationFoundationKeys.admin(accountId), "staff"] as const
  const cohortDirectoryKey = [
    ...evaluationFoundationKeys.staffCohorts(accountId),
    "authorization",
    authorizationRevision,
  ] as const
  const currentCohortDirectoryKey = useRef(cohortDirectoryKey)
  currentCohortDirectoryKey.current = cohortDirectoryKey

  const cohorts = useQuery({
    queryKey: cohortDirectoryKey,
    queryFn: listAllEvaluationCohorts,
    enabled: !fixedCohortId && !evaluationAccessRevoked,
  })
  useEffect(() => {
    if (fixedCohortId || !cohorts.data) return
    const nextCohortId = cohorts.data.some((cohort) => cohort.id === selectedCohortId)
      ? selectedCohortId
      : (cohorts.data[0]?.id ?? "")
    if (nextCohortId === selectedCohortId) return
    setSelectedCohortId(nextCohortId)
    setAssignmentOpen(false)
    setEnding(null)
    setStaffQuery("")
    setStaffId("")
    setRoleId("")
    setScopeId("")
    setValidFrom("")
    setValidUntil("")
    setPermissions([])
    setEndReason("")
    setFormError(null)
  }, [cohorts.data, fixedCohortId, selectedCohortId])
  const cohortId =
    fixedCohortId ??
    (cohorts.data?.some((cohort) => cohort.id === selectedCohortId) === true
      ? selectedCohortId
      : "")

  const roles = useQuery({
    queryKey: [...adminKey, "roles", "authorization", authorizationRevision],
    queryFn: listStaffRoles,
    enabled: !evaluationAccessRevoked,
  })
  const staff = useQuery({
    queryKey: [
      ...adminKey,
      "directory",
      cohortId,
      staffQuery.trim(),
      "authorization",
      authorizationRevision,
    ],
    queryFn: () =>
      listStaffDirectory({
        cohortId,
        query: staffQuery.trim(),
        page: 1,
        pageSize: 100,
      }),
    enabled: assignmentOpen && Boolean(cohortId) && !evaluationAccessRevoked,
  })
  const scopes = useQuery({
    queryKey: [
      ...evaluationFoundationKeys.staffScopes(accountId, cohortId, scopeKind),
      "authorization",
      authorizationRevision,
    ],
    queryFn: () => listAllEvaluationStaffScopes(cohortId, scopeKind),
    enabled: assignmentOpen && Boolean(cohortId) && !evaluationAccessRevoked,
  })
  const assignments = useQuery({
    queryKey: [
      ...adminKey,
      "assignments",
      cohortId,
      "authorization",
      authorizationRevision,
    ],
    queryFn: () => listCohortStaffAssignments(cohortId),
    enabled: Boolean(cohortId) && !evaluationAccessRevoked,
  })

  function handleCohortObjectDenial(
    error: unknown,
    targetCohortId: string,
  ): boolean {
    if (!isCohortObjectAccessDenied(error)) return false
    setAssignmentOpen(false)
    setEnding(null)
    setStaffQuery("")
    setStaffId("")
    setRoleId("")
    setScopeId("")
    setValidFrom("")
    setValidUntil("")
    setPermissions([])
    setEndReason("")
    setFormError(null)
    const directoryKey = fixedCohortId ? null : currentCohortDirectoryKey.current
    if (directoryKey) {
      queryClient.setQueryData<EvaluationCohortDirectoryItem[]>(
        directoryKey,
        (current) => current?.filter((cohort) => cohort.id !== targetCohortId),
      )
    }
    queryClient.removeQueries({
      predicate: ({ queryKey }) =>
        queryKey[0] === "evaluations" &&
        queryKey[1] === accountId &&
        queryKey.includes(targetCohortId) &&
        (queryKey.includes("staff") || queryKey.includes("staff-scopes")),
    })
    if (!directoryKey) {
      onAccessRevoked?.("cohort")
    } else {
      setSelectedCohortId((current) =>
        current === targetCohortId ? "" : current,
      )
      void queryClient.refetchQueries({ queryKey: directoryKey, exact: true })
    }
    return true
  }

  const assignmentCreate = useMutation({
    mutationFn: (input: {
      cohortId: string
      roleId: string
      staffId: string
      scopeKind: "project" | "class"
      scopeId: string
      validFrom: string
      validUntil: string
      permissions: string[]
    }) =>
      createCohortStaffAssignment(input.cohortId, {
        role_id: input.roleId,
        staff_person_id: input.staffId,
        scope_kind: input.scopeKind,
        scope_id: input.scopeId,
        valid_from: shanghaiLocalToIso(input.validFrom),
        valid_until: input.validUntil ? shanghaiLocalToIso(input.validUntil) : null,
        permission_keys: input.permissions,
      }),
    onSuccess: async (_, input) => {
      await queryClient.invalidateQueries({
        queryKey: [...adminKey, "assignments", input.cohortId],
      })
      setAssignmentOpen(false)
      setFormError(null)
    },
    onError: (error, input) => {
      if (handleCohortObjectDenial(error, input.cohortId)) return
      setFormError(message(error, "工作人员服务暂时无法创建"))
    },
  })
  const roleCreate = useMutation({
    mutationFn: () => createStaffRole({ key: roleKey, name: roleName, category: roleCategory }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: [...adminKey, "roles"] })
      setRoleOpen(false)
      setRoleKey("")
      setRoleName("")
      setFormError(null)
    },
    onError: (error) => setFormError(message(error, "工作人员角色暂时无法创建")),
  })
  const roleDeactivate = useMutation({
    mutationFn: deactivateStaffRole,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: [...adminKey, "roles"] })
    },
  })
  const assignmentEnd = useMutation({
    mutationFn: (input: { cohortId: string; assignmentId: string; reason: string }) =>
      endStaffAssignment(input.assignmentId, { reason: input.reason }),
    onSuccess: async (_, input) => {
      await queryClient.invalidateQueries({
        queryKey: [...adminKey, "assignments", input.cohortId],
      })
      setEnding(null)
      setEndReason("")
      setFormError(null)
    },
    onError: (error, input) => {
      if (handleCohortObjectDenial(error, input.cohortId)) return
      setFormError(message(error, "工作人员服务暂时无法结束"))
    },
  })
  const accessError = useEvaluationAccessError([
    assignmentCreate.error,
    roleCreate.error,
    roleDeactivate.error,
    assignmentEnd.error,
    assignments.error,
    roles.error,
    staff.error,
    scopes.error,
    cohorts.error,
  ])
  useEffect(() => {
    if (!accessError.permissionRevoked) return
    setEvaluationAccessRevoked(true)
    onAccessRevoked?.("evaluation")
  }, [accessError.permissionRevoked, onAccessRevoked])
  const scopeOptions = scopes.data
  const protectedDirectoryFetching =
    authorizationPending ||
    roles.isFetching ||
    assignments.isFetching ||
    (!fixedCohortId && cohorts.isFetching) ||
    (!fixedCohortId &&
      cohorts.isSuccess &&
      Boolean(selectedCohortId) &&
      !cohortId) ||
    (assignmentOpen && (staff.isFetching || scopes.isFetching))
  const failedProtectedDirectory = [cohorts, roles, staff, scopes, assignments].find(
    (query) => query.isError,
  )

  async function retryProtectedDirectories(): Promise<void> {
    const retries: Array<Promise<unknown>> = [roles.refetch()]
    if (cohortId) retries.push(assignments.refetch())
    if (!fixedCohortId) retries.push(cohorts.refetch())
    if (assignmentOpen) retries.push(staff.refetch(), scopes.refetch())
    await Promise.all(retries)
  }

  function submitAssignment(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (
      !cohortId ||
      !staffId ||
      !roleId ||
      !scopeId ||
      !validFrom ||
      assignmentCreate.isPending
    ) return
    setFormError(null)
    assignmentCreate.mutate({
      cohortId,
      roleId,
      staffId,
      scopeKind,
      scopeId,
      validFrom,
      validUntil,
      permissions: [...permissions],
    })
  }

  function submitRole(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!roleKey.trim() || !roleName.trim() || roleCreate.isPending) return
    setFormError(null)
    roleCreate.mutate()
  }

  function submitEnd(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!endReason.trim()) {
      setFormError("请填写结束原因")
      return
    }
    if (!cohortId || !ending || assignmentEnd.isPending) return
    assignmentEnd.mutate({
      cohortId,
      assignmentId: ending.id,
      reason: endReason.trim(),
    })
  }

  if (accessError.permissionRevoked) {
    return <div className="foundationState">正在更新基础数据权限...</div>
  }
  if (protectedDirectoryFetching) {
    return <div className="foundationState">正在更新工作人员数据...</div>
  }
  if (failedProtectedDirectory) {
    return (
      <div className="foundationState" role="alert">
        <p>{message(failedProtectedDirectory.error, "工作人员数据暂时无法读取")}</p>
        <button type="button" onClick={() => void retryProtectedDirectories()}>
          重试工作人员数据
        </button>
      </div>
    )
  }

  return (
    <section className="foundationPanel" aria-label="工作人员服务">
      <header className="foundationPanelHeader">
        <div>
          <h2>工作人员服务</h2>
          <p>当前服务与历史记录</p>
        </div>
        <div className="foundationPanelActions">
          {canManageGlobalRoles ? (
            <button type="button" onClick={() => setRoleOpen(true)}><Plus aria-hidden="true" />新增工作人员角色</button>
          ) : null}
          <button
            ref={assignmentOpenerRef}
            type="button"
            onClick={() => {
              setStaffQuery("")
              setStaffId("")
              setValidFrom(isoToShanghaiLocal(new Date().toISOString()))
              setAssignmentOpen(true)
            }}
            disabled={!cohortId}
          ><Plus aria-hidden="true" />新增工作人员服务</button>
        </div>
      </header>

      {!fixedCohortId ? (
        <div className="foundationToolbar">
          <label><span>营期</span><select value={cohortId} onChange={(event) => setSelectedCohortId(event.target.value)}>
            <option value="">请选择营期</option>
            {cohorts.data?.map((cohort) => <option key={cohort.id} value={cohort.id}>{cohort.name}</option>)}
          </select></label>
        </div>
      ) : null}

      {roleDeactivate.error ? (
        <p className="foundationFormError" role="alert">
          {message(roleDeactivate.error, "工作人员角色暂时无法停用")}
        </p>
      ) : null}

      <div className="foundationRoleStrip" aria-label="工作人员角色">
        {roles.data?.map((role) => (
          <span key={role.id}>
            <strong>{role.name}</strong>
            <small>{role.category}</small>
            {canManageGlobalRoles && !role.is_builtin && role.is_active ? (
              <button type="button" disabled={roleDeactivate.isPending} onClick={() => roleDeactivate.mutate(role.id)}>停用</button>
            ) : null}
          </span>
        ))}
      </div>

      {assignments.isPending ? <div className="foundationState">正在读取工作人员服务...</div> : null}
      {assignments.data ? (
        <div className="foundationTableWrap">
          <table className="foundationTable responsiveRecords">
            <thead><tr><th>工作人员</th><th>角色</th><th>范围</th><th>权限</th><th>有效期</th><th>状态/原因</th><th><span className="srOnly">操作</span></th></tr></thead>
            <tbody>
              {assignments.data.map((item) => (
                <tr key={item.id}>
                  <td data-label="工作人员"><strong>{item.staff_name}</strong></td>
                  <td data-label="角色">{item.role_name}</td>
                  <td data-label="范围">{item.class_name ?? item.project_name}</td>
                  <td data-label="权限">{item.permission_keys.length ? item.permission_keys.map((key) => permissionLabels[key] ?? key).join("、") : "-"}</td>
                  <td data-label="有效期">{formatShanghaiDateTime(item.valid_from)} - {item.valid_until ? formatShanghaiDateTime(item.valid_until) : "持续"}</td>
                  <td data-label="状态/原因">{item.ended_at ? item.end_reason ?? "已结束" : "进行中"}</td>
                  <td data-label="操作">
                    {item.source === "cohort_staff_assignment" && item.manage_allowed && !item.ended_at ? (
                      <button type="button" className="foundationIconTextButton danger" aria-label={`结束 ${item.staff_name} 的服务`} onClick={() => { setFormError(null); setEndReason(""); setEnding(item) }}><Square aria-hidden="true" />结束</button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {assignmentOpen && !authorizationPending ? (
        <StaffDialog kind="assignment" title="新增工作人员服务" pending={assignmentCreate.isPending} onClose={() => !assignmentCreate.isPending && setAssignmentOpen(false)} returnFocus={() => assignmentOpenerRef.current?.focus()}>
          <form className="foundationForm" onSubmit={submitAssignment}>
            <label><span>搜索工作人员</span><input value={staffQuery} disabled={assignmentCreate.isPending} onChange={(event) => { setStaffQuery(event.target.value); setStaffId("") }} /></label>
            <label><span>工作人员</span><select autoFocus value={staffId} disabled={assignmentCreate.isPending} onChange={(event) => setStaffId(event.target.value)} required><option value="">请选择工作人员</option>{staff.data?.items.map((item) => <option key={item.staff_person_id} value={item.staff_person_id}>{item.staff_name}</option>)}</select></label>
            <label><span>角色</span><select value={roleId} disabled={assignmentCreate.isPending} onChange={(event) => setRoleId(event.target.value)} required><option value="">请选择角色</option>{roles.data?.filter((role) => role.is_active).map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}</select></label>
            <label><span>服务范围</span><select value={scopeKind} disabled={assignmentCreate.isPending} onChange={(event) => { setScopeKind(event.target.value as "project" | "class"); setScopeId("") }}><option value="project">项目</option><option value="class">班级</option></select></label>
            <label><span>{scopeKind === "project" ? "项目" : "班级"}</span><select value={scopeId} disabled={assignmentCreate.isPending} onChange={(event) => setScopeId(event.target.value)} required><option value="">请选择</option>{scopeOptions?.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}</select></label>
            <div className="foundationFormGrid"><label><span>开始时间</span><input type="datetime-local" value={validFrom} disabled={assignmentCreate.isPending} onChange={(event) => setValidFrom(event.target.value)} required /></label><label><span>结束时间</span><input type="datetime-local" value={validUntil} disabled={assignmentCreate.isPending} onChange={(event) => setValidUntil(event.target.value)} /></label></div>
            <fieldset className="foundationChecks"><legend>可分配权限</legend>{Object.entries(permissionLabels).map(([key, label]) => <label key={key}><input type="checkbox" checked={permissions.includes(key)} disabled={assignmentCreate.isPending} onChange={(event) => setPermissions((current) => event.target.checked ? [...current, key] : current.filter((item) => item !== key))} />{label}</label>)}</fieldset>
            {formError ? <p className="foundationFormError" role="alert">{formError}</p> : null}
            <footer className="foundationDialogActions"><button type="button" disabled={assignmentCreate.isPending} onClick={() => setAssignmentOpen(false)}>取消</button><button type="submit" disabled={assignmentCreate.isPending}>确认创建</button></footer>
          </form>
        </StaffDialog>
      ) : null}

      {roleOpen && canManageGlobalRoles && !authorizationPending ? (
        <StaffDialog kind="role" title="新增工作人员角色" pending={roleCreate.isPending} onClose={() => !roleCreate.isPending && setRoleOpen(false)}>
          <form className="foundationForm" onSubmit={submitRole}>
            <label><span>角色标识</span><input autoFocus value={roleKey} disabled={roleCreate.isPending} onChange={(event) => setRoleKey(event.target.value)} pattern="[a-z][a-z0-9_]+" required /></label>
            <label><span>角色名称</span><input value={roleName} disabled={roleCreate.isPending} onChange={(event) => setRoleName(event.target.value)} required /></label>
            <label><span>角色类别</span><select value={roleCategory} disabled={roleCreate.isPending} onChange={(event) => setRoleCategory(event.target.value as typeof roleCategory)}><option value="teaching">教学</option><option value="life_service">生活服务</option><option value="operations">运营</option></select></label>
            {formError ? <p className="foundationFormError" role="alert">{formError}</p> : null}
            <footer className="foundationDialogActions"><button type="button" disabled={roleCreate.isPending} onClick={() => setRoleOpen(false)}>取消</button><button type="submit" disabled={roleCreate.isPending}>确认创建</button></footer>
          </form>
        </StaffDialog>
      ) : null}

      {ending && !authorizationPending ? (
        <StaffDialog kind="end" title="结束工作人员服务" pending={assignmentEnd.isPending} onClose={() => !assignmentEnd.isPending && setEnding(null)}>
          <form className="foundationForm" onSubmit={submitEnd}>
            <label><span>结束原因</span><textarea autoFocus value={endReason} disabled={assignmentEnd.isPending} onChange={(event) => setEndReason(event.target.value)} required /></label>
            {formError ? <p className="foundationFormError" role="alert">{formError}</p> : null}
            <footer className="foundationDialogActions"><button type="button" disabled={assignmentEnd.isPending} onClick={() => setEnding(null)}>取消</button><button type="submit" disabled={assignmentEnd.isPending}>确认结束</button></footer>
          </form>
        </StaffDialog>
      ) : null}
    </section>
  )
}
