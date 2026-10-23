import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { Link, useParams } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { StudentMatchingPanel } from "../evaluations/StudentMatchingPanel"
import { createAccount } from "../settings/api"
import { GuardianPanel } from "./GuardianPanel"
import { getPerson, revealSensitive } from "./api"
import type { PersonType } from "./api"
import "./people.css"

type SensitiveField = "phone" | "identity_number"
export const SENSITIVE_REVEAL_DURATION_MS = 60_000
const GUARDIAN_ACCOUNT_MESSAGE = "请先绑定已成年、已实名且账号启用的监护人"

const typeLabels: Record<PersonType, string> = {
  student: "学员",
  guardian: "监护人",
  staff: "工作人员",
  visitor: "访客",
}

const missingLabels: Record<string, string> = {
  full_name: "姓名",
  identity_number: "身份证",
  sex: "性别",
  date_of_birth: "出生日期",
  phone: "手机号",
  email: "邮箱",
  guardian: "有效实名监护人",
}

function revealLabel(field: SensitiveField): string {
  return field === "identity_number" ? "查看完整身份证" : "查看完整手机号"
}

function hasGuardianError(error: ProblemDetailsError): boolean {
  return error.problem.errors?.some((item) => item.field === "guardian") === true
}

export function PersonDetailPage({
  permissions = new Set<string>(),
  revealDurationMs = SENSITIVE_REVEAL_DURATION_MS,
}: {
  permissions?: ReadonlySet<string>
  revealDurationMs?: number
}) {
  const { personId } = useParams()
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
  const hideTimers = useRef<
    Partial<Record<SensitiveField, ReturnType<typeof setTimeout>>>
  >({})
  const routeVersion = useRef(0)
  const person = useQuery({
    queryKey: ["person", personId],
    queryFn: () => getPerson(personId ?? ""),
    enabled: Boolean(personId),
  })
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
    setAccountPending(true)
    setAccountError(null)
    setAccountNeedsGuardian(false)
    try {
      const created = await createAccount(personId)
      setAccountDialogOpen(false)
      setTemporaryPassword(created.temporary_password)
    } catch (error) {
      if (error instanceof ProblemDetailsError && hasGuardianError(error)) {
        setAccountError(GUARDIAN_ACCOUNT_MESSAGE)
        setAccountNeedsGuardian(true)
      } else {
        setAccountError(
          error instanceof ProblemDetailsError ? error.problem.detail : "暂时无法开通账号",
        )
      }
    } finally {
      setAccountPending(false)
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

  if (person.isLoading) return <div className="peopleState">正在读取人员档案…</div>
  if (person.isError || !person.data) {
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
  const canOpenAccount = detail.account_management_allowed === true

  return (
    <div className="personDetailPage">
      <div className="personDetailHeading">
        <div>
          <Link className="backLink" to="/people">
            ← 返回人员名单
          </Link>
          <p className="pageEyebrow">PERSON RECORD</p>
          <h1>{detail.full_name}</h1>
          <p>{detail.person_types.map((type) => typeLabels[type]).join(" · ")}</p>
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
          {canUpdate ? (
            <Link className="secondaryButton detailEditLink" to={`/people/${detail.id}/edit`}>
              编辑档案
            </Link>
          ) : null}
        </div>
      </div>

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
              <dt>身份证</dt>
              <dd>
                <span className="maskedValue">
                  {revealed.identity_number ?? detail.identity_number}
                </span>
                {canRevealIdentity ? (
                  <button type="button" onClick={() => openReveal("identity_number")}>
                    {revealed.identity_number ? "重新查看" : "查看完整身份证"}
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
              <dd>{detail.sex === "female" ? "女" : "男"}</dd>
            </div>
            <div>
              <dt>出生日期</dt>
              <dd>{detail.date_of_birth}</dd>
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
                <dd>{detail.staff_type}</dd>
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
              <dd>{new Date(detail.created_at).toLocaleDateString("zh-CN")}</dd>
            </div>
          </dl>
        </section>
      </div>

      {detail.person_types.includes("student") && permissions.has("people.read") ? (
        <GuardianPanel studentId={detail.id} canManage={canUpdate} />
      ) : null}

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
