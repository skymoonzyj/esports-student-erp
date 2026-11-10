import { useCallback, useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"

import { useAuth } from "../auth/AuthProvider"
import {
  acceptHandedOffGuardianInvitation,
  getGuardianInvitationAcceptance,
  getGuardianInvitationContext,
  handoffGuardianInvitation,
} from "./api"
import type { GuardianInvitationContext } from "./api"
import "./registration.css"
import { PublicEntryAside } from "./PublicEntryAside"

const INVITATION_PATH = "/guardian-invitation"

class GuardianInvitationHandoffFailed extends Error {
  constructor() {
    super("guardian invitation handoff failed")
    this.name = "GuardianInvitationHandoffFailed"
  }
}

export function GuardianInvitationPage() {
  const { status } = useAuth()
  const [context, setContext] = useState<GuardianInvitationContext | null>(null)
  const [loading, setLoading] = useState(true)
  const [consented, setConsented] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [accepted, setAccepted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const invitationLoad = useRef<Promise<GuardianInvitationContext> | null>(null)
  const pendingHandoffToken = useRef<string | null>(null)
  const [retryVersion, setRetryVersion] = useState(0)
  const [handoffRetryable, setHandoffRetryable] = useState(false)

  const recoverAcceptance = useCallback(async (): Promise<boolean> => {
    try {
      await getGuardianInvitationAcceptance()
    } catch {
      return false
    }
    setContext(null)
    setConsented(false)
    setAccepted(true)
    setError(null)
    return true
  }, [])

  useEffect(() => {
    let active = true
    if (handoffRetryable) {
      return () => {
        active = false
      }
    }
    if (invitationLoad.current === null) {
      if (pendingHandoffToken.current === null) {
        const fragmentToken = window.location.hash.startsWith("#")
          ? window.location.hash.slice(1).trim()
          : ""
        pendingHandoffToken.current = fragmentToken || null
      }
      if (pendingHandoffToken.current) {
        window.history.replaceState(
          window.history.state,
          "",
          `${window.location.pathname}${window.location.search}`,
        )
      }
      const token = pendingHandoffToken.current
      invitationLoad.current = (async () => {
        if (token) {
          try {
            await handoffGuardianInvitation(token)
          } catch {
            throw new GuardianInvitationHandoffFailed()
          }
          pendingHandoffToken.current = null
        }
        return getGuardianInvitationContext()
      })()
    }
    const request = invitationLoad.current
    void request
      .then((invitation) => {
        if (!active) return
        setContext(invitation)
        setError(null)
      })
      .catch(async (reason: unknown) => {
        if (!active) return
        if (reason instanceof GuardianInvitationHandoffFailed) {
          setContext(null)
          setHandoffRetryable(true)
          setError("暂时无法确认这份监护邀请，可以重试")
          return
        }
        if (status === "authenticated" && await recoverAcceptance()) return
        setContext(null)
        setError("监护邀请不可用，请重新打开收到的邀请链接")
      })
      .finally(() => {
        if (!active) return
        invitationLoad.current = null
        setLoading(false)
      })
    return () => {
      active = false
    }
  }, [handoffRetryable, recoverAcceptance, retryVersion, status])

  async function acceptInvitation(): Promise<void> {
    if (!context || !consented || status !== "authenticated") return
    setSubmitting(true)
    setError(null)
    try {
      await acceptHandedOffGuardianInvitation(context.consent_version)
      setContext(null)
      setConsented(false)
      setAccepted(true)
    } catch {
      if (!(await recoverAcceptance())) {
        setError("暂时无法确认监护关系，可以直接重试")
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="registrationPage">
      <PublicEntryAside />
      <section className="registrationCard" aria-labelledby="guardian-invitation-title">
        <div className="registrationBrand" aria-hidden="true">竞</div>
        <p className="registrationEyebrow">GUARDIAN INVITATION</p>
        <h1 id="guardian-invitation-title">确认监护邀请</h1>

        {loading ? <p className="registrationLead" role="status">正在安全确认邀请…</p> : null}
        {error ? <p className="registrationError" role="alert">{error}</p> : null}
        {handoffRetryable ? (
          <button
            className="primaryButton registrationSubmit"
            type="button"
            onClick={() => {
              setHandoffRetryable(false)
              setError(null)
              setLoading(true)
              setRetryVersion((value) => value + 1)
            }}
          >
            重试确认邀请
          </button>
        ) : null}
        {accepted ? (
          <div className="registrationStatus" role="status">
            <strong>监护关系已建立</strong>
            <span>你现在可以按家庭范围查看被监护学员获授权的信息。</span>
            <Link to="/">返回工作台</Link>
          </div>
        ) : null}

        {context && !accepted ? (
          <div className="registrationForm">
            <div className="registrationStatus">
              <strong>受邀联系方式：{context.masked_identifier}</strong>
              <span>关系：{context.relationship}</span>
              <span>{context.is_primary ? "主要监护人" : "普通监护人"}</span>
            </div>

            {status === "authenticated" ? (
              <>
                <label className="registrationConsent">
                  <input
                    type="checkbox"
                    checked={consented}
                    onChange={(event) => setConsented(event.target.checked)}
                  />
                  <span>本人确认接受监护关系并同意按家庭范围查看学员资料</span>
                </label>
                <button
                  className="primaryButton registrationSubmit"
                  type="button"
                  disabled={!consented || submitting}
                  onClick={() => void acceptInvitation()}
                >
                  {submitting ? "正在确认…" : "确认建立监护关系"}
                </button>
              </>
            ) : status === "force_password_change" ? (
              <Link
                className="primaryButton registrationActionLink"
                to="/change-password"
                state={{ returnTo: INVITATION_PATH }}
              >
                修改密码后继续
              </Link>
            ) : status === "anonymous" ? (
              <div className="registrationActions">
                <Link
                  className="primaryButton registrationActionLink"
                  to="/login"
                  state={{ returnTo: INVITATION_PATH }}
                >
                  已有账号登录
                </Link>
                <Link
                  className="registrationSecondaryButton registrationActionLink"
                  to="/register/guardian-invitation"
                >
                  注册监护人账号
                </Link>
              </div>
            ) : (
              <p className="registrationLead" role="status">正在确认登录状态…</p>
            )}
          </div>
        ) : null}
      </section>
    </main>
  )
}
