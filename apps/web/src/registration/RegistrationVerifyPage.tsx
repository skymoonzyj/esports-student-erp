import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { Link, useLocation, useNavigate } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import {
  issueRegistrationChallenge,
  verifyRegistration,
} from "./api"
import "./registration.css"

type VerifyRouteState = {
  applicationId: string
  challengeId: string
  maskedDestination: string
  retryAfterSeconds: number
  guardianInvitationFlow?: true
}

function verifyRouteState(value: unknown): VerifyRouteState | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  if (
    typeof record.applicationId !== "string" ||
    typeof record.challengeId !== "string" ||
    typeof record.maskedDestination !== "string" ||
    typeof record.retryAfterSeconds !== "number" ||
    !Number.isInteger(record.retryAfterSeconds) ||
    (record.guardianInvitationFlow !== undefined &&
      record.guardianInvitationFlow !== true)
  ) {
    return null
  }
  return record as VerifyRouteState
}

function verificationError(error: unknown): string {
  if (error instanceof ProblemDetailsError) {
    if (error.problem.status === 429) return "验证码请求过于频繁，请稍后重试"
    if (error.problem.code === "verification_invalid_or_expired") {
      return "验证码无效或已过期"
    }
  }
  return "暂时无法验证，请重新输入或稍后重试"
}

export function RegistrationVerifyPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const routeState = verifyRouteState(location.state)
  const [challengeId, setChallengeId] = useState(routeState?.challengeId ?? "")
  const [maskedDestination, setMaskedDestination] = useState(
    routeState?.maskedDestination ?? "",
  )
  const [retryAfterSeconds, setRetryAfterSeconds] = useState(
    Math.max(0, routeState?.retryAfterSeconds ?? 0),
  )
  const [code, setCode] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [resending, setResending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (retryAfterSeconds <= 0) return
    const timer = window.setInterval(() => {
      setRetryAfterSeconds((current) => Math.max(0, current - 1))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [retryAfterSeconds > 0])

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!routeState) return
    if (!/^\d{6}$/.test(code)) {
      setError("请输入 6 位数字验证码")
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await verifyRegistration(routeState.applicationId, {
        challenge_id: challengeId,
        code,
      })
      setCode("")
      navigate("/register/profile", {
        replace: true,
        state: routeState.guardianInvitationFlow
          ? { guardianInvitationFlow: true }
          : null,
      })
    } catch (caught) {
      setError(verificationError(caught))
    } finally {
      setSubmitting(false)
    }
  }

  async function resend(): Promise<void> {
    if (!routeState || retryAfterSeconds > 0) return
    setResending(true)
    setError(null)
    try {
      const challenge = await issueRegistrationChallenge(routeState.applicationId)
      setChallengeId(challenge.challenge_id)
      setMaskedDestination(challenge.masked_destination)
      setRetryAfterSeconds(Math.max(0, challenge.retry_after_seconds))
      setCode("")
    } catch (caught) {
      setError(verificationError(caught))
    } finally {
      setResending(false)
    }
  }

  return (
    <main className="registrationPage">
      <section className="registrationCard" aria-labelledby="registration-verify-title">
        <div className="registrationStep">步骤 2 / 3</div>
        <p className="registrationEyebrow">CONTACT VERIFICATION</p>
        <h1 id="registration-verify-title">验证联系方式</h1>

        {!routeState ? (
          <div className="registrationStatus registrationStatusWarning" role="alert">
            <strong>验证步骤已失效</strong>
            <span>请重新选择身份和联系方式。</span>
            <Link to="/register">重新开始注册</Link>
          </div>
        ) : (
          <>
            <p className="registrationLead">验证码已发送至</p>
            <p className="registrationMaskedValue">{maskedDestination}</p>
            <form className="registrationForm" onSubmit={submit}>
              <div className="registrationFieldGroup">
                <label htmlFor="registration-code">验证码</label>
                <input
                  id="registration-code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  placeholder="请输入 6 位验证码"
                  required
                />
              </div>
              {error ? <p className="registrationError" role="alert">{error}</p> : null}
              <button className="primaryButton registrationSubmit" type="submit" disabled={submitting}>
                {submitting ? "正在验证…" : "验证并继续"}
              </button>
              <button
                className="registrationSecondaryButton"
                type="button"
                onClick={() => void resend()}
                disabled={resending || retryAfterSeconds > 0}
              >
                {resending
                  ? "正在重发…"
                  : retryAfterSeconds > 0
                    ? `${retryAfterSeconds} 秒后可重发`
                    : "重新发送验证码"}
              </button>
            </form>
            <p className="registrationFooter"><Link to="/register">更换联系方式</Link></p>
          </>
        )}
      </section>
    </main>
  )
}
