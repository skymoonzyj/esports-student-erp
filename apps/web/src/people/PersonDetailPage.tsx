import { useQuery, useQueryClient } from "@tanstack/react-query"
import { lazy, Suspense, useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { Link, useParams } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { AssistedProfileCorrectionPanel } from "../assistedPeople/AssistedProfileCorrectionPanel"
import { documentTypeLabel } from "../assistedPeople/documentFacts"
import { formatDateOnly } from "../shared/date"
import { ModuleErrorBoundary } from "../shared/ModuleErrorBoundary"
import { StudentMatchingPanel } from "../evaluations/StudentMatchingPanel"
import { PersonAssetsPanel } from "../assets/PersonAssetsPanel"
import { FoodPreferencesPanel } from "../foodPreferences/FoodPreferencesPanel"
import { createAccount } from "../settings/api"
import { GuardianPanel } from "./GuardianPanel"
import { StaffProfilePanel } from "./StaffProfilePanel"
import { PersonHousingPanel } from "./PersonHousingPanel"
import { TeamAllocationPanel } from "./TeamAllocationPanel"
import { PersonAffiliationsSection } from "./PersonAffiliationsSection"
import { getPerson, revealSensitive } from "./api"
import { ageText, personAccountStatusLabels, personTypeText, staffTypeLabels, teamProjectionText } from "./presentation"
import "./people.css"

const AssessmentReportsPanel = lazy(() => import("../esportsAssessments/AssessmentReportsPanel").then(module => ({ default: module.AssessmentReportsPanel })))
const ReactionxReportsPanel = lazy(() => import("../reactionx/ReactionxReportsPanel").then(module => ({ default: module.ReactionxReportsPanel })))
const ActivationRolesPanel = lazy(() => import("../assistedPeople/ActivationRolesPanel").then(module => ({ default: module.ActivationRolesPanel })))
const PendingRoleIntentPanel = lazy(() => import("../assistedPeople/PendingRoleIntentPanel").then(module => ({ default: module.PendingRoleIntentPanel })))

type SensitiveField = "phone" | "identity_number"
export const SENSITIVE_REVEAL_DURATION_MS = 60_000
const GUARDIAN_ACCOUNT_MESSAGE = "请先绑定已成年、已实名且账号启用的监护人"

const missingLabels: Record<string, string> = {
  full_name: "姓名",
  identity_number: "证件号码",
  sex: "性别",
  date_of_birth: "出生日期",
  phone: "手机号",
  email: "邮箱",
  guardian: "有效实名监护人",
}

function revealLabel(field: SensitiveField): string {
  return field === "identity_number" ? "查看完整证件号码" : "查看完整手机号"
}

function hasGuardianError(error: ProblemDetailsError): boolean {
  return error.problem.errors?.some((item) => item.field === "guardian") === true
}

type DetailProps = {
  permissions?: ReadonlySet<string>
  scopedPermissions?: ReadonlySet<string>
  revealDurationMs?: number
}

export function PersonDetailPage(props: DetailProps) {
  const auth = useOptionalAuth()
  if (auth && (!auth.user || auth.status !== "authenticated")) return <p>请先登录。</p>
  const accountId = auth?.user?.account_id ?? "legacy"
  const scope = JSON.stringify([accountId, auth?.user?.is_super_admin, [...auth?.user?.permissions ?? []].sort(), [...auth?.user?.scoped_permissions ?? []].sort(), [...props.permissions ?? []].sort(), [...props.scopedPermissions ?? []].sort()])
  return <AccountPersonDetailPage key={scope} accountId={accountId} scope={scope} {...props} />
}

function AccountPersonDetailPage({
  permissions = new Set<string>(),
  scopedPermissions = new Set<string>(),
  revealDurationMs = SENSITIVE_REVEAL_DURATION_MS,
  accountId,
  scope,
}: DetailProps & { accountId: string; scope: string }) {
  const client = useQueryClient()
  const auth = useOptionalAuth()
  const { personId } = useParams()
  const [detailAccessRevoked, setDetailAccessRevoked] = useState(false)
  const [dialogField, setDialogField] = useState<SensitiveField | null>(null)
  const [purpose, setPurpose] = useState("")
  const [revealed, setRevealed] = useState<Partial<Record<SensitiveField, string>>>({})
  const [revealError, setRevealError] = useState<string | null>(null)
  const [revealPending, setRevealPending] = useState(false)
  const [accountDialogOpen, setAccountDialogOpen] = useState(false)
  const [accountPending, setAccountPending] = useState(false)
  const [accountError, setAccountError] = useState<string | null>(null)
  const [accountNeedsGuardian, setAccountNeedsGuardian] = useState(false)
  const [temporaryPassword, setTemporaryPassword] = useState<string | null>(null)
  const [evaluationPanelRevoked, setEvaluationPanelRevoked] = useState(false)
  const [foodPreferencesPanelRevoked, setFoodPreferencesPanelRevoked] =
    useState(false)
  const [housingPanelRevoked, setHousingPanelRevoked] = useState(false)
  const hideTimers = useRef<
    Partial<Record<SensitiveField, ReturnType<typeof setTimeout>>>
  >({})
  const routeVersion = useRef(0)
  const accountRequestVersion = useRef(0)
  const person = useQuery({
    queryKey: ["person", personId, accountId, scope],
    queryFn: ({ signal }) => getPerson(personId ?? "", signal),
    enabled: Boolean(personId) && !detailAccessRevoked,
    staleTime: 0, gcTime: 0, retry: false,
  })
  useEffect(() => {
    if (!(person.error instanceof ProblemDetailsError) || ![401, 403, 404].includes(person.error.problem.status) || detailAccessRevoked) return
    setDetailAccessRevoked(true)
    const clear = () => {
      for (const queryKey of [["person", personId], ["people"], ["people-counts"]]) {
        void client.cancelQueries({ queryKey })
        client.removeQueries({ queryKey })
      }
    }
    clear()
    void auth?.refreshUser({ background: true }).catch(() => undefined).finally(clear)
  }, [auth, client, detailAccessRevoked, person.error, personId])
  useEffect(() => () => {
    const predicate = (query: { queryKey: readonly unknown[] }) => query.queryKey[0] === "person" && query.queryKey.at(-1) === scope
    void client.cancelQueries({ predicate })
    client.removeQueries({ predicate })
  }, [client, scope])
  useEffect(() => {
    routeVersion.current += 1
    setRevealed({})
    setDialogField(null)
    setPurpose("")
    setRevealError(null)
    setRevealPending(false)
    setAccountDialogOpen(false)
    setAccountPending(false)
    setAccountError(null)
    setAccountNeedsGuardian(false)
    setTemporaryPassword(null)
    setEvaluationPanelRevoked(false)
    setFoodPreferencesPanelRevoked(false)
    setHousingPanelRevoked(false)
    setDetailAccessRevoked(false)
    for (const timer of Object.values(hideTimers.current)) clearTimeout(timer)
    hideTimers.current = {}
    return () => {
      routeVersion.current += 1
      for (const timer of Object.values(hideTimers.current)) clearTimeout(timer)
      hideTimers.current = {}
    }
  }, [personId])

  function openReveal(field: SensitiveField): void {
    setDialogField(field)
    setPurpose("")
    setRevealError(null)
  }

  async function openAccount(): Promise<void> {
    if (!personId || accountPending) return
    const requestRouteVersion = routeVersion.current
    const requestVersion = ++accountRequestVersion.current
    const isCurrentRequest = () => requestRouteVersion === routeVersion.current && requestVersion === accountRequestVersion.current
    setAccountPending(true)
    setAccountError(null)
    setAccountNeedsGuardian(false)
    try {
      const created = await createAccount(personId)
      if (!isCurrentRequest()) return
      setAccountDialogOpen(false)
      setTemporaryPassword(created.temporary_password)
    } catch (error) {
      if (!isCurrentRequest()) return
      if (error instanceof ProblemDetailsError && hasGuardianError(error)) {
        setAccountError(GUARDIAN_ACCOUNT_MESSAGE)
        setAccountNeedsGuardian(true)
      } else {
        setAccountError(
          error instanceof ProblemDetailsError ? error.problem.detail : "暂时无法开通账号",
        )
      }
    } finally {
      if (isCurrentRequest()) setAccountPending(false)
    }
  }

  async function submitReveal(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!dialogField || revealPending) return
    const field = dialogField
    const requestRouteVersion = routeVersion.current
    setRevealPending(true)
    try {
      const result = await revealSensitive(personId ?? "", field, purpose)
      if (requestRouteVersion !== routeVersion.current) return
      const existingTimer = hideTimers.current[field]
      if (existingTimer) clearTimeout(existingTimer)
      setRevealed((current) => ({ ...current, [field]: result.value }))
      hideTimers.current[field] = setTimeout(() => {
        setRevealed((current) => {
          const next = { ...current }
          delete next[field]
          return next
        })
        delete hideTimers.current[field]
      }, revealDurationMs)
      setPurpose("")
      setDialogField(null)
      setRevealError(null)
    } catch (error) {
      if (requestRouteVersion !== routeVersion.current) return
      setRevealError(
        error instanceof ProblemDetailsError ? error.problem.detail : "敏感资料暂时无法查看",
      )
    } finally {
      if (requestRouteVersion === routeVersion.current) setRevealPending(false)
    }
  }

  if (person.isLoading && !detailAccessRevoked) return <div className="peopleState">正在读取人员档案…</div>
  const transientDetailFailure = person.isError && Boolean(person.data) && (
    person.error instanceof TypeError || person.error instanceof ProblemDetailsError && person.error.problem.status >= 500
  )
  if (detailAccessRevoked || !person.data || person.isError && !transientDetailFailure) {
    return (
      <div className="peopleState" role="alert">
        无法读取该人员档案，可能没有访问权限或资料已不存在。
      </div>
    )
  }

  const detail = person.data
  const canRevealIdentity = permissions.has("people.identity.reveal")
  const canRevealPhone = permissions.has("people.phone.reveal")
  const canUpdate = permissions.has("people.update")
  const assisted = Boolean(detail.assisted_kind)
  const canQueryAssistedEditor = assisted && (canUpdate || scopedPermissions.has("people.update"))
  const canOpenAccount = detail.account_management_allowed === true
  const canQueryFoodProfile =
    permissions.has("food_preferences.read") ||
    scopedPermissions.has("food_preferences.read")
  const canQueryHousing = ["housing.read", "personnel_intake.housing.read"].some((permission) => permissions.has(permission) || scopedPermissions.has(permission))

  return (
    <div className="personDetailPage">
      {transientDetailFailure ? <p role="alert">人员摘要暂时无法刷新，现有草稿未清空。</p> : null}
      <div className="personDetailHeading">
        <div>
          <Link className="backLink" to="/people">
            ← 返回人员名单
          </Link>
          <p className="pageEyebrow">PERSON RECORD</p>
          <h1>{detail.full_name}</h1>
          {detail.nickname ? <p className="personDetailNickname">昵称：{detail.nickname}</p> : null}
          <div className="personDetailMeta" aria-label="人员类型与年龄">
            <span>{personTypeText(detail.person_types, detail.staff_type, detail.roles, detail.assisted_kind)}</span>
            {detail.account_status ? <span className="privacyBadge">{personAccountStatusLabels[detail.account_status]}</span> : null}
            {detail.age_status !== "unknown" && detail.date_of_birth ? <span className={`ageBadge ${detail.age_status}`}>
              {detail.age_status === "adult" ? "成年" : "未成年"}
            </span> : null}
            <span className="personAgeSummary">{ageText(detail.date_of_birth)}</span>
          </div>
        </div>
        <div className="personDetailActions">
          {canOpenAccount ? (
            <button
              className="primaryButton"
              type="button"
              onClick={() => {
                setAccountError(null)
                setAccountNeedsGuardian(false)
                setAccountDialogOpen(true)
              }}
            >
              开通账号
            </button>
          ) : null}
          {canUpdate && !assisted ? (
            <Link className="secondaryButton detailEditLink" to={`/people/${detail.id}/edit`}>
              编辑档案
            </Link>
          ) : null}
        </div>
      </div>

      {canUpdate ? <StaffProfilePanel key={`${accountId}:${detail.id}`} personId={detail.id} accountId={accountId} /> : null}
      <PersonAffiliationsSection key={`affiliations:${scope}:${detail.id}`} detail={detail} />
      {detail.team_allocation ? <TeamAllocationPanel key={`allocation:${accountId}:${detail.id}`} personId={detail.id} projection={detail.team_allocation} /> : null}

      <div className="personDetailGrid">
        <section className="detailCard detailIdentityCard" aria-labelledby="identity-title">
          <div className="detailCardHeader">
            <div>
              <p className="detailCardEyebrow">IDENTITY</p>
              <h2 id="identity-title">实名与联系资料</h2>
            </div>
            <span className="privacyBadge">默认脱敏</span>
          </div>
          <dl className="detailList">
            <div>
              <dt>昵称</dt>
              <dd>{detail.nickname ?? "未设置"}</dd>
            </div>
            <div>
              <dt>证件类型</dt>
              <dd>{documentTypeLabel(detail.document_type)}</dd>
            </div>
            <div>
              <dt>证件号码</dt>
              <dd>
                <span className="maskedValue">
                  {revealed.identity_number ?? detail.identity_number}
                </span>
                {canRevealIdentity ? (
                  <button type="button" onClick={() => openReveal("identity_number")}>
                    {revealed.identity_number ? "重新查看" : "查看完整证件号码"}
                  </button>
                ) : null}
              </dd>
            </div>
            <div>
              <dt>手机号</dt>
              <dd>
                <span className="maskedValue">{revealed.phone ?? detail.phone ?? "未填写"}</span>
                {canRevealPhone && detail.phone ? (
                  <button type="button" onClick={() => openReveal("phone")}>
                    {revealed.phone ? "重新查看" : "查看完整手机号"}
                  </button>
                ) : null}
              </dd>
            </div>
            <div>
              <dt>邮箱</dt>
              <dd>{detail.email ?? "未填写"}</dd>
            </div>
            <div>
              <dt>性别</dt>
              <dd>{detail.sex === "female" ? "女" : detail.sex === "male" ? "男" : "待补"}</dd>
            </div>
            <div>
              <dt>出生日期</dt>
              <dd>{detail.date_of_birth ?? "待补"}</dd>
            </div>
            <div>
              <dt>年龄</dt>
              <dd>
                <span className="detailAgeValue">{ageText(detail.date_of_birth)}</span>
                {detail.age_status !== "unknown" && detail.date_of_birth ? <span
                  className={`ageBadge ${detail.age_status}`}
                  aria-label={detail.age_status === "adult" ? "成年" : "未成年"}
                >
                  {detail.age_status === "adult" ? "成年" : "未成年"}
                </span> : null}
              </dd>
            </div>
          </dl>
        </section>

        <section className="detailCard" aria-labelledby="profile-title">
          <div className="detailCardHeader">
            <div>
              <p className="detailCardEyebrow">PROFILE</p>
              <h2 id="profile-title">业务档案</h2>
            </div>
          </div>
          <dl className="detailList compact">
            <div><dt>队伍归属</dt><dd>{teamProjectionText(detail.team)}</dd></div>
            {detail.student_number ? (
              <div>
                <dt>学员编号</dt>
                <dd>{detail.student_number}</dd>
              </div>
            ) : null}
            {detail.student_status ? (
              <div>
                <dt>学员状态</dt>
                <dd>{detail.student_status === "active" ? "在读" : "待完善"}</dd>
              </div>
            ) : null}
            {detail.staff_type ? (
              <div>
                <dt>工作人员类型</dt>
                <dd>{staffTypeLabels[detail.staff_type]}</dd>
              </div>
            ) : null}
            {detail.visit_reason ? (
              <div>
                <dt>来访原因</dt>
                <dd>{detail.visit_reason}</dd>
              </div>
            ) : null}
            <div>
              <dt>建档时间</dt>
              <dd>{formatDateOnly(detail.created_at)}</dd>
            </div>
          </dl>
        </section>
      </div>

      {canQueryHousing && !housingPanelRevoked ? <PersonHousingPanel key={`housing:${accountId}:${detail.id}`} accountId={accountId} personId={detail.id} onAccessRevoked={() => setHousingPanelRevoked(true)} /> : null}

      {canQueryFoodProfile && !foodPreferencesPanelRevoked ? (
        <div className="personFoodPreferences">
          <FoodPreferencesPanel
            mode="profile"
            personId={detail.id}
            onAccessRevoked={() => setFoodPreferencesPanelRevoked(true)}
          />
        </div>
      ) : null}

      {permissions.has("assets.read") ? <PersonAssetsPanel personId={detail.id} /> : null}

      {permissions.has("esports_assessments.read") || scopedPermissions.has("esports_assessments.read") ? (
        <ModuleErrorBoundary key={`assessment:${accountId}:${personId}`} moduleName="天赋评测报告">
          <Suspense fallback={<p role="status">正在加载天赋评测报告面板…</p>}>
            <AssessmentReportsPanel personId={detail.id} />
          </Suspense>
        </ModuleErrorBoundary>
      ) : null}

      {permissions.has("talent_tests.read") || scopedPermissions.has("talent_tests.read") ? (
        <ModuleErrorBoundary key={`reactionx:${accountId}:${personId}`} moduleName="实体灯报告">
          <Suspense fallback={<p role="status">正在加载实体灯报告面板…</p>}>
            <ReactionxReportsPanel personId={detail.id} />
          </Suspense>
        </ModuleErrorBoundary>
      ) : null}

      {assisted && permissions.has("personnel_intake.review") ? <ModuleErrorBoundary key={`pending-roles:${accountId}:${personId}`} moduleName="待授权角色"><Suspense fallback={<p role="status">正在加载角色审批…</p>}><PendingRoleIntentPanel personId={detail.id} /></Suspense></ModuleErrorBoundary> : null}
      {detail.activation_roles_available === true && auth?.status === "authenticated" && auth.user ? <ModuleErrorBoundary key={`activation-roles:${accountId}:${personId}`} moduleName="激活角色">
        <Suspense fallback={<p role="status">正在加载激活角色面板…</p>}><ActivationRolesPanel personId={detail.id} available={detail.activation_roles_available} /></Suspense>
      </ModuleErrorBoundary> : null}

      {canQueryAssistedEditor ? <>
        <AssistedProfileCorrectionPanel personId={detail.id} />
      </> : null}

      {detail.person_types.includes("student") && permissions.has("people.read") ? <>
        {assisted ? <p className="assistedFormNotice">下方为独立的监护关系授权流程，绑定或结束关系仍按原有权限、监护验证和家庭同意规则处理，不等同于上方本人及监护资料更正。</p> : null}
        <GuardianPanel studentId={detail.id} canManage={canUpdate} />
      </> : null}

      {detail.person_types.includes("student") &&
      permissions.has("evaluation_center.access") &&
      permissions.has("student_game_accounts.manage") &&
      !evaluationPanelRevoked ? (
        <section className="personEvaluationFoundation" aria-label="评价匹配资料">
          <StudentMatchingPanel
            studentId={detail.id}
            onAccessRevoked={() => setEvaluationPanelRevoked(true)}
          />
        </section>
      ) : null}

      {dialogField ? (
        <div className="dialogBackdrop">
          <section
            className="revealDialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="reveal-dialog-title"
          >
            <p className="detailCardEyebrow">SENSITIVE ACCESS</p>
            <h2 id="reveal-dialog-title">{revealLabel(dialogField)}</h2>
            <p>本次查看会记录操作人、对象、用途和时间，请填写真实业务用途。</p>
            <form onSubmit={(event) => void submitReveal(event)}>
              <label htmlFor="reveal-purpose">查看用途</label>
              <textarea
                id="reveal-purpose"
                value={purpose}
                onChange={(event) => setPurpose(event.target.value)}
                minLength={4}
                maxLength={200}
                required
                placeholder="例如：入住资料核对"
              />
              {revealError ? (
                <p className="formError" role="alert">
                  {revealError}
                </p>
              ) : null}
              <div className="dialogActions">
                <button
                  className="secondaryButton"
                  type="button"
                  onClick={() => setDialogField(null)}
                >
                  取消
                </button>
                <button className="primaryButton" type="submit" disabled={revealPending}>
                  {revealPending ? "正在安全读取…" : "确认查看"}
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}

      {accountDialogOpen ? (
        <div className="dialogBackdrop">
          <section
            className="revealDialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="account-confirm-title"
          >
            <p className="detailCardEyebrow">ACCOUNT OPENING</p>
            <h2 id="account-confirm-title">确认开通账号</h2>
            <p>将为 {detail.full_name} 创建登录账号，并要求首次登录修改密码。</p>
            {accountError ? (
              <div className="guardianAccountError" role="alert">
                <span>{accountError}</span>
                {accountNeedsGuardian ? (
                  <Link to={`/people/${detail.id}`}>返回人员档案</Link>
                ) : null}
              </div>
            ) : null}
            <div className="dialogActions">
              <button
                className="secondaryButton"
                type="button"
                onClick={() => {
                  accountRequestVersion.current += 1
                  setAccountPending(false)
                  setAccountDialogOpen(false)
                  setAccountError(null)
                  setAccountNeedsGuardian(false)
                }}
              >
                取消
              </button>
              <button
                className="primaryButton"
                type="button"
                disabled={accountPending}
                onClick={() => void openAccount()}
              >
                {accountPending ? "正在开通…" : "确认开通"}
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {temporaryPassword ? (
        <div className="dialogBackdrop">
          <section
            className="revealDialog oneTimePasswordDialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="person-temporary-password-title"
          >
            <p className="detailCardEyebrow">ONE-TIME SECRET</p>
            <h2 id="person-temporary-password-title">请立即安全交付临时密码</h2>
            <p>关闭后将无法再次查看，如遗失请在账号管理中重置。</p>
            <code className="temporaryPasswordValue">{temporaryPassword}</code>
            <button
              className="primaryButton"
              type="button"
              onClick={() => setTemporaryPassword(null)}
            >
              我已保存并关闭
            </button>
          </section>
        </div>
      ) : null}
    </div>
  )
}

export { missingLabels }
