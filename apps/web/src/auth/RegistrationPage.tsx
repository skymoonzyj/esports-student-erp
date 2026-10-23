import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { Link, useNavigate } from "react-router-dom"

import { apiFetch, ProblemDetailsError } from "../api/client"
import { PasswordInput } from "./PasswordInput"
import "./LoginPage.css"

type RegistrationForm = {
  full_name: string
  phone: string
  email: string
  identity_number: string
  sex: "male" | "female"
  date_of_birth: string
  person_type: "student" | "guardian" | "staff"
  staff_type: "coach" | "life_teacher" | "customer_service" | "administration" | "other"
  password: string
  confirmation: string
}

type StartResponse = {
  registration_id: string
  expires_at: string
  message: string
}

type VerifyResponse = {
  requires_guardian: boolean
  message: string
}

function messageFor(error: unknown): string {
  if (error instanceof ProblemDetailsError) return error.problem.detail
  return "暂时无法连接系统，请稍后重试"
}

const initialForm: RegistrationForm = {
  full_name: "",
  phone: "",
  email: "",
  identity_number: "",
  sex: "female",
  date_of_birth: "",
  person_type: "student",
  staff_type: "coach",
  password: "",
  confirmation: "",
}

export function RegistrationPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState(initialForm)
  const [registration, setRegistration] = useState<StartResponse | null>(null)
  const [code, setCode] = useState("")
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    if (seconds <= 0) return
    const timer = window.setInterval(() => setSeconds((value) => Math.max(0, value - 1)), 1000)
    return () => window.clearInterval(timer)
  }, [seconds])

  function update<K extends keyof RegistrationForm>(key: K, value: RegistrationForm[K]) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  async function start(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setError(null)
    setNotice(null)
    if (form.password !== form.confirmation) {
      setError("两次输入的密码不一致")
      return
    }
    setSubmitting(true)
    try {
      const result = await apiFetch<StartResponse>("/auth/registration/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          full_name: form.full_name,
          phone: form.phone,
          email: form.email,
          identity_number: form.identity_number,
          sex: form.sex,
          date_of_birth: form.date_of_birth,
          person_type: form.person_type,
          staff_type: form.person_type === "staff" ? form.staff_type : null,
          password: form.password,
        }),
      })
      setRegistration(result)
      setSeconds(60)
      setNotice(result.message)
    } catch (caught) {
      setError(messageFor(caught))
    } finally {
      setSubmitting(false)
    }
  }

  async function verify(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!registration) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await apiFetch<VerifyResponse>("/auth/registration/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ registration_id: registration.registration_id, code }),
      })
      setNotice(result.message)
      window.setTimeout(() => navigate("/login", { replace: true }), 1400)
    } catch (caught) {
      setError(messageFor(caught))
    } finally {
      setSubmitting(false)
    }
  }

  async function resend(): Promise<void> {
    if (!registration || seconds > 0) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await apiFetch<StartResponse>("/auth/registration/resend", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ registration_id: registration.registration_id }),
      })
      setRegistration(result)
      setSeconds(60)
      setNotice(result.message)
    } catch (caught) {
      setError(messageFor(caught))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="loginPage">
      <section className="loginBrand" aria-label="竞学 ERP 注册说明">
        <div className="loginBrandMark" aria-hidden="true">竞</div>
        <p className="loginEyebrow">ESPORTS EDUCATION OPERATIONS</p>
        <h1>注册你的竞学 ERP 账号。</h1>
        <p className="loginBrandCopy">实名资料只用于账号和营地服务，敏感字段会按系统安全策略保存。</p>
        <div className="loginFeatureRow" aria-label="注册须知">
          <span>邮箱验证</span><span>实名资料</span><span>未成年需监护人</span>
        </div>
      </section>
      <section className="loginPanel" aria-labelledby="registration-title">
        <div className="loginCard registrationCard">
          <div className="loginMobileBrand"><span className="loginMobileMark">竞</span><strong>竞学 ERP</strong></div>
          <p className="loginKicker">新账号</p>
          <h2 id="registration-title">自助注册</h2>
          <p className="loginHint">所有资料均为必填，提交后需要验证邮箱。</p>

          {registration ? (
            <form className="loginForm" onSubmit={verify}>
              <div className="loginNotice" role="status"><strong>验证码已发送</strong><span>{notice}</span></div>
              <label htmlFor="registration-code">邮箱验证码</label>
              <input id="registration-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} autoComplete="one-time-code" required />
              <button className="primaryButton loginSubmit" type="submit" disabled={submitting}>{submitting ? "正在验证…" : "验证并完成注册"}</button>
              <button className="secondaryButton registrationResend" type="button" onClick={() => void resend()} disabled={submitting || seconds > 0}>{seconds > 0 ? `${seconds} 秒后重新发送` : "重新发送验证码"}</button>
              {error ? <p className="formError" role="alert">{error}</p> : null}
            </form>
          ) : (
            <form className="loginForm registrationForm" onSubmit={start}>
              <label htmlFor="registration-name">姓名</label>
              <input id="registration-name" value={form.full_name} onChange={(event) => update("full_name", event.target.value)} required />
              <label htmlFor="registration-phone">手机号</label>
              <input id="registration-phone" type="tel" inputMode="numeric" value={form.phone} onChange={(event) => update("phone", event.target.value)} required />
              <label htmlFor="registration-email">邮箱</label>
              <input id="registration-email" type="email" value={form.email} onChange={(event) => update("email", event.target.value)} required />
              <label htmlFor="registration-id">身份证号</label>
              <input id="registration-id" value={form.identity_number} onChange={(event) => update("identity_number", event.target.value.toUpperCase())} maxLength={18} required />
              <div className="registrationInlineFields">
                <div><label htmlFor="registration-sex">性别</label><select id="registration-sex" value={form.sex} onChange={(event) => update("sex", event.target.value as RegistrationForm["sex"])}><option value="female">女</option><option value="male">男</option></select></div>
                <div><label htmlFor="registration-birth">出生日期</label><input id="registration-birth" type="date" value={form.date_of_birth} onChange={(event) => update("date_of_birth", event.target.value)} required /></div>
              </div>
              <label htmlFor="registration-type">账号类型</label>
              <select id="registration-type" value={form.person_type} onChange={(event) => update("person_type", event.target.value as RegistrationForm["person_type"])}><option value="student">学员</option><option value="guardian">监护人</option><option value="staff">工作人员</option></select>
              {form.person_type === "staff" ? (
                <>
                  <label htmlFor="registration-staff-type">申请岗位</label>
                  <select id="registration-staff-type" value={form.staff_type} onChange={(event) => update("staff_type", event.target.value as RegistrationForm["staff_type"])}>
                    <option value="coach">教练</option><option value="life_teacher">生活老师</option><option value="customer_service">客服</option><option value="administration">行政</option><option value="other">其他工作人员</option>
                  </select>
                </>
              ) : null}
              <label htmlFor="registration-password">密码</label>
              <PasswordInput
                id="registration-password"
                minLength={8}
                maxLength={16}
                value={form.password}
                onChange={(event) => update("password", event.target.value)}
                autoComplete="new-password"
                required
              />
              <label htmlFor="registration-confirmation">确认密码</label>
              <PasswordInput
                id="registration-confirmation"
                minLength={8}
                maxLength={16}
                value={form.confirmation}
                onChange={(event) => update("confirmation", event.target.value)}
                autoComplete="new-password"
                required
              />
              {error ? <p className="formError" role="alert">{error}</p> : null}
              <button className="primaryButton loginSubmit" type="submit" disabled={submitting}>{submitting ? "正在发送验证码…" : "提交并验证邮箱"}</button>
            </form>
          )}
          {!registration && <p className="loginSupport">已有账号？<Link to="/login">返回登录</Link></p>}
        </div>
      </section>
    </main>
  )
}
