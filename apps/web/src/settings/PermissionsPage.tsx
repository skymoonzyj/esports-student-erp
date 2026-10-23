import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"
import type { FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import {
  addPermissionOverride,
  assignRole,
  getAccountAccess,
  listPermissionAccounts,
  listPermissionBuildings,
  listPermissionClasses,
  listPermissionCohorts,
  listPermissionEnrollments,
  listPermissionFloors,
  listPermissionPeople,
  listPermissionProjects,
  listPermissionTeams,
  listPermissions,
  listRoles,
  previewPermissionOverride,
  removePermissionOverride,
  removeRole,
} from "./api"
import type {
  EffectivePermission,
  PermissionEffect,
  RolePermission,
  ScopeKind,
} from "./api"
import type { PermissionPreview } from "./api"
import type {
  PermissionBuildingDirectoryItem,
  PermissionClassDirectoryItem,
  PermissionFloorDirectoryItem,
} from "./api"
import type {
  PermissionCohortDirectoryItem,
  PermissionEnrollmentDirectoryItem,
  PermissionProjectDirectoryItem,
  PermissionTeamDirectoryItem,
} from "./api"
import type { PermissionPersonDirectoryItem } from "./api"
import { dateInputToShanghaiIso, formatShanghaiDate } from "../teaching/datetime"
import "./settings.css"

const scopeLabels: Record<ScopeKind, string> = {
  global: "全部数据",
  self: "本人",
  family: "本人家庭",
  person: "指定人员",
  assigned_class: "指定班级",
  assigned_team: "指定队伍",
  cohort: "指定营期",
  project: "指定项目",
  enrollment: "指定报名",
  building: "指定楼栋",
  floor: "指定楼层",
}

type ObjectScopeKind = "cohort" | "project" | "enrollment" | "building" | "floor" | "assigned_team"
type ObjectDirectoryItem =
  | PermissionCohortDirectoryItem
  | PermissionProjectDirectoryItem
  | PermissionEnrollmentDirectoryItem
  | PermissionBuildingDirectoryItem
  | PermissionFloorDirectoryItem
  | PermissionTeamDirectoryItem

const objectScopeSearchLabels: Record<ObjectScopeKind, string> = {
  cohort: "查找指定营期",
  project: "查找指定项目",
  enrollment: "查找指定报名",
  building: "查找指定楼栋",
  floor: "查找指定楼层",
  assigned_team: "查找指定队伍",
}

const organizationScopeKinds = new Set<ScopeKind>([
  "assigned_class",
  "assigned_team",
  "cohort",
  "project",
  "enrollment",
  "building",
  "floor",
])

function scopeRequiresId(scopeKind: ScopeKind): boolean {
  return [
    "person",
    "assigned_class",
    "assigned_team",
    "cohort",
    "project",
    "enrollment",
    "building",
    "floor",
  ].includes(scopeKind)
}

function isObjectScope(scopeKind: ScopeKind): scopeKind is ObjectScopeKind {
  return (
    scopeKind === "cohort" ||
    scopeKind === "project" ||
    scopeKind === "enrollment" ||
    scopeKind === "building" ||
    scopeKind === "floor"
    || scopeKind === "assigned_team"
  )
}

function isSensitiveEvaluationPermission(
  permission: { key: string; sensitive: boolean } | undefined,
): boolean {
  if (!permission?.sensitive) return false
  return (
    permission.key.startsWith("evaluation_") ||
    permission.key === "analysis_profiles.manage" ||
    permission.key === "student_game_accounts.manage" ||
    permission.key === "cohort_staff.manage"
  )
}

const talentPermissionNames: Record<string, string> = {
  "talent_tests.take": "参加天赋测试",
  "talent_tests.read": "查看天赋测试报告",
  "esports_assessments.take": "参加天赋评测 2",
  "esports_assessments.read": "查看天赋评测 2 报告",
}

function isTalentPermission(permission: { key: string } | undefined): boolean {
  return permission?.key === "talent_tests.take" ||
    permission?.key === "talent_tests.read" ||
    permission?.key === "esports_assessments.take" ||
    permission?.key === "esports_assessments.read"
}

function permissionName(permission: { key: string; name: string } | undefined): string {
  if (!permission) return "未选择权限"
  return talentPermissionNames[permission.key] ?? permission.name
}

function talentScopeKinds(permission: { key: string } | undefined): readonly ScopeKind[] | null {
  if (
    permission?.key === "talent_tests.take" ||
    permission?.key === "esports_assessments.take"
  ) return ["self"]
  if (
    permission?.key === "talent_tests.read" ||
    permission?.key === "esports_assessments.read"
  ) {
    return ["global", "self", "family", "person", "assigned_class"]
  }
  return null
}

function formatValidity(value: string): string {
  return formatShanghaiDate(value)
}

function formatValidityRange(
  validFrom: string | null | undefined,
  validUntil: string | null | undefined,
): string {
  if (validFrom && validUntil) {
    return `有效期：${formatValidity(validFrom)} 至 ${formatValidity(validUntil)}`
  }
  if (validFrom) return `有效期：自 ${formatValidity(validFrom)} 起`
  if (validUntil) return `有效期：截至 ${formatValidity(validUntil)}`
  return "有效期：长期有效"
}

function personReference(id: string): string {
  return id
}

function overrideScopeText(
  override: {
    scope_kind: ScopeKind
    scope_id: string | null
    scope_label?: string | null
  },
): string {
  const label = scopeLabels[override.scope_kind]
  if (!override.scope_id) return label
  if (override.scope_kind === "person") {
    return `${label}：${override.scope_label ?? "未知人员"} · 档案 ${personReference(override.scope_id)}`
  }
  if (
    override.scope_kind === "assigned_class" ||
    override.scope_kind === "cohort" ||
    override.scope_kind === "project" ||
    override.scope_kind === "enrollment" ||
    override.scope_kind === "building" ||
    override.scope_kind === "floor"
    || override.scope_kind === "assigned_team"
  ) {
    return `${label}：${override.scope_label ?? "未知范围"}`
  }
  return label
}

function effectiveScopeText(scopeKind: ScopeKind): string {
  return scopeLabels[scopeKind]
}

function effectiveSourceText(sources: EffectivePermission["sources"]): string {
  if (sources.includes("role") && sources.includes("account_override")) {
    return "角色 + 账号覆盖"
  }
  if (sources.includes("account_override")) return "账号覆盖"
  return "角色继承"
}

function rolePermissionScopeText(permission: RolePermission): string {
  return scopeLabels[permission.scope_kind]
}

export function PermissionsPage({
  canManageGlobalPermissions,
  canManageTalentPermissions = false,
  currentAccountId,
  onCurrentAccountAccessChanged,
}: {
  canManageGlobalPermissions: boolean
  canManageTalentPermissions?: boolean
  currentAccountId?: string
  onCurrentAccountAccessChanged?: () => void | Promise<void>
}) {
  const queryClient = useQueryClient()
  const [accountId, setAccountId] = useState("")
  const [roleId, setRoleId] = useState("")
  const [effect, setEffect] = useState<PermissionEffect>("allow")
  const [permissionId, setPermissionId] = useState("")
  const [scopeKind, setScopeKind] = useState<ScopeKind>("global")
  const [scopeId, setScopeId] = useState("")
  const [scopePersonName, setScopePersonName] = useState("")
  const [scopeClassLabel, setScopeClassLabel] = useState("")
  const [scopeObjectLabel, setScopeObjectLabel] = useState("")
  const [scopeClassActive, setScopeClassActive] = useState<boolean | null>(null)
  const [scopeObjectActive, setScopeObjectActive] = useState<boolean | null>(null)
  const [personSearch, setPersonSearch] = useState("")
  const [submittedPersonSearch, setSubmittedPersonSearch] = useState("")
  const [classSearch, setClassSearch] = useState("")
  const [submittedClassSearch, setSubmittedClassSearch] = useState("")
  const [classPage, setClassPage] = useState(1)
  const [objectSearch, setObjectSearch] = useState("")
  const [submittedObjectSearch, setSubmittedObjectSearch] = useState("")
  const [objectPage, setObjectPage] = useState(1)
  const [validFrom, setValidFrom] = useState("")
  const [validUntil, setValidUntil] = useState("")
  const [grantReason, setGrantReason] = useState("")
  const [previewing, setPreviewing] = useState(false)
  const [previewDecision, setPreviewDecision] = useState<PermissionPreview | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const accounts = useQuery({
    queryKey: ["accounts", { permissionDirectory: true }],
    queryFn: listPermissionAccounts,
  })
  const roles = useQuery({ queryKey: ["roles"], queryFn: listRoles })
  const roleCatalog = useMemo(
    () => new Map((roles.data ?? []).map((role) => [role.id, role] as const)),
    [roles.data],
  )
  const permissions = useQuery({ queryKey: ["permissions"], queryFn: listPermissions })
  const visiblePermissions = useMemo(
    () => permissions.data?.filter((permission) => permission.key !== "evaluation_media.upload"),
    [permissions.data],
  )
  const selectablePermissions = useMemo(
    () => visiblePermissions?.filter(
      (permission) => canManageTalentPermissions || !isTalentPermission(permission),
    ),
    [canManageTalentPermissions, visiblePermissions],
  )
  const access = useQuery({
    queryKey: ["account-access", accountId],
    queryFn: () => getAccountAccess(accountId),
    enabled: Boolean(accountId),
  })
  const people = useQuery({
    queryKey: ["permission-person-directory", submittedPersonSearch],
    queryFn: () => listPermissionPeople(submittedPersonSearch),
    enabled: Boolean(submittedPersonSearch),
  })
  const classes = useQuery({
    queryKey: ["permission-class-directory", submittedClassSearch, classPage],
    queryFn: () => listPermissionClasses(submittedClassSearch, classPage),
    enabled:
      canManageGlobalPermissions &&
      scopeKind === "assigned_class" &&
      Boolean(submittedClassSearch),
  })
  const cohorts = useQuery({
    queryKey: ["permission-cohort-directory", submittedObjectSearch, objectPage],
    queryFn: () => listPermissionCohorts(submittedObjectSearch, objectPage),
    enabled:
      canManageGlobalPermissions && scopeKind === "cohort" && Boolean(submittedObjectSearch),
  })
  const projects = useQuery({
    queryKey: ["permission-project-directory", submittedObjectSearch, objectPage],
    queryFn: () => listPermissionProjects(submittedObjectSearch, objectPage),
    enabled:
      canManageGlobalPermissions && scopeKind === "project" && Boolean(submittedObjectSearch),
  })
  const enrollments = useQuery({
    queryKey: ["permission-enrollment-directory", submittedObjectSearch, objectPage],
    queryFn: () => listPermissionEnrollments(submittedObjectSearch, objectPage),
    enabled:
      canManageGlobalPermissions &&
      scopeKind === "enrollment" &&
      Boolean(submittedObjectSearch),
  })
  const buildings = useQuery({
    queryKey: ["permission-building-directory", submittedObjectSearch, objectPage],
    queryFn: () => listPermissionBuildings(submittedObjectSearch, objectPage),
    enabled:
      canManageGlobalPermissions &&
      scopeKind === "building" &&
      Boolean(submittedObjectSearch),
  })
  const floors = useQuery({
    queryKey: ["permission-floor-directory", submittedObjectSearch, objectPage],
    queryFn: () => listPermissionFloors(submittedObjectSearch, objectPage),
    enabled:
      canManageGlobalPermissions && scopeKind === "floor" && Boolean(submittedObjectSearch),
  })
  const teams = useQuery({
    queryKey: ["permission-team-directory", submittedObjectSearch, objectPage],
    queryFn: () => listPermissionTeams(submittedObjectSearch, objectPage),
    enabled: canManageGlobalPermissions && scopeKind === "assigned_team" && Boolean(submittedObjectSearch),
  })

  useEffect(() => {
    if (scopeKind !== "assigned_class" || !classSearch.trim()) {
      setSubmittedClassSearch("")
      return
    }
    const timeout = window.setTimeout(() => {
      setClassPage(1)
      setSubmittedClassSearch(classSearch.trim())
    }, 300)
    return () => window.clearTimeout(timeout)
  }, [classSearch, scopeKind])

  useEffect(() => {
    if (!isObjectScope(scopeKind) || !objectSearch.trim()) {
      setSubmittedObjectSearch("")
      return
    }
    const timeout = window.setTimeout(() => {
      setObjectPage(1)
      setSubmittedObjectSearch(objectSearch.trim())
    }, 300)
    return () => window.clearTimeout(timeout)
  }, [objectSearch, scopeKind])

  const selectedPermission = useMemo(
    () => visiblePermissions?.find((permission) => permission.id === permissionId),
    [permissionId, visiblePermissions],
  )
  const selectedTalentScopes = talentScopeKinds(selectedPermission)
  const sensitiveEvaluationAllow =
    effect === "allow" && isSensitiveEvaluationPermission(selectedPermission)
  const objectDirectory = isObjectScope(scopeKind)
    ? scopeKind === "cohort"
      ? cohorts.data
      : scopeKind === "project"
        ? projects.data
        : scopeKind === "enrollment"
          ? enrollments.data
          : scopeKind === "building"
            ? buildings.data
            : scopeKind === "floor" ? floors.data : teams.data
    : undefined
  async function refreshAccess(): Promise<void> {
    await queryClient.invalidateQueries(
      { queryKey: ["account-access", accountId] },
      { throwOnError: true },
    )
  }

  async function refreshMutationState(): Promise<void> {
    if (accountId === currentAccountId) {
      if (onCurrentAccountAccessChanged) {
        await onCurrentAccountAccessChanged()
      } else {
        queryClient.clear()
      }
    }
    await refreshAccess()
  }

  function overrideInput() {
    const input = {
      permission_id: permissionId,
      effect,
      scope_kind: scopeKind,
      scope_id: scopeRequiresId(scopeKind) ? scopeId : null,
    }
    if (!sensitiveEvaluationAllow) return input
    return {
      ...input,
      valid_from: dateInputToShanghaiIso(validFrom),
      valid_until: dateInputToShanghaiIso(validUntil),
      grant_reason: grantReason.trim(),
    }
  }

  async function addRole(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!accountId || !roleId || pending) return
    setPending(true)
    setError(null)
    try {
      await assignRole(accountId, roleId)
      setRoleId("")
      await refreshMutationState()
    } catch (caught) {
      setError(
        caught instanceof ProblemDetailsError ? caught.problem.detail : "暂时无法分配角色",
      )
    } finally {
      setPending(false)
    }
  }

  async function deleteRole(id: string): Promise<void> {
    if (!accountId || pending) return
    setPending(true)
    setError(null)
    try {
      await removeRole(accountId, id)
      await refreshMutationState()
    } catch (caught) {
      setError(
        caught instanceof ProblemDetailsError ? caught.problem.detail : "暂时无法移除角色",
      )
    } finally {
      setPending(false)
    }
  }

  async function previewOverride(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (isTalentPermission(selectedPermission) && !canManageTalentPermissions) return
    if (
      !accountId ||
      !permissionId ||
      (scopeRequiresId(scopeKind) && !scopeId)
    ) {
      setError("请补齐账号、权限能力和数据范围")
      return
    }
    if (scopeKind === "assigned_class" && effect === "allow" && scopeClassActive === false) {
      setError("停用班级不能新增允许规则")
      return
    }
    if (isObjectScope(scopeKind) && effect === "allow" && scopeObjectActive === false) {
      setError("停用项目、楼栋或楼层不能新增允许规则")
      return
    }
    if (sensitiveEvaluationAllow && scopeKind === "global") {
      setError("敏感评价允许不能使用全部数据范围")
      setPreviewing(false)
      return
    }
    if (sensitiveEvaluationAllow && (!validFrom || !validUntil || !grantReason.trim())) {
      setError("敏感评价允许必须填写有效起止时间和授权原因")
      return
    }
    if (
      accountId === currentAccountId &&
      effect === "deny" &&
      selectedPermission?.key === "permissions.manage"
    ) {
      setError("不能拒绝自己的权限管理能力")
      setPreviewing(false)
      return
    }
    setError(null)
    setPending(true)
    try {
      setPreviewDecision(await previewPermissionOverride(accountId, overrideInput()))
      setPreviewing(true)
    } catch (caught) {
      setPreviewDecision(null)
      setPreviewing(false)
      setError(
        caught instanceof ProblemDetailsError ? caught.problem.detail : "暂时无法计算最终权限",
      )
    } finally {
      setPending(false)
    }
  }

  async function saveOverride(): Promise<void> {
    if (!accountId || !permissionId || pending) return
    if (isTalentPermission(selectedPermission) && !canManageTalentPermissions) return
    setPending(true)
    setError(null)
    try {
      await addPermissionOverride(accountId, overrideInput())
      setPreviewing(false)
      setPreviewDecision(null)
      setScopeId("")
      setScopeClassLabel("")
      setScopeObjectLabel("")
      setScopeClassActive(null)
      setScopeObjectActive(null)
      await refreshMutationState()
    } catch (caught) {
      setError(
        caught instanceof ProblemDetailsError ? caught.problem.detail : "暂时无法保存权限规则",
      )
    } finally {
      setPending(false)
    }
  }

  async function deleteOverride(overrideId: string): Promise<void> {
    if (!accountId || pending) return
    const override = access.data?.permission_overrides.find((item) => item.id === overrideId)
    const permission = visiblePermissions?.find((item) => item.id === override?.permission_id)
    if (isTalentPermission(permission) && !canManageTalentPermissions) return
    setPending(true)
    setError(null)
    try {
      await removePermissionOverride(accountId, overrideId)
      await refreshMutationState()
    } catch (caught) {
      setError(
        caught instanceof ProblemDetailsError ? caught.problem.detail : "暂时无法移除权限规则",
      )
    } finally {
      setPending(false)
    }
  }

  function searchPeople(): void {
    const name = personSearch.trim()
    if (!name) return
    setScopeId("")
    setScopePersonName("")
    setPreviewing(false)
    setSubmittedPersonSearch(name)
  }

  function selectPerson(person: PermissionPersonDirectoryItem): void {
    setScopeId(person.id)
    setScopePersonName(person.full_name)
    setPreviewing(false)
  }

  function selectClass(item: PermissionClassDirectoryItem): void {
    setScopeId(item.id)
    setScopeClassLabel(item.label)
    setScopeClassActive(item.is_active)
    setPreviewing(false)
  }

  function selectObject(item: ObjectDirectoryItem): void {
    setScopeId(item.id)
    setScopeObjectLabel(item.label)
    setScopeObjectActive(
      "is_active" in item
        ? item.is_active &&
            (!("building_is_active" in item) || item.building_is_active)
        : null,
    )
    setPreviewing(false)
  }

  return (
    <div className="settingsPage permissionsPage">
      <div className="settingsHeading">
        <div>
          <p className="pageEyebrow">DENY-FIRST ACCESS</p>
          <h1>权限管理</h1>
          <p>按账号维护角色和精确的数据范围，所有更改由服务器重新计算。</p>
        </div>
      </div>

      <div className="securityPrinciple" role="note">
        <strong>显式拒绝优先于角色和账号允许</strong>
        <span>同一对象同时命中允许与拒绝时，系统始终拒绝。</span>
      </div>

      <div className="settingsToolbar accountSelector">
        <label htmlFor="permission-account">账号</label>
        <select
          id="permission-account"
          value={accountId}
          onChange={(event) => {
            setAccountId(event.target.value)
            setPreviewing(false)
            setError(null)
          }}
        >
          <option value="">请选择账号</option>
          {accounts.data?.map((account) => (
            <option key={account.id} value={account.id}>
              {account.full_name} · {account.status === "active" ? "已启用" : "已停用"} · 账号 {account.id}
            </option>
          ))}
        </select>
      </div>

      {error ? (
        <p className="formSubmitError" role="alert">
          {error}
        </p>
      ) : null}

      {accountId ? (
        <div className="permissionColumns">
          <section className="settingsCard" aria-labelledby="roles-title">
            <div className="settingsCardHeader">
              <h2 id="roles-title">当前角色</h2>
              <span>{access.data?.roles.length ?? 0} 个</span>
            </div>
            <div className="assignmentList">
              {access.data?.roles.map((role) => {
                const roleDefinition = roleCatalog.get(role.id)
                const rolePermissions =
                  role.permissions && role.permissions.length > 0
                    ? role.permissions
                    : roleDefinition?.permissions ?? role.permissions ?? []
                return (
                  <article key={role.id} className="roleAssignment">
                    <div className="roleAssignmentMain">
                      <span>
                        <strong>{role.name}</strong>
                        <small>{role.key}</small>
                      </span>
                      <details className="rolePermissionDetails" open={rolePermissions.length > 0}>
                        <summary>拥有权限 {rolePermissions.length} 项</summary>
                        {rolePermissions.length > 0 ? (
                          <ul className="rolePermissionList" aria-label={`${role.name}拥有的权限`}>
                            {rolePermissions.map((permission) => (
                              <li
                                key={`${permission.id}-${permission.scope_kind}`}
                                className="rolePermissionItem"
                              >
                                <div>
                                  <strong>{permission.name}</strong>
                                  <small>{permission.key}</small>
                                </div>
                                <div className="rolePermissionMeta">
                                  <span className="rolePermissionScope">
                                    {rolePermissionScopeText(permission)}
                                  </span>
                                  {permission.sensitive ? (
                                    <span className="rolePermissionSensitive">敏感权限</span>
                                  ) : null}
                                </div>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className="rolePermissionEmpty">该角色暂未配置权限。</p>
                        )}
                      </details>
                    </div>
                    {canManageGlobalPermissions ? (
                      <button
                        type="button"
                        aria-label={
                          accountId === currentAccountId && role.key === "super_admin"
                            ? "超级管理员角色受保护"
                            : `移除 ${role.name}`
                        }
                        disabled={
                          pending ||
                          (accountId === currentAccountId && role.key === "super_admin")
                        }
                        onClick={() => void deleteRole(role.id)}
                      >
                        {accountId === currentAccountId && role.key === "super_admin"
                          ? "当前账号受保护"
                          : `移除 ${role.name}`}
                      </button>
                    ) : null}
                  </article>
                )
              })}
              {access.data?.roles.length === 0 ? <p>当前没有分配角色。</p> : null}
            </div>
            {canManageGlobalPermissions ? (
              <form className="inlineAssignment" onSubmit={(event) => void addRole(event)}>
                <label htmlFor="role-assignment">添加角色</label>
                <select
                  id="role-assignment"
                  value={roleId}
                  onChange={(event) => setRoleId(event.target.value)}
                  required
                >
                  <option value="">请选择角色</option>
                  {roles.data?.map((role) => (
                    <option key={role.id} value={role.id}>
                      {role.name}
                    </option>
                  ))}
                </select>
                <button className="secondaryButton" type="submit" disabled={pending}>
                  分配角色
                </button>
              </form>
            ) : null}
          </section>

          <section className="settingsCard" aria-labelledby="override-title">
            <div className="settingsCardHeader">
              <h2 id="override-title">账号覆盖规则</h2>
              <span>{access.data?.permission_overrides.length ?? 0} 条</span>
            </div>
            <div className="assignmentList overrideList">
              {access.data?.permission_overrides.map((override) => {
                const permission = visiblePermissions?.find(
                  (item) => item.id === override.permission_id,
                )
                return (
                  <article key={override.id}>
                    <span className="overrideRuleSummary">
                      <strong>{permission ? permissionName(permission) : override.permission_id}</strong>
                      <small>
                        {override.effect === "deny" ? "拒绝" : "允许"} · {overrideScopeText(override)}
                      </small>
                      <small>
                        {formatValidityRange(override.valid_from, override.valid_until)}
                      </small>
                      {override.grant_reason ? <small>授权原因：{override.grant_reason}</small> : null}
                    </span>
                    {!isTalentPermission(permission) || canManageTalentPermissions ? <button
                      type="button"
                      aria-label={
                        accountId === currentAccountId &&
                        permission?.key === "permissions.manage" &&
                        override.effect === "allow"
                          ? "自己的权限管理授权受保护"
                          : `移除规则 ${permission ? permissionName(permission) : override.permission_id}`
                      }
                      disabled={
                        pending ||
                        (accountId === currentAccountId &&
                          permission?.key === "permissions.manage" &&
                          override.effect === "allow")
                      }
                      onClick={() => void deleteOverride(override.id)}
                    >
                      {accountId === currentAccountId &&
                      permission?.key === "permissions.manage" &&
                      override.effect === "allow"
                        ? "当前账号受保护"
                        : "移除规则"}
                    </button> : null}
                  </article>
                )
              })}
              {access.data?.permission_overrides.length === 0 ? <p>当前没有账号覆盖规则。</p> : null}
            </div>
          </section>
        </div>
      ) : null}

      {accountId ? (
        <section className="settingsCard effectivePermissionsCard" aria-labelledby="effective-permissions-title">
          <div className="settingsCardHeader">
            <div>
              <h2 id="effective-permissions-title">当前拥有权限</h2>
              <p>按当前角色、账号覆盖和有效期计算；明确拒绝规则仍显示在上方。</p>
            </div>
            <span>{access.data?.effective_permissions?.length ?? 0} 项</span>
          </div>
          <div className="effectivePermissionList">
            {access.data?.effective_permissions?.map((permission) => (
              <article key={permission.id}>
                <div className="effectivePermissionSummary">
                  <strong>{permissionName(permission)}</strong>
                  <small>{permission.key}</small>
                </div>
                <div className="effectivePermissionMeta">
                  <span className="effectivePermissionSource">
                    {effectiveSourceText(permission.sources)}
                  </span>
                  {permission.sensitive ? (
                    <span className="effectivePermissionSensitive">敏感权限</span>
                  ) : null}
                </div>
                <div className="effectivePermissionScopes" aria-label={`${permission.name}数据范围`}>
                  {permission.scope_kinds.map((scopeKind) => (
                    <span key={scopeKind}>{effectiveScopeText(scopeKind)}</span>
                  ))}
                </div>
              </article>
            ))}
            {access.data && (access.data.effective_permissions?.length ?? 0) === 0 ? (
              <p>当前没有可用权限。</p>
            ) : null}
          </div>
        </section>
      ) : null}

      {accountId ? (
        <section className="settingsCard overrideEditor" aria-labelledby="override-editor-title">
          <div className="settingsCardHeader">
            <div>
              <h2 id="override-editor-title">新增账号覆盖规则</h2>
              <p>可按全部数据、指定人员或指定班级计算最终权限。</p>
            </div>
          </div>
          <form className="overrideForm" onSubmit={(event) => void previewOverride(event)}>
            <div className="formField">
              <label htmlFor="override-effect">允许或拒绝</label>
              <select
                id="override-effect"
                value={effect}
                onChange={(event) => {
                  const nextEffect = event.target.value as PermissionEffect
                  setEffect(nextEffect)
                  setScopeId("")
                  setScopePersonName("")
                  setScopeClassLabel("")
                  setScopeObjectLabel("")
                  setScopeClassActive(null)
                  setScopeObjectActive(null)
                  setPreviewing(false)
                }}
              >
                <option value="allow">允许</option>
                <option value="deny">拒绝</option>
              </select>
            </div>
            <div className="formField">
              <label htmlFor="override-permission">权限能力</label>
              <select
                id="override-permission"
                value={permissionId}
                onChange={(event) => {
                  const nextPermissionId = event.target.value
                  setPermissionId(nextPermissionId)
                  const nextPermission = visiblePermissions?.find(
                    (permission) => permission.id === nextPermissionId,
                  )
                  const nextTalentScopes = talentScopeKinds(nextPermission)
                  if (nextTalentScopes && !nextTalentScopes.includes(scopeKind)) {
                    setScopeKind(nextTalentScopes[0])
                    setScopeId("")
                  }
                  setPreviewing(false)
                }}
                required
              >
                <option value="">请选择权限</option>
                {selectablePermissions?.map((permission) => (
                  <option key={permission.id} value={permission.id}>
                    {permissionName(permission)}
                  </option>
                ))}
              </select>
              {selectedPermission?.key === "talent_tests.take" ? (
                <small>参加天赋测试还必须同时具备查看天赋测试报告权限（本人范围）。</small>
              ) : null}
              {selectedPermission?.key === "esports_assessments.take" ? (
                <small>参加天赋评测 2 还必须同时具备查看天赋评测 2 报告权限（本人范围）。</small>
              ) : null}
            </div>
            <div className="formField">
              <label htmlFor="override-scope">数据范围</label>
              <select
                id="override-scope"
                value={scopeKind}
                onChange={(event) => {
                  const nextScope = event.target.value as ScopeKind
                  if (!canManageGlobalPermissions && organizationScopeKinds.has(nextScope)) return
                  setScopeKind(nextScope)
                  setPreviewing(false)
                  setScopeId("")
                  setScopePersonName("")
                  setScopeClassLabel("")
                  setScopeObjectLabel("")
                  setScopeClassActive(null)
                  setScopeObjectActive(null)
                  setClassSearch("")
                  setSubmittedClassSearch("")
                  setClassPage(1)
                  setObjectSearch("")
                  setSubmittedObjectSearch("")
                  setObjectPage(1)
                }}
              >
                {Object.entries(scopeLabels)
                  .filter(
                    ([value]) =>
                      (!selectedTalentScopes || selectedTalentScopes.includes(value as ScopeKind)) &&
                      (canManageGlobalPermissions ||
                        !organizationScopeKinds.has(value as ScopeKind)),
                  )
                  .map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                  ))}
              </select>
            </div>
            {scopeKind === "person" ? (
              <div className="formField personScopePicker">
                <div className="settingsSearch">
                  <label htmlFor="override-person-search">查找指定人员</label>
                  <div>
                    <input
                      id="override-person-search"
                      value={personSearch}
                      onChange={(event) => setPersonSearch(event.target.value)}
                      placeholder="输入姓名"
                      required
                    />
                    <button className="secondaryButton" type="button" onClick={searchPeople}>查找人员</button>
                  </div>
                </div>
                {submittedPersonSearch ? (
                  <div className="settingsCandidates" aria-label="人员搜索结果">
                    {people.data?.map((person) => (
                      <label key={person.id}>
                        <input
                          type="radio"
                          name="scope-person"
                          checked={scopeId === person.id}
                          onChange={() => selectPerson(person)}
                          aria-label={`${person.full_name} · 档案 ${personReference(person.id)}`}
                        />
                        <span>
                          <strong>{person.full_name}</strong>
                          <small>档案 {personReference(person.id)}</small>
                        </span>
                      </label>
                    ))}
                    {people.data?.length === 0 ? <p>没有找到匹配人员。</p> : null}
                  </div>
                ) : null}
              </div>
            ) : null}
            {scopeKind === "assigned_class" ? (
              <div className="formField personScopePicker">
                <div className="settingsSearch">
                  <label htmlFor="override-class-search">查找指定班级</label>
                  <div>
                    <input
                      id="override-class-search"
                      value={classSearch}
                      onChange={(event) => {
                        setClassSearch(event.target.value)
                        setScopeId("")
                        setScopeClassLabel("")
                        setScopeClassActive(null)
                        setPreviewing(false)
                      }}
                      placeholder="输入营期、班级或项目"
                    />
                  </div>
                </div>
                {submittedClassSearch ? (
                  <div className="settingsCandidates" aria-label="班级搜索结果">
                    {classes.data?.items.map((item) => {
                      const disabled = effect === "allow" && !item.is_active
                      return (
                        <label key={item.id} aria-disabled={disabled}>
                          <input
                            type="radio"
                            name="scope-class"
                            checked={scopeId === item.id}
                            disabled={disabled}
                            onChange={() => selectClass(item)}
                            aria-label={`${item.label}${item.is_active ? "" : "（已停用）"}`}
                          />
                          <span>
                            <strong>{item.label}</strong>
                            <small>{item.is_active ? "可授权" : "已停用"}</small>
                          </span>
                        </label>
                      )
                    })}
                    {classes.data?.items.length === 0 ? <p>没有找到匹配班级。</p> : null}
                    {classes.data && classes.data.total > classes.data.page_size ? (
                      <div className="settingsPagination" aria-label="班级结果分页">
                        <button
                          className="secondaryButton"
                          type="button"
                          disabled={classes.data.page <= 1}
                          onClick={() => setClassPage((page) => Math.max(1, page - 1))}
                        >
                          上一页
                        </button>
                        <span>
                          第 {classes.data.page} 页 · 共 {classes.data.total} 个
                        </span>
                        <button
                          className="secondaryButton"
                          type="button"
                          disabled={
                            classes.data.page * classes.data.page_size >= classes.data.total
                          }
                          onClick={() => setClassPage((page) => page + 1)}
                        >
                          下一页
                        </button>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}
            {isObjectScope(scopeKind) ? (
              <div className="formField personScopePicker">
                <div className="settingsSearch">
                  <label htmlFor="override-object-search">
                    {objectScopeSearchLabels[scopeKind]}
                  </label>
                  <div>
                    <input
                      id="override-object-search"
                      value={objectSearch}
                      onChange={(event) => {
                        setObjectSearch(event.target.value)
                        setScopeId("")
                        setScopeObjectLabel("")
                        setScopeObjectActive(null)
                        setPreviewing(false)
                      }}
                      placeholder={`输入${scopeLabels[scopeKind].replace("指定", "")}`}
                    />
                  </div>
                </div>
                {submittedObjectSearch ? (
                  <div className="settingsCandidates" aria-label={`${scopeLabels[scopeKind]}搜索结果`}>
                    {objectDirectory?.items.map((item) => {
                      const inactiveObject =
                        "is_active" in item &&
                        (!item.is_active ||
                          ("building_is_active" in item && !item.building_is_active))
                      const disabled = effect === "allow" && inactiveObject
                      return (
                        <label key={item.id} aria-disabled={disabled}>
                          <input
                            type="radio"
                            name={`scope-${scopeKind}`}
                            checked={scopeId === item.id}
                            disabled={disabled}
                            onChange={() => selectObject(item)}
                            aria-label={`${item.label} · ${item.id}`}
                          />
                          <span>
                            <strong>{item.label}</strong>
                            <small>{inactiveObject ? "已停用" : item.id}</small>
                          </span>
                        </label>
                      )
                    })}
                    {objectDirectory?.items.length === 0 ? <p>没有找到匹配结果。</p> : null}
                    {objectDirectory && objectDirectory.total > objectDirectory.page_size ? (
                      <div className="settingsPagination" aria-label={`${scopeLabels[scopeKind]}结果分页`}>
                        <button
                          className="secondaryButton"
                          type="button"
                          disabled={objectDirectory.page <= 1}
                          onClick={() => setObjectPage((page) => Math.max(1, page - 1))}
                        >
                          上一页
                        </button>
                        <span>
                          第 {objectDirectory.page} 页 · 共 {objectDirectory.total} 个
                        </span>
                        <button
                          className="secondaryButton"
                          type="button"
                          disabled={objectDirectory.page * objectDirectory.page_size >= objectDirectory.total}
                          onClick={() => setObjectPage((page) => page + 1)}
                        >
                          下一页
                        </button>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}
            {sensitiveEvaluationAllow ? (
              <div className="finitePermissionFields">
                <div className="formField">
                  <label htmlFor="override-valid-from">授权开始日期</label>
                  <input
                    id="override-valid-from"
                    type="date"
                    value={validFrom}
                    onChange={(event) => {
                      setValidFrom(event.target.value)
                      setPreviewing(false)
                    }}
                  />
                </div>
                <div className="formField">
                  <label htmlFor="override-valid-until">授权结束日期</label>
                  <input
                    id="override-valid-until"
                    type="date"
                    value={validUntil}
                    onChange={(event) => {
                      setValidUntil(event.target.value)
                      setPreviewing(false)
                    }}
                  />
                </div>
                <div className="formField grantReasonField">
                  <label htmlFor="override-grant-reason">授权原因</label>
                  <input
                    id="override-grant-reason"
                    value={grantReason}
                    onChange={(event) => {
                      setGrantReason(event.target.value)
                      setPreviewing(false)
                    }}
                  />
                </div>
              </div>
            ) : null}
            <button className="primaryButton" type="submit" disabled={pending}>
              {pending ? "正在计算…" : "查看变更摘要"}
            </button>
          </form>

          {previewing ? (
            <div className="permissionPreview" role="status">
              <strong>
                {previewDecision?.allowed
                  ? "最终结果：允许（具体对象仍按显式拒绝优先）"
                  : "最终结果：拒绝（匹配的显式拒绝优先）"}
              </strong>
              <span>
                {effect === "deny"
                  ? "这条显式拒绝将覆盖角色允许和账号允许"
                  : "这条账号允许将扩展角色能力，但不会覆盖显式拒绝"}
              </span>
              <span>
                {permissionName(selectedPermission)} · {scopeLabels[scopeKind]}
                {scopeKind === "person"
                  ? `：${scopePersonName} · 档案 ${personReference(scopeId)}`
                  : scopeKind === "assigned_class"
                    ? `：${scopeClassLabel}`
                    : isObjectScope(scopeKind)
                      ? `：${scopeObjectLabel}`
                    : ""}
              </span>
              <div>
                <button
                  className="secondaryButton"
                  type="button"
                  onClick={() => setPreviewing(false)}
                >
                  返回修改
                </button>
                <button
                  className="primaryButton"
                  type="button"
                  onClick={() => void saveOverride()}
                  disabled={pending}
                >
                  确认保存
                </button>
              </div>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  )
}
