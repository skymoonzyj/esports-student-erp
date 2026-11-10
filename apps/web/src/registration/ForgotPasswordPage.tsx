import { useState } from "react"
import type { FormEvent } from "react"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { PasswordInput } from "../auth/PasswordInput"
import {
  completePasswordReset,
  issuePasswordResetChallenge,
} from "./api"
import type { RegistrationChallenge } from "./api"
import "./registration.css"
import { PublicEntryAside } from "./PublicEntryAside"

function recoveryError(error: unknown): string {
  if (error instanceof ProblemDetailsError) {
    if (error.problem.status === 429) return "验证码请求过于频繁，请稍后重试"
    if (error.problem.code === "verification_invalid_or_expired") {
      return "验证码无效或已过期"
    }
    if (error.problem.code === "password_policy_failed") {
      return "新密码需为 8–16 位，并同时包含大写、小写字母和数字"
    }
  }
  return "暂时无法完成操作，请稍后重试"
}

export function ForgotPasswordPage() {
  const [identifier, setIdentifier] = useState("")
  const [challenge, setChallenge] = useState<RegistrationChallenge | null>(null)
  const [code, setCode] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmation, setConfirmation] = useState("")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [completed, setCompleted] = useState(false)

  async function issue(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setPending(true)
    setError(null)
    try {
      const result = await issuePasswordResetChallenge(identifier.trim())
      setChallenge(result)
      setIdentifier("")
    } catch (caught) {
      setError(recoveryError(caught))
    } finally {
      setPending(false)
    }
  }

  async function complete(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!challenge) return
    if (!/^\d{6}$/.test(code)) {
      setError("请输入 6 位数字验证码")
      return
    }
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致")
      return
    }
    setPending(true)
    setError(null)
    try {
      await completePasswordReset({
        challenge_id: challenge.challenge_id,
        code,
        new_password: newPassword,
      })
      setCode("")
      setNewPassword("")
      setConfirmation("")
      setChallenge(null)
      setCompleted(true)
    } catch (caught) {
      setError(recoveryError(caught))
    } finally {
      setPending(false)
    }
  }

  return (
    <main className="registrationPage">
      <PublicEntryAside />
      <section className="registrationCard" aria-labelledby="forgot-password-title">
        <div className="registrationBrand" aria-hidden="true">竞</div>
        <p className="registrationEyebrow">ACCOUNT RECOVERY</p>
        <h1 id="forgot-password-title">找回密码</h1>
        <p className="registrationLead">通过已验证的手机号或邮箱接收验证码。</p>

        {completed ? (
          <div className="registrationStatus" role="status">
            <strong>如果账号信息有效，密码已完成更新</strong>
            <span>请使用新密码登录；其他已登录设备会安全退出。</span>
            <Link to="/login">返回登录</Link>
          </div>
        ) : challenge ? (
          <form className="registrationForm" onSubmit={complete}>
            <p className="registrationLead">验证码已发送至</p>
            <p className="registrationMaskedValue">{challenge.masked_destination}</p>
            <div className="registrationFieldGroup">
              <label htmlFor="recovery-code">验证码</label>
              <input
                id="recovery-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                required
              />
            </div>
            <div className="registrationFieldGroup">
              <label htmlFor="recovery-password">新密码</label>
              <PasswordInput
                id="recovery-password"
                autoComplete="new-password"
                minLength={8}
                maxLength={16}
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                required
              />
            </div>
            <div className="registrationFieldGroup">
              <label htmlFor="recovery-confirmation">确认新密码</label>
              <PasswordInput
                id="recovery-confirmation"
                autoComplete="new-password"
                minLength={8}
                maxLength={16}
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                required
              />
            </div>
            {error ? <p className="registrationError" role="alert">{error}</p> : null}
            <button className="primaryButton registrationSubmit" type="submit" disabled={pending}>
              {pending ? "正在重置…" : "重置密码"}
            </button>
          </form>
        ) : (
          <form className="registrationForm" onSubmit={issue}>
            <div className="registrationFieldGroup">
              <label htmlFor="recovery-identifier">手机号或邮箱</label>
              <input
                id="recovery-identifier"
                autoComplete="username"
                value={identifier}
                onChange={(event) => setIdentifier(event.target.value)}
                required
              />
            </div>
            {error ? <p className="registrationError" role="alert">{error}</p> : null}
            <button className="primaryButton registrationSubmit" type="submit" disabled={pending}>
              {pending ? "正在发送…" : "发送验证码"}
            </button>
          </form>
        )}

        {!completed ? <p className="registrationFooter"><Link to="/login">返回登录</Link></p> : null}
      </section>
    </main>
  )
}
