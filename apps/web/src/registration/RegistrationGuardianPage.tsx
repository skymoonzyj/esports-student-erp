import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import {
  confirmRegistrationTermsConsent,
  createMinorGuardianInvitation,
  getRegistrationTermsConsent,
  issueRegistrationContactChallenge,
  listMinorGuardianInvitations,
  resendMinorGuardianInvitation,
  restoreRegistrationCsrf,
  revokeMinorGuardianInvitation,
  verifyRegistrationContactChange,
} from "./api"
import type {
  GuardianInvitation,
  GuardianInvitationInput,
  RegistrationIdentifierKind,
  RegistrationTermsConsentStatus,
} from "./api"
import { registrationKeys } from "./keys"
import "./registration.css"

const STATUS_LABELS: Record<GuardianInvitation["status"], string> = {
  pending: "等待监护人确认",
  accepted: "监护关系已确认",
  revoked: "邀请已撤销",
  expired: "邀请已过期",
}

function guardianError(error: unknown): string {
  if (error instanceof ProblemDetailsError && error.problem.status === 429) {
    return "邀请发送过于频繁，请稍后重试"
  }
  return "监护人邀请操作暂时不可用，请稍后重试"
}

export function RegistrationGuardianPage() {
  const queryClient = useQueryClient()
  const key = registrationKeys.minorGuardianInvitations()
  const termsKey = registrationKeys.registrationTermsConsent()
  const invitations = useQuery({
    queryKey: key,
    queryFn: async () => {
      await restoreRegistrationCsrf()
      return listMinorGuardianInvitations()
    },
    retry: false,
    staleTime: 0,
  })
  const terms = useQuery({
    queryKey: termsKey,
    queryFn: getRegistrationTermsConsent,
    enabled: invitations.isSuccess,
    retry: false,
    staleTime: 0,
  })
  const [identifierKind, setIdentifierKind] = useState<RegistrationIdentifierKind>("phone")
  const [identifier, setIdentifier] = useState("")
  const [relationship, setRelationship] = useState("")
  const [isPrimary, setIsPrimary] = useState(false)
  const [pendingAction, setPendingAction] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [contactKind, setContactKind] = useState<RegistrationIdentifierKind>("phone")
  const [contactIdentifier, setContactIdentifier] = useState("")
  const [contactChallenge, setContactChallenge] = useState<{
    id: string
    maskedDestination: string
  } | null>(null)
  const [contactCode, setContactCode] = useState("")
  const [contactChanged, setContactChanged] = useState<string | null>(null)
  const [termsConsented, setTermsConsented] = useState(false)
  const [termsConfirmed, setTermsConfirmed] = useState(false)

  useEffect(() => {
    if (!invitations.isError) return
    setIdentifier("")
    setRelationship("")
    setIsPrimary(false)
    setContactIdentifier("")
    setContactCode("")
    setContactChallenge(null)
    setContactChanged(null)
  }, [invitations.isError])

  function replaceInvitation(previousId: string | null, next: GuardianInvitation): void {
    queryClient.setQueryData<GuardianInvitation[]>(key, (current = []) => {
      if (previousId === null) return [...current, next]
      return current.map((item) => item.id === previousId ? next : item)
    })
  }

  async function create(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const input: GuardianInvitationInput = {
      identifier_kind: identifierKind,
      identifier: identifier.trim(),
      relationship: relationship.trim(),
      is_primary: isPrimary,
    }
    setIdentifier("")
    setPendingAction("create")
    setError(null)
    try {
      replaceInvitation(null, await createMinorGuardianInvitation(input))
      setRelationship("")
      setIsPrimary(false)
    } catch (caught) {
      setError(guardianError(caught))
    } finally {
      setPendingAction(null)
    }
  }

  async function resend(invitation: GuardianInvitation): Promise<void> {
    setPendingAction(invitation.id)
    setError(null)
    try {
      replaceInvitation(
        invitation.id,
        await resendMinorGuardianInvitation(invitation.id),
      )
    } catch (caught) {
      setError(guardianError(caught))
    } finally {
      setPendingAction(null)
    }
  }

  async function revoke(invitation: GuardianInvitation): Promise<void> {
    setPendingAction(invitation.id)
    setError(null)
    try {
      replaceInvitation(
        invitation.id,
        await revokeMinorGuardianInvitation(invitation.id),
      )
    } catch (caught) {
      setError(guardianError(caught))
    } finally {
      setPendingAction(null)
    }
  }

  async function sendContactCode(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const normalizedIdentifier = contactIdentifier.trim()
    if (!normalizedIdentifier) return
    setPendingAction("contact-challenge")
    setError(null)
    setContactChanged(null)
    try {
      const challenge = await issueRegistrationContactChallenge({
        identifier_kind: contactKind,
        identifier: normalizedIdentifier,
      })
      setContactChallenge({
        id: challenge.challenge_id,
        maskedDestination: challenge.masked_destination,
      })
      setContactCode("")
    } catch (caught) {
      setError(guardianError(caught))
    } finally {
      setPendingAction(null)
    }
  }

  async function confirmContactChange(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!contactChallenge || !/^\d{6}$/.test(contactCode)) return
    setPendingAction("contact-verify")
    setError(null)
    try {
      const changed = await verifyRegistrationContactChange({
        challenge_id: contactChallenge.id,
        code: contactCode,
        identifier_kind: contactKind,
        identifier: contactIdentifier.trim(),
      })
      setContactChanged(changed.masked_identifier)
      setContactIdentifier("")
      setContactCode("")
      setContactChallenge(null)
    } catch (caught) {
      setError(guardianError(caught))
    } finally {
      setPendingAction(null)
    }
  }

  async function confirmCurrentTerms(): Promise<void> {
    if (!terms.data?.reconfirmation_required || !termsConsented) return
    setPendingAction("registration-terms")
    setError(null)
    try {
      const confirmed = await confirmRegistrationTermsConsent(
        terms.data.current_version,
      )
      queryClient.setQueryData<RegistrationTermsConsentStatus>(termsKey, confirmed)
      setTermsConsented(false)
      setTermsConfirmed(true)
    } catch (caught) {
      setError(guardianError(caught))
    } finally {
      setPendingAction(null)
    }
  }

  return (
    <main className="registrationPage">
      <section className="registrationCard registrationCardWide" aria-labelledby="guardian-completion-title">
        <p className="registrationEyebrow">GUARDIAN COMPLETION</p>
        <h1 id="guardian-completion-title">监护人完善区</h1>
        <p className="registrationLead">
          未成年学员需至少一名已成年、已实名的监护人独立登录并确认关系后，才能正式开通账号。
        </p>

        {terms.data?.reconfirmation_required ? (
          <section className="registrationStatus registrationStatusWarning" aria-labelledby="registration-terms-title">
            <strong id="registration-terms-title">注册条款已更新</strong>
            <span>正式开通前，需要由学员账号重新确认当前版本 {terms.data.current_version}。</span>
            <label className="registrationConsent">
              <input
                type="checkbox"
                checked={termsConsented}
                onChange={(event) => setTermsConsented(event.target.checked)}
              />
              <span>本人已重新阅读并同意实名资料处理条款</span>
            </label>
            <button
              type="button"
              className="primaryButton"
              disabled={!termsConsented || pendingAction !== null}
              onClick={() => void confirmCurrentTerms()}
            >
              {pendingAction === "registration-terms"
                ? "正在确认…"
                : "确认新版注册条款"}
            </button>
          </section>
        ) : null}
        {termsConfirmed ? (
          <p className="registrationStatus" role="status">新版注册条款已确认</p>
        ) : null}
        {terms.isError ? (
          <p className="registrationError" role="alert">
            暂时无法确认注册条款状态，请刷新后重试。
          </p>
        ) : null}

        {invitations.isPending ? <p role="status">正在读取邀请…</p> : null}
        {invitations.isError ? (
          <div className="registrationStatus registrationStatusWarning" role="alert">
            <strong>注册完善会话不可用</strong>
            <span>请重新开始注册，或联系管理员确认申请状态。</span>
            <Link to="/register">重新开始</Link>
          </div>
        ) : null}

        {invitations.data && !invitations.isError ? (
          <div className="guardianWorkspace">
            <section aria-labelledby="guardian-invitations-title">
              <div className="registrationSectionHeading">
                <h2 id="guardian-invitations-title">已发送邀请</h2>
                <span>{invitations.data.length} 条</span>
              </div>
              {invitations.data.length === 0 ? (
                <p className="registrationEmpty">还没有监护人邀请。</p>
              ) : (
                <ul className="guardianInvitationList">
                  {invitations.data.map((invitation) => (
                    <li key={invitation.id}>
                      <div>
                        <strong>{invitation.masked_identifier}</strong>
                        <span>{invitation.relationship} · {invitation.is_primary ? "主要监护人" : "其他监护人"}</span>
                        <small>{STATUS_LABELS[invitation.status]}</small>
                      </div>
                      {invitation.status === "pending" ? (
                        <div className="guardianInvitationActions">
                          <button
                            type="button"
                            aria-label={`重新发送给 ${invitation.masked_identifier}`}
                            onClick={() => void resend(invitation)}
                            disabled={pendingAction !== null}
                          >
                            重新发送
                          </button>
                          <button
                            type="button"
                            aria-label={`撤销 ${invitation.masked_identifier}`}
                            onClick={() => void revoke(invitation)}
                            disabled={pendingAction !== null}
                          >
                            撤销
                          </button>
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <form className="registrationForm guardianInviteForm" onSubmit={create}>
              <h2>邀请监护人</h2>
              <div className="registrationFieldGroup">
                <label htmlFor="guardian-identifier-kind">邀请方式</label>
                <select
                  id="guardian-identifier-kind"
                  value={identifierKind}
                  onChange={(event) =>
                    setIdentifierKind(event.target.value as RegistrationIdentifierKind)}
                >
                  <option value="phone">手机号</option>
                  <option value="email">邮箱</option>
                </select>
              </div>
              <div className="registrationFieldGroup">
                <label htmlFor="guardian-identifier">监护人手机号或邮箱</label>
                <input
                  id="guardian-identifier"
                  type={identifierKind === "phone" ? "tel" : "email"}
                  value={identifier}
                  onChange={(event) => setIdentifier(event.target.value)}
                  required
                />
              </div>
              <div className="registrationFieldGroup">
                <label htmlFor="guardian-relationship">与学员关系</label>
                <input
                  id="guardian-relationship"
                  value={relationship}
                  onChange={(event) => setRelationship(event.target.value)}
                  minLength={2}
                  maxLength={40}
                  required
                />
              </div>
              <label className="registrationConsent">
                <input
                  type="checkbox"
                  checked={isPrimary}
                  onChange={(event) => setIsPrimary(event.target.checked)}
                />
                <span>设为主要监护人</span>
              </label>
              {error ? <p className="registrationError" role="alert">{error}</p> : null}
              <button
                className="primaryButton registrationSubmit"
                type="submit"
                disabled={pendingAction !== null}
              >
                {pendingAction === "create" ? "正在发送…" : "发送监护人邀请"}
              </button>
            </form>
          </div>
        ) : null}

        {invitations.data && !invitations.isError ? (
          <section className="registrationContactChange" aria-labelledby="registration-contact-title">
            <div className="registrationSectionHeading">
              <div>
                <h2 id="registration-contact-title">更换本人联系方式</h2>
                <p>新手机号或邮箱验证成功后，旧联系方式才会被替换。</p>
              </div>
            </div>
            {contactChanged ? (
              <p className="registrationStatus" role="status">
                本人联系方式已更新为 {contactChanged}
              </p>
            ) : null}
            {!contactChallenge ? (
              <form className="registrationForm" onSubmit={sendContactCode}>
                <div className="registrationFieldGroup">
                  <label htmlFor="self-contact-kind">本人联系方式类型</label>
                  <select
                    id="self-contact-kind"
                    value={contactKind}
                    onChange={(event) => {
                      setContactKind(event.target.value as RegistrationIdentifierKind)
                      setContactIdentifier("")
                    }}
                  >
                    <option value="phone">手机号</option>
                    <option value="email">邮箱</option>
                  </select>
                </div>
                <div className="registrationFieldGroup">
                  <label htmlFor="self-contact-identifier">新的本人手机号或邮箱</label>
                  <input
                    id="self-contact-identifier"
                    type={contactKind === "phone" ? "tel" : "email"}
                    value={contactIdentifier}
                    onChange={(event) => setContactIdentifier(event.target.value)}
                    required
                  />
                </div>
                <button
                  type="submit"
                  className="registrationSecondaryButton"
                  disabled={pendingAction !== null}
                >
                  {pendingAction === "contact-challenge" ? "正在发送…" : "发送本人验证码"}
                </button>
              </form>
            ) : (
              <form className="registrationForm" onSubmit={confirmContactChange}>
                <p className="registrationMaskedValue">{contactChallenge.maskedDestination}</p>
                <div className="registrationFieldGroup">
                  <label htmlFor="self-contact-code">本人的验证码</label>
                  <input
                    id="self-contact-code"
                    inputMode="numeric"
                    pattern="[0-9]{6}"
                    maxLength={6}
                    value={contactCode}
                    onChange={(event) =>
                      setContactCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                    required
                  />
                </div>
                <div className="registrationReviewActions">
                  <button
                    type="button"
                    onClick={() => {
                      setContactChallenge(null)
                      setContactCode("")
                    }}
                    disabled={pendingAction !== null}
                  >
                    取消
                  </button>
                  <button
                    type="submit"
                    className="primaryButton"
                    disabled={pendingAction !== null || !/^\d{6}$/.test(contactCode)}
                  >
                    {pendingAction === "contact-verify" ? "正在验证…" : "确认更换联系方式"}
                  </button>
                </div>
              </form>
            )}
          </section>
        ) : null}

        <p className="registrationFooter"><Link to="/register/status">查看注册状态</Link></p>
      </section>
    </main>
  )
}
