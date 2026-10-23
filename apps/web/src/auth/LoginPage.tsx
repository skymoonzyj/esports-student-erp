import { useState } from "react"
import type { FormEvent } from "react"
import { Link, useNavigate } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "./AuthProvider"
import { PasswordInput } from "./PasswordInput"
import "./LoginPage.css"

function errorMessage(error: unknown): string {
  if (error instanceof ProblemDetailsError) return error.problem.detail
  return "暂时无法连接系统，请稍后重试"
}

export function LoginPage() {
  const { login, status } = useAuth()
  const [identifier, setIdentifier] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(identifier, password)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="loginPage">
      <section className="loginBrand" aria-label="竞学 ERP 介绍">
        <div className="loginBrandMark" aria-hidden="true">
          竞
        </div>
        <p className="loginEyebrow">ESPORTS EDUCATION OPERATIONS</p>
        <h1>把每一位学员的成长，放在清晰可见的位置。</h1>
        <p className="loginBrandCopy">
          学员档案、教学协作与运营管理统一在一个安全的工作台中。
        </p>
        <div className="loginFeatureRow" aria-label="系统特点">
          <span>实名资料保护</span>
          <span>角色权限隔离</span>
          <span>全端自适应</span>
        </div>
      </section>

      <section className="loginPanel" aria-labelledby="login-title">
        <div className="loginCard">
          <div className="loginMobileBrand">
            <span className="loginMobileMark">竞</span>
            <strong>竞学 ERP</strong>
          </div>
          <p className="loginKicker">欢迎回来</p>
          <h2 id="login-title">登录工作台</h2>
          <p className="loginHint">使用已验证的手机号或邮箱以及密码。</p>

          {status === "force_password_change" ? (
            <div className="loginNotice" role="status">
              <strong>请先修改临时密码</strong>
              <span>首次登录需设置只有你本人知道的新密码。</span>
            </div>
          ) : null}

          <form className="loginForm" onSubmit={submit}>
            <label htmlFor="login-identifier">手机号或邮箱</label>
            <input
              id="login-identifier"
              name="identifier"
              type="text"
              autoComplete="username"
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
              placeholder="请输入手机号或邮箱"
              required
            />

            <div className="loginPasswordLabel">
              <label htmlFor="login-password">密码</label>
              <span>区分大小写</span>
            </div>
            <PasswordInput
              id="login-password"
              name="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="请输入密码"
              required
            />

            {error ? (
              <p className="formError" role="alert">
                {error}
              </p>
            ) : null}

            <button className="primaryButton loginSubmit" type="submit" disabled={submitting}>
              {submitting ? "正在登录…" : "登录"}
            </button>
          </form>
          <nav className="loginPublicActions" aria-label="账号服务">
            <Link to="/register">注册账号</Link>
            <Link to="/activate-profile">激活已有档案</Link>
            <Link to="/forgot-password">忘记密码</Link>
          </nav>
          <p className="loginSupport">无法登录时，请联系本机构管理员重置账号。</p>
        </div>
      </section>
    </main>
  )
}

export function ChangePasswordPage({ returnTo = "/" }: { returnTo?: string }) {
  const { changePassword } = useAuth()
  const navigate = useNavigate()
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmation, setConfirmation] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致")
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      await changePassword(currentPassword, newPassword)
      navigate(returnTo, { replace: true })
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="passwordPage">
      <section className="passwordCard" aria-labelledby="change-password-title">
        <div className="loginMobileBrand alwaysVisible">
          <span className="loginMobileMark">竞</span>
          <strong>竞学 ERP</strong>
        </div>
        <p className="loginKicker">账号安全</p>
        <h1 id="change-password-title">请先修改临时密码</h1>
        <p className="loginHint">新密码需为 8–16 位，并同时包含大写、小写字母和数字。</p>
        <form className="loginForm" onSubmit={submit}>
          <label htmlFor="current-password">当前临时密码</label>
          <PasswordInput
            id="current-password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            required
          />
          <label htmlFor="new-password">新密码</label>
          <PasswordInput
            id="new-password"
            autoComplete="new-password"
            minLength={8}
            maxLength={16}
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            required
          />
          <label htmlFor="confirm-password">再次输入新密码</label>
          <PasswordInput
            id="confirm-password"
            autoComplete="new-password"
            minLength={8}
            maxLength={16}
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            required
          />
          {error ? (
            <p className="formError" role="alert">
              {error}
            </p>
          ) : null}
          <button className="primaryButton loginSubmit" type="submit" disabled={submitting}>
            {submitting ? "正在保存…" : "保存新密码"}
          </button>
        </form>
      </section>
    </main>
  )
}
