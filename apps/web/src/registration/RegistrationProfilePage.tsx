import { useCallback, useEffect, useMemo, useState } from "react"
import type { FormEvent } from "react"
import { Link, useLocation } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { PasswordInput } from "../auth/PasswordInput"
import {
  finalizeRegistration,
  getRegistrationMe,
  restoreRegistrationCsrf,
  updateRegistrationProfile,
} from "./api"
import type {
  RegistrationProfileInput,
  RegistrationFinalizeResponse,
  RegistrationStateResponse,
} from "./api"
import "./registration.css"
import { PublicEntryAside } from "./PublicEntryAside"

const CONSENT_VERSION = "2026-08"
const STAFF_TYPES = [
  ["coach", "教练"],
  ["life_teacher", "生活老师"],
  ["customer_service", "客服"],
  ["administration", "行政"],
  ["other", "其他"],
] as const
type StaffType = (typeof STAFF_TYPES)[number][0]

const IDENTITY_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
const IDENTITY_CHECKS = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]
type IdentityIssue = "format" | "birth_date" | "checksum"

function isGuardianInvitationFlow(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false
  return (value as Record<string, unknown>).guardianInvitationFlow === true
}

function identityIssue(identityNumber: string): IdentityIssue | null {
  const normalized = identityNumber.trim().toUpperCase()
  if (!/^\d{17}[\dX]$/.test(normalized)) return "format"

  const year = Number(normalized.slice(6, 10))
  const month = Number(normalized.slice(10, 12))
  const day = Number(normalized.slice(12, 14))
  const parsed = new Date(Date.UTC(year, month - 1, day))
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    return "birth_date"
  }

  const checksumIndex = [...normalized.slice(0, 17)].reduce(
    (sum, digit, index) => sum + Number(digit) * IDENTITY_WEIGHTS[index],
    0,
  ) % 11
  return normalized[17] === IDENTITY_CHECKS[checksumIndex] ? null : "checksum"
}

function identityIssueMessage(issue: IdentityIssue | null): string {
  if (issue === "checksum") return "身份证号码校验码不正确，请检查最后一位"
  if (issue === "birth_date") return "身份证中的出生日期无效，请检查身份证号"
  return "请输入有效的 18 位身份证号码"
}

function deriveIdentity(identityNumber: string): {
  dateOfBirth: string
  sex: "male" | "female"
} | null {
  const normalized = identityNumber.trim().toUpperCase()
  if (identityIssue(normalized) !== null) return null
  const year = Number(normalized.slice(6, 10))
  const month = Number(normalized.slice(10, 12))
  const day = Number(normalized.slice(12, 14))
  const parsed = new Date(Date.UTC(year, month - 1, day))
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    return null
  }
  return {
    dateOfBirth: `${normalized.slice(6, 10)}-${normalized.slice(10, 12)}-${normalized.slice(12, 14)}`,
    sex: Number(normalized[16]) % 2 === 1 ? "male" : "female",
  }
}

function profileLoadError(): string {
  return "注册会话不可用，请重新开始"
}

function profileValidationError(field: string, message: string): string | null {
  const normalizedField = field.split(".").at(-1) ?? field
  const normalizedMessage = message.toLowerCase()

  if (normalizedField === "identity_number" || normalizedField === "date_of_birth") {
    if (message.includes("校验码") || normalizedMessage.includes("checksum")) {
      return identityIssueMessage("checksum")
    }
    if (message.includes("出生") || normalizedMessage.includes("birth")) {
      return identityIssueMessage("birth_date")
    }
    return "身份证号码不符合校验规则，请检查后重试"
  }
  if (normalizedField === "sex") return "性别与身份证信息不一致，请检查身份证号"
  if (normalizedField === "password") {
    return "密码需为 8–16 位，并同时包含大写、小写字母和数字"
  }
  if (normalizedField === "full_name") return "请填写姓名，且长度不要超过 80 个字符"
  if (normalizedField === "consent_version") {
    return "实名资料条款已更新，请刷新页面后重试"
  }
  if (normalizedField === "requested_staff_type") {
    return "请选择申请岗位"
  }
  if (normalizedField === "staff_application_reason") {
    if (message.includes("敏感") || normalizedMessage.includes("sensitive")) {
      return "申请说明包含敏感信息，请删除手机号、邮箱或身份证后重试"
    }
    return "请填写完整的申请说明"
  }
  if (normalizedField === "visit_reason") return "请填写完整的来访说明"
  if (normalizedField === "purpose_details") {
    return "申请说明或岗位信息不完整，请检查后重试"
  }
  if (normalizedField === "body" || normalizedField === "") {
    if (normalizedMessage.includes("required")) {
      return "提交请求未包含实名资料，请重新点击提交；如仍失败请刷新页面"
    }
    return "提交的数据格式与当前页面不一致，请刷新页面后重新填写"
  }
  return null
}

function profileSubmitError(error: unknown): string {
  if (!(error instanceof ProblemDetailsError)) {
    return "资料提交失败，请检查填写内容后重试"
  }
  if (error.problem.status === 401 || error.problem.status === 404) {
    return profileLoadError()
  }
  for (const item of error.problem.errors ?? []) {
    const message = profileValidationError(item.field, item.message)
    if (message) return message
  }
  switch (error.problem.code) {
    case "password_policy_failed":
      return "密码需为 8–16 位，并同时包含大写、小写字母和数字"
    case "registration_action_unavailable":
      return "注册申请已失效或已处理，请重新开始注册"
    case "registration_csrf_invalid":
    case "csrf_invalid":
      return "页面安全校验已过期，请刷新页面后重试"
    case "registration_unavailable":
      return "注册服务暂时不可用，请联系管理员"
    case "registration_profile_conflict":
      return "当前申请与已有注册申请发生冲突，请重新开始注册；如仍失败请联系管理员"
    case "registration_profile_invalid":
      return "岗位或申请说明格式不符合当前规则，请检查后重试"
    case "verification_invalid_or_expired":
      return "验证码无效或已过期，请重新获取验证码"
    case "validation_error":
      return "提交的数据格式与当前页面不一致，请刷新页面后重新填写"
    default:
      return "资料提交失败，请检查填写内容后重试"
  }
}

function passwordPolicyError(password: string): string | null {
  return /^(?=.{8,16}$)(?=.*[A-Z])(?=.*[a-z])(?=.*\d).*$/.test(password)
    ? null
    : "密码需为 8–16 位，并同时包含大写、小写字母和数字"
}

function isRegistrationBoundaryError(error: unknown): boolean {
  return error instanceof ProblemDetailsError &&
    (error.problem.status === 401 || error.problem.status === 404)
}

export function RegistrationProfilePage() {
  const location = useLocation()
  const guardianInvitationFlow = isGuardianInvitationFlow(location.state)
  const [registrationState, setRegistrationState] = useState<RegistrationStateResponse | null>(null)
  const primaryPurpose = registrationState?.primary_purpose ?? null
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [fullName, setFullName] = useState("")
  const [identityNumber, setIdentityNumber] = useState("")
  const [password, setPassword] = useState("")
  const [confirmation, setConfirmation] = useState("")
  const [requestedStaffType, setRequestedStaffType] = useState<StaffType | "">("")
  const [staffReason, setStaffReason] = useState("")
  const [visitReason, setVisitReason] = useState("")
  const [consented, setConsented] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [profileSaved, setProfileSaved] = useState(false)
  const [completion, setCompletion] = useState<"review" | "guardian" | "complete" | null>(null)
  const currentIdentityIssue = useMemo(() => identityIssue(identityNumber), [identityNumber])
  const identity = useMemo(() => deriveIdentity(identityNumber), [identityNumber])

  const clearSensitiveFields = useCallback((): void => {
    setFullName("")
    setIdentityNumber("")
    setPassword("")
    setConfirmation("")
    setRequestedStaffType("")
    setStaffReason("")
    setVisitReason("")
    setConsented(false)
  }, [])

  useEffect(() => {
    let active = true
    void Promise.all([restoreRegistrationCsrf(), getRegistrationMe()])
      .then(([, state]) => {
        if (!active) return
        setRegistrationState(state)
        setLoadError(null)
      })
      .catch(() => {
        if (!active) return
        clearSensitiveFields()
        setRegistrationState(null)
        setLoadError(profileLoadError())
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [clearSensitiveFields])

  function applyFinalization(result: RegistrationFinalizeResponse): void {
    setProfileSaved(false)
    if (result.state === "conflict") setCompletion("review")
    else if (result.state === "pending_guardian") setCompletion("guardian")
    else setCompletion("complete")
  }

  function handleRegistrationSessionBoundary(): void {
    clearSensitiveFields()
    setRegistrationState(null)
    setProfileSaved(false)
    setLoadError(profileLoadError())
  }

  async function retryFinalization(): Promise<void> {
    setSubmitting(true)
    setSubmitError(null)
    try {
      applyFinalization(await finalizeRegistration())
    } catch (caught) {
      if (isRegistrationBoundaryError(caught)) {
        handleRegistrationSessionBoundary()
      } else {
        setProfileSaved(true)
      }
    } finally {
      setSubmitting(false)
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!primaryPurpose || !registrationState) return
    if (!identity || currentIdentityIssue !== null) {
      setSubmitError(identityIssueMessage(currentIdentityIssue))
      return
    }
    if (!fullName.trim()) {
      setSubmitError("请填写姓名")
      return
    }
    const passwordError = passwordPolicyError(password)
    if (passwordError) {
      setSubmitError(passwordError)
      return
    }
    if (password !== confirmation) {
      setSubmitError("两次输入的密码不一致")
      return
    }
    if (!consented) {
      setSubmitError("请先同意实名资料处理条款")
      return
    }
    if (primaryPurpose === "staff_applicant" && !requestedStaffType) {
      setSubmitError("请选择申请岗位")
      return
    }
    if (primaryPurpose === "staff_applicant") {
      const reason = staffReason.trim()
      if (reason.length < 2) {
        setSubmitError("请填写申请说明（至少 2 个字符）")
        return
      }
      if (reason.length > 500) {
        setSubmitError("申请说明不能超过 500 个字符")
        return
      }
    }
    if (primaryPurpose === "visitor") {
      const reason = visitReason.trim()
      if (reason.length < 2) {
        setSubmitError("请填写来访说明（至少 2 个字符）")
        return
      }
      if (reason.length > 200) {
        setSubmitError("来访说明不能超过 200 个字符")
        return
      }
    }

    const purposeDetails: RegistrationProfileInput["purpose_details"] = {
      requested_staff_type:
        primaryPurpose === "staff_applicant" ? requestedStaffType || null : null,
      staff_application_reason:
        primaryPurpose === "staff_applicant" ? staffReason.trim() : null,
      visit_reason: primaryPurpose === "visitor" ? visitReason.trim() : null,
    }
    setSubmitting(true)
    setSubmitError(null)
    try {
      const savedProfile = await updateRegistrationProfile({
        password,
        full_name: fullName.trim(),
        identity_number: identityNumber.trim().toUpperCase(),
        sex: identity.sex,
        date_of_birth: identity.dateOfBirth,
        consent_version: CONSENT_VERSION,
        purpose_details: purposeDetails,
      })
      setRegistrationState(savedProfile)
      clearSensitiveFields()
      setProfileSaved(true)
      try {
        applyFinalization(await finalizeRegistration())
      } catch (caught) {
        if (isRegistrationBoundaryError(caught)) {
          handleRegistrationSessionBoundary()
        } else {
          setProfileSaved(true)
        }
      }
    } catch (caught) {
      if (isRegistrationBoundaryError(caught)) {
        handleRegistrationSessionBoundary()
      } else {
        setSubmitError(profileSubmitError(caught))
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="registrationPage">
      <PublicEntryAside />
      <section className="registrationCard" aria-labelledby="registration-profile-title">
        <div className="registrationStep">步骤 3 / 3</div>
        <p className="registrationEyebrow">REAL-NAME PROFILE</p>
        <h1 id="registration-profile-title">填写实名资料</h1>

        {loading ? <p className="registrationLead" role="status">正在确认注册会话…</p> : null}
        {loadError ? (
          <div className="registrationStatus registrationStatusWarning" role="alert">
            <strong>{loadError}</strong>
            <Link to="/register">重新开始注册</Link>
          </div>
        ) : null}
        {completion === "review" ? (
          <div className="registrationStatus" role="status">
            <strong>资料已提交审核</strong>
            <span>管理员处理后，你可以使用已验证的联系方式登录或找回密码。</span>
            <Link to="/register/status">查看审核状态</Link>
            <Link to="/login">返回登录</Link>
          </div>
        ) : null}
        {completion === "guardian" ? (
          <div className="registrationStatus" role="status">
            <strong>实名资料已保存</strong>
            <span>未成年学员需完成监护人绑定后才能正式开通账号。</span>
            <Link to="/register/guardian">管理监护人邀请</Link>
          </div>
        ) : null}
        {completion === "complete" ? (
          <div className="registrationStatus" role="status">
            <strong>账号注册完成</strong>
            <span>
              {guardianInvitationFlow && primaryPurpose === "guardian"
                ? "请登录后确认监护关系。"
                : "请使用已验证的手机号或邮箱登录。"}
            </span>
            <Link
              to="/login"
              state={
                guardianInvitationFlow && primaryPurpose === "guardian"
                  ? { returnTo: "/guardian-invitation" }
                  : undefined
              }
            >
              {guardianInvitationFlow && primaryPurpose === "guardian"
                ? "登录并确认邀请"
                : "去登录"}
            </Link>
          </div>
        ) : null}

        {profileSaved && !completion ? (
          <div className="registrationStatus registrationStatusWarning" role="status">
            <strong>实名资料已保存，请重试完成注册</strong>
            <span>无需再次填写身份证或密码，系统只会重试账号开通步骤。</span>
            <button
              className="primaryButton"
              type="button"
              onClick={() => void retryFinalization()}
              disabled={submitting}
            >
              {submitting ? "正在重试…" : "重试完成注册"}
            </button>
          </div>
        ) : null}

        {registrationState && !completion && !profileSaved ? (
          <>
            <p className="registrationLead">已验证联系方式</p>
            <p className="registrationMaskedValue">{registrationState.masked_identifier}</p>
            <form className="registrationForm" onSubmit={submit}>
              <div className="registrationFieldGroup">
                <label htmlFor="registration-full-name">姓名</label>
                <input
                  id="registration-full-name"
                  autoComplete="name"
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                  required
                />
              </div>
              <div className="registrationFieldGroup">
                <label htmlFor="registration-identity">身份证</label>
                <input
                  id="registration-identity"
                  autoComplete="off"
                  inputMode="text"
                  minLength={18}
                  maxLength={18}
                  value={identityNumber}
                  onChange={(event) => setIdentityNumber(event.target.value.toUpperCase())}
                  required
                />
                {identity ? (
                  <p className="registrationDerived" aria-live="polite">
                    出生日期：{identity.dateOfBirth} · 性别：{identity.sex === "male" ? "男" : "女"}
                  </p>
                ) : identityNumber.length > 0 ? (
                  <p className="registrationFieldError">
                    {identityIssueMessage(currentIdentityIssue)}
                  </p>
                ) : null}
              </div>
              <div className="registrationFieldGroup">
                <label htmlFor="registration-password">密码</label>
                <PasswordInput
                  id="registration-password"
                  autoComplete="new-password"
                  minLength={8}
                  maxLength={16}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
                <small>8–16 位，并同时包含大写、小写字母和数字。</small>
              </div>
              <div className="registrationFieldGroup">
                <label htmlFor="registration-password-confirmation">确认密码</label>
                <PasswordInput
                  id="registration-password-confirmation"
                  autoComplete="new-password"
                  minLength={8}
                  maxLength={16}
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                  required
                />
              </div>

              {primaryPurpose === "staff_applicant" ? (
                <>
                  <div className="registrationFieldGroup">
                    <label htmlFor="registration-staff-type">申请岗位</label>
                    <select
                      id="registration-staff-type"
                      value={requestedStaffType}
                      onChange={(event) => setRequestedStaffType(event.target.value as StaffType | "")}
                      required
                    >
                      <option value="">请选择岗位</option>
                      {STAFF_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                  </div>
                  <div className="registrationFieldGroup">
                    <label htmlFor="registration-staff-reason">申请说明</label>
                    <textarea
                      id="registration-staff-reason"
                      rows={4}
                      value={staffReason}
                      onChange={(event) => setStaffReason(event.target.value)}
                      required
                    />
                  </div>
                </>
              ) : null}

              {primaryPurpose === "visitor" ? (
                <div className="registrationFieldGroup">
                  <label htmlFor="registration-visit-reason">来访说明</label>
                  <textarea
                    id="registration-visit-reason"
                    rows={4}
                    value={visitReason}
                    onChange={(event) => setVisitReason(event.target.value)}
                    required
                  />
                </div>
              ) : null}

              <label className="registrationConsent">
                <input
                  type="checkbox"
                  checked={consented}
                  onChange={(event) => setConsented(event.target.checked)}
                />
                <span>我已阅读并同意实名资料处理条款（版本 {CONSENT_VERSION}）</span>
              </label>
              {submitError ? <p className="registrationError" role="alert">{submitError}</p> : null}
              <button className="primaryButton registrationSubmit" type="submit" disabled={submitting}>
                {submitting ? "正在提交…" : "提交实名资料"}
              </button>
            </form>
          </>
        ) : null}
      </section>
    </main>
  )
}
