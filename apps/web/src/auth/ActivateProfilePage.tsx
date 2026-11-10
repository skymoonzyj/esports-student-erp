import { useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { Link, useNavigate } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { PublicEntryAside } from "../registration/PublicEntryAside"
import { PasswordInput } from "./PasswordInput"
import { completeProfileActivation, startProfileActivation, verifyProfileActivation } from "./activationApi"
import type { ActivationPreview } from "./activationApi"
import "../registration/registration.css"

const KIND_LABELS = { student: "学员", player: "队员", both: "学员兼队员", staff: "工作人员" }
const ACCEPTED = "请求已受理；如未收到邮件，可能资料尚未获批或邮件发送失败，请稍后重试或联系管理员"
const PASSWORD_HINT = "密码需为 8–16 位，并同时包含大写、小写字母和数字"

function errorMessage(error: unknown): string {
  if (error instanceof ProblemDetailsError) {
    if (error.problem.status === 429) return "请求过于频繁，请稍后重试"
    if (error.problem.code === "verification_invalid_or_expired") return "验证码无效或已过期，请重新请求验证码"
    if (error.problem.code === "password_policy_failed") return PASSWORD_HINT
    if (error.problem.code === "activation_roles_invalidated") return "激活角色授权已失效，请联系管理员重新确认"
  }
  return "暂时无法完成操作，请稍后重试"
}

export function ActivateProfilePage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState("")
  const [requested, setRequested] = useState(false)
  const [code, setCode] = useState("")
  const [preview, setPreview] = useState<ActivationPreview | null>(null)
  const [password, setPassword] = useState("")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [resendAt, setResendAt] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const request = useRef<AbortController | null>(null)
  const remaining = Math.max(0, Math.ceil((resendAt - now) / 1000))

  // Route departure cancels this page's I/O; focus/background events do not.
  // Also fence late responses from transports that have already delivered them.
  useEffect(() => () => { request.current?.abort() }, [])

  function beginRequest() {
    const controller = new AbortController()
    request.current = controller
    return controller
  }

  useEffect(() => {
    if (!resendAt) return
    const timer = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [resendAt])

  function changeEmail(value: string) {
    setEmail(value)
    setRequested(false)
    setPreview(null)
    setCode("")
    setPassword("")
    setError(null)
  }

  function failed(caught: unknown) {
    setError(errorMessage(caught))
    if (caught instanceof ProblemDetailsError && caught.retryAfterSeconds !== null) {
      const current = Date.now()
      setNow(current)
      setResendAt((previous) => Math.max(previous, current + caught.retryAfterSeconds! * 1000))
    }
  }

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || remaining > 0) return
    setPending(true)
    setError(null)
    const controller = beginRequest()
    try {
      const result = await startProfileActivation(email.trim(), controller.signal)
      if (controller.signal.aborted) return
      setRequested(true)
      setCode("")
      setPassword("")
      setPreview(null)
      setNow(Date.now())
      setResendAt(Date.parse(result.resend_available_at))
    } catch (caught) {
      if (!controller.signal.aborted) failed(caught)
    } finally {
      if (!controller.signal.aborted) setPending(false)
    }
  }

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending) return
    if (!/^[0-9]{6}$/.test(code)) {
      setError("请输入 6 位数字验证码")
      return
    }
    setPending(true)
    setError(null)
    const controller = beginRequest()
    try {
      const result = await verifyProfileActivation(code, controller.signal)
      if (!controller.signal.aborted) setPreview(result)
    } catch (caught) {
      if (!controller.signal.aborted) failed(caught)
    } finally {
      if (!controller.signal.aborted) setPending(false)
    }
  }

  async function complete(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || !preview) return
    if (!/^(?=.{8,16}$)(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9]).*$/.test(password)) {
      setError(PASSWORD_HINT)
      return
    }
    setPending(true)
    setError(null)
    const controller = beginRequest()
    try {
      await completeProfileActivation(code, password, controller.signal)
      if (controller.signal.aborted) return
      setEmail("")
      setCode("")
      setPassword("")
      setPreview(null)
      setRequested(false)
      navigate("/login", { replace: true })
    } catch (caught) {
      if (!controller.signal.aborted) failed(caught)
    } finally {
      if (!controller.signal.aborted) setPending(false)
    }
  }

  return (
    <main className="registrationPage">
      <PublicEntryAside />
      <section className="registrationCard" aria-labelledby="activate-profile-title">
        <div className="registrationBrand" aria-hidden="true">竞</div>
        <p className="registrationEyebrow">PROFILE ACTIVATION</p>
        <h1 id="activate-profile-title">激活已有档案</h1>
        <p className="registrationLead">通过本人邮箱验证后设置密码。暂不支持手机号激活，请使用本人邮箱。</p>
        <form className="registrationForm" onSubmit={start}>
          <div className="registrationFieldGroup">
            <label htmlFor="activation-email">本人邮箱</label>
            <input id="activation-email" type="email" autoComplete="email" value={email}
              onChange={(event) => changeEmail(event.target.value)} disabled={pending} required />
          </div>
          <button className="primaryButton registrationSubmit" type="submit" disabled={pending || remaining > 0}>
            {requested ? "重新请求验证码" : "请求邮箱验证码"}{remaining > 0 ? `（${remaining} 秒）` : ""}
          </button>
        </form>
        {requested ? <p className="registrationLead" role="status">{ACCEPTED}</p> : null}
        {preview ? (
          <form className="registrationForm" onSubmit={complete} noValidate>
            <div className="registrationStatus">
              <strong>{preview.masked_name}</strong>
              <span>{KIND_LABELS[preview.kind]}</span>
              <p>预览不代表账号已激活；完成密码设置后仍需正常登录。档案身份不等于系统角色或管理权限；已批准的额外角色仅在激活时通过授权复核后生效，不自动建立监护、教练或队伍关系。</p>
            </div>
            <div className="registrationFieldGroup">
              <label htmlFor="activation-password">新密码</label>
              <PasswordInput id="activation-password" autoComplete="new-password" value={password}
                onChange={(event) => setPassword(event.target.value)} disabled={pending} required />
              <small>{PASSWORD_HINT}，允许符号。</small>
            </div>
            <button className="primaryButton registrationSubmit" type="submit" disabled={pending}>
              {pending ? "正在激活…" : "完成激活"}
            </button>
          </form>
        ) : requested ? (
          <form className="registrationForm" onSubmit={verify} noValidate>
            <div className="registrationFieldGroup">
              <label htmlFor="activation-code">验证码</label>
              <input id="activation-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
                value={code} onChange={(event) => setCode(event.target.value)} disabled={pending} required />
            </div>
            <button className="primaryButton registrationSubmit" type="submit" disabled={pending}>
              {pending ? "正在验证…" : "验证邮箱"}
            </button>
          </form>
        ) : null}
        {error ? <p className="registrationError" role="alert">{error}</p> : null}
        <p className="registrationFooter"><Link to="/login">返回登录</Link></p>
      </section>
    </main>
  )
}
