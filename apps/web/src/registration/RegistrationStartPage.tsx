import { useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import {
  getRegistrationChannelSettings,
  issueRegistrationChallenge,
  getGuardianInvitationContext,
  startRegistration,
  startGuardianInvitationRegistration,
} from "./api"
import type {
  RegistrationChannelSettings,
  RegistrationApplicationStarted,
  RegistrationIdentifierKind,
  RegistrationPurpose,
} from "./api"
import "./registration.css"

const PURPOSES: ReadonlyArray<{
  value: RegistrationPurpose
  label: string
  description: string
}> = [
  { value: "student", label: "学员", description: "创建学员账号并完善实名资料" },
  { value: "guardian", label: "监护人", description: "接受邀请并查看被监护人信息" },
  { value: "staff_applicant", label: "工作人员申请", description: "提交岗位申请，等待管理员审核" },
  { value: "visitor", label: "临时访客", description: "登记来访用途和实名信息" },
]

type PendingRegistration = {
  primaryPurpose: RegistrationPurpose
  identifierKind: RegistrationIdentifierKind
  identifier: string
  referralCode: string
  application: RegistrationApplicationStarted
}

function startErrorMessage(error: unknown): string {
  if (
    error instanceof ProblemDetailsError &&
    error.problem.status === 429 &&
    error.problem.code === "registration_application_pending"
  ) {
    return error.problem.detail
  }
  if (error instanceof ProblemDetailsError && error.problem.status === 429) {
    return "验证码请求过于频繁，请稍后重试"
  }
  if (
    error instanceof ProblemDetailsError &&
    error.problem.code === "registration_phone_disabled"
  ) {
    return "当前暂不支持手机号申请账号，请使用邮箱注册"
  }
  if (
    error instanceof ProblemDetailsError &&
    error.problem.code === "registration_email_disabled"
  ) {
    return "当前暂不支持邮箱申请账号，请联系管理员"
  }
  if (
    error instanceof ProblemDetailsError &&
    ["referral_code_invalid", "referral_code_not_allowed"].includes(error.problem.code)
  ) {
    return error.problem.detail
  }
  return "暂时无法开始注册，请检查联系方式后重试"
}

function firstEnabledIdentifier(
  settings: RegistrationChannelSettings | null,
): RegistrationIdentifierKind | null {
  if (!settings) return null
  if (settings.email_enabled) return "email"
  if (settings.phone_enabled) return "phone"
  return null
}

function identifierEnabled(
  settings: RegistrationChannelSettings | null,
  kind: RegistrationIdentifierKind | null,
): boolean {
  return Boolean(
    settings &&
      kind &&
      (kind === "email" ? settings.email_enabled : settings.phone_enabled),
  )
}

export function RegistrationStartPage({
  guardianInvitation = false,
}: {
  guardianInvitation?: boolean
}) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const invitedCode = guardianInvitation
    ? ""
    : (searchParams.get("invite") ?? "").trim().toUpperCase()
  const [primaryPurpose, setPrimaryPurpose] = useState<RegistrationPurpose | null>(
    guardianInvitation ? "guardian" : invitedCode ? "student" : null,
  )
  const [identifierKind, setIdentifierKind] = useState<RegistrationIdentifierKind | null>(null)
  const [identifier, setIdentifier] = useState("")
  const [referralCode, setReferralCode] = useState(invitedCode)
  const [channelSettings, setChannelSettings] = useState<RegistrationChannelSettings | null>(null)
  const [channelLoading, setChannelLoading] = useState(!guardianInvitation)
  const [channelError, setChannelError] = useState<string | null>(null)
  const [invitedMaskedIdentifier, setInvitedMaskedIdentifier] = useState<string | null>(null)
  const [invitedRelationship, setInvitedRelationship] = useState<string | null>(null)
  const [invitationLoading, setInvitationLoading] = useState(guardianInvitation)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pendingRegistrationRef = useRef<PendingRegistration | null>(null)
  const selectedPurpose: RegistrationPurpose | null = invitedCode ? "student" : primaryPurpose
  const effectiveReferralCode = invitedCode || referralCode.trim()

  useEffect(() => {
    if (guardianInvitation) return
    let active = true
    setChannelLoading(true)
    setChannelError(null)
    void getRegistrationChannelSettings()
      .then((settings) => {
        if (!active) return
        setChannelSettings(settings)
      })
      .catch(() => {
        if (!active) return
        setChannelSettings(null)
        setChannelError("暂时无法读取开放的注册方式，请稍后重试")
      })
      .finally(() => {
        if (active) setChannelLoading(false)
      })
    return () => {
      active = false
    }
  }, [guardianInvitation])

  useEffect(() => {
    if (guardianInvitation || !primaryPurpose || !channelSettings) return
    if (!identifierEnabled(channelSettings, identifierKind)) {
      setIdentifierKind(firstEnabledIdentifier(channelSettings))
      setIdentifier("")
    }
  }, [channelSettings, guardianInvitation, identifierKind, primaryPurpose])

  useEffect(() => {
    if (!guardianInvitation) return
    let active = true
    void getGuardianInvitationContext()
      .then((context) => {
        if (!active) return
        setInvitedMaskedIdentifier(context.masked_identifier)
        setInvitedRelationship(context.relationship)
        setError(null)
      })
      .catch(() => {
        if (!active) return
        setInvitedMaskedIdentifier(null)
        setInvitedRelationship(null)
        setError("监护邀请不可用，请重新打开收到的邀请链接")
      })
      .finally(() => {
        if (active) setInvitationLoading(false)
      })
    return () => {
      active = false
    }
  }, [guardianInvitation])

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (
      guardianInvitation
        ? !invitedMaskedIdentifier
        : !selectedPurpose ||
          !identifierEnabled(channelSettings, identifierKind) ||
          identifier.trim().length === 0
    ) {
      setError("请选择身份、开放的申请方式并填写联系方式")
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      let application: RegistrationApplicationStarted
      if (guardianInvitation) {
        application = await startGuardianInvitationRegistration()
      } else {
        const registrationPurpose = selectedPurpose as RegistrationPurpose
        const registrationIdentifierKind = identifierKind as RegistrationIdentifierKind
        const registrationIdentifier = identifier.trim()
        const registrationReferralCode =
          registrationPurpose === "student" ? effectiveReferralCode : ""
        const cached = pendingRegistrationRef.current
        const cacheMatches =
          cached &&
          cached.primaryPurpose === registrationPurpose &&
          cached.identifierKind === registrationIdentifierKind &&
          cached.identifier === registrationIdentifier &&
          cached.referralCode === registrationReferralCode
        const cacheExpiresAt = cached ? Date.parse(cached.application.expires_at) : NaN
        const cacheIsUnexpired =
          cached !== null && Number.isFinite(cacheExpiresAt) && cacheExpiresAt > Date.now()
        if (cacheMatches && cacheIsUnexpired) {
          application = cached.application
        } else {
          if (!cacheIsUnexpired) pendingRegistrationRef.current = null
          application = await startRegistration({
            primary_purpose: registrationPurpose,
            identifier_kind: registrationIdentifierKind,
            identifier: registrationIdentifier,
            ...(registrationPurpose === "student" && registrationReferralCode
              ? { referral_code: registrationReferralCode }
              : {}),
          })
          pendingRegistrationRef.current = {
            primaryPurpose: registrationPurpose,
            identifierKind: registrationIdentifierKind,
            identifier: registrationIdentifier,
            referralCode: registrationReferralCode,
            application,
          }
        }
      }
      const challenge = await issueRegistrationChallenge(application.application_id)
      pendingRegistrationRef.current = null
      navigate("/register/verify", {
        state: {
          applicationId: application.application_id,
          challengeId: challenge.challenge_id,
          maskedDestination: challenge.masked_destination,
          retryAfterSeconds: challenge.retry_after_seconds,
          ...(guardianInvitation ? { guardianInvitationFlow: true } : {}),
        },
      })
    } catch (caught) {
      setError(startErrorMessage(caught))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="registrationPage">
      <section className="registrationCard registrationCardWide" aria-labelledby="registration-start-title">
        <div className="registrationBrand" aria-hidden="true">竞</div>
        <p className="registrationEyebrow">SELF REGISTRATION</p>
        <h1 id="registration-start-title">创建账号</h1>
        <p className="registrationLead">
          {guardianInvitation
            ? "使用收到邀请的联系方式创建监护人账号。"
            : "先选择账号用途，再选择已开放的手机号或邮箱。"}
        </p>

        <form className="registrationForm" onSubmit={submit}>
          {guardianInvitation ? (
            <div className="registrationStatus" role="status">
              <strong>账号用途已锁定为监护人</strong>
              {invitationLoading ? <span>正在确认邀请…</span> : null}
              {invitedMaskedIdentifier ? (
                <>
                  <span>验证码将发送至 {invitedMaskedIdentifier}</span>
                  {invitedRelationship ? <span>邀请关系：{invitedRelationship}</span> : null}
                </>
              ) : null}
            </div>
          ) : invitedCode ? (
            <div className="registrationStatus" role="status">
              <strong>已通过邀请二维码进入</strong>
              <span>账号用途已锁定为学员，邀请码将在注册完成后自动绑定。</span>
              <span>邀请码：{invitedCode}</span>
            </div>
          ) : (
            <fieldset className="registrationFieldset">
              <legend>选择你的身份</legend>
              <div className="registrationChoiceGrid">
                {PURPOSES.map((purpose) => (
                  <label className="registrationPurpose" key={purpose.value}>
                    <input
                      type="radio"
                      name="primary-purpose"
                      value={purpose.value}
                      checked={primaryPurpose === purpose.value}
                      onChange={() => {
                        setPrimaryPurpose(purpose.value)
                        setIdentifierKind(firstEnabledIdentifier(channelSettings))
                        setIdentifier("")
                        setError(null)
                      }}
                    />
                    <span>
                      <strong>{purpose.label}</strong>
                      <small>{purpose.description}</small>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          {!guardianInvitation && selectedPurpose ? (
            <>
              {channelLoading ? (
                <p className="registrationStatus" role="status">正在读取开放的注册方式…</p>
              ) : null}
              {channelError ? (
                <p className="registrationError" role="alert">{channelError}</p>
              ) : null}
              {!channelLoading && !channelError && channelSettings ? (
                channelSettings.email_enabled || channelSettings.phone_enabled ? (
                  <>
                    <fieldset className="registrationFieldset">
                      <legend>申请方式</legend>
                      <div className="registrationInlineChoices">
                        {channelSettings.email_enabled ? (
                          <label>
                            <input
                              type="radio"
                              name="registration-identifier-kind"
                              value="email"
                              checked={identifierKind === "email"}
                              onChange={() => {
                                setIdentifierKind("email")
                                setIdentifier("")
                                setError(null)
                              }}
                            />
                            邮箱
                          </label>
                        ) : null}
                        {channelSettings.phone_enabled ? (
                          <label>
                            <input
                              type="radio"
                              name="registration-identifier-kind"
                              value="phone"
                              checked={identifierKind === "phone"}
                              onChange={() => {
                                setIdentifierKind("phone")
                                setIdentifier("")
                                setError(null)
                              }}
                            />
                            手机号
                          </label>
                        ) : null}
                      </div>
                    </fieldset>
                    {identifierKind ? (
                      <div className="registrationFieldGroup">
                        <div className="registrationFieldLabelRow">
                          <label htmlFor="registration-identifier">
                            {identifierKind === "phone" ? "手机号" : "邮箱"}
                          </label>
                        </div>
                        <input
                          id="registration-identifier"
                          type={identifierKind === "phone" ? "tel" : "email"}
                          inputMode={identifierKind === "phone" ? "tel" : "email"}
                          autoComplete={identifierKind === "phone" ? "tel" : "email"}
                          value={identifier}
                          onChange={(event) => setIdentifier(event.target.value)}
                          placeholder={identifierKind === "phone" ? "请输入手机号" : "请输入邮箱地址"}
                          required
                        />
                      </div>
                    ) : null}
                  </>
                ) : (
                  <div className="registrationStatus registrationStatusWarning" role="status">
                    当前暂未开放手机号或邮箱申请账号，请联系管理员。
                  </div>
                )
              ) : null}
            </>
          ) : null}

          {!guardianInvitation && selectedPurpose === "student" ? (
            <div className="registrationFieldGroup">
              <label htmlFor="registration-referral-code">
                邀请人或邀请机构代码{invitedCode ? "" : "（选填）"}
              </label>
              <input
                id="registration-referral-code"
                type="text"
                autoCapitalize="characters"
                autoComplete="off"
                maxLength={24}
                value={effectiveReferralCode}
                readOnly={Boolean(invitedCode)}
                onChange={(event) => {
                  if (invitedCode) return
                  setReferralCode(event.target.value.toUpperCase())
                  setError(null)
                }}
                placeholder="请输入邀请码"
              />
              <small>
                {invitedCode
                  ? "邀请码已由扫码链接锁定，不可修改。"
                  : "通过邀请链接打开时会自动填写。"}
              </small>
            </div>
          ) : null}

          {error ? <p className="registrationError" role="alert">{error}</p> : null}
          <button
            className="primaryButton registrationSubmit"
            type="submit"
            disabled={
              submitting || invitationLoading || !selectedPurpose ||
              (guardianInvitation
                ? !invitedMaskedIdentifier
                : channelLoading ||
                  Boolean(channelError) ||
                  !identifierEnabled(channelSettings, identifierKind) ||
                  identifier.trim().length === 0)
            }
          >
            {submitting
              ? "正在发送…"
              : guardianInvitation
                ? "向受邀联系方式发送验证码"
                : "发送验证码"}
          </button>
        </form>

        <p className="registrationFooter">
          已有账号？
          <Link
            to="/login"
            state={guardianInvitation ? { returnTo: "/guardian-invitation" } : undefined}
          >
            返回登录
          </Link>
        </p>
      </section>
    </main>
  )
}
