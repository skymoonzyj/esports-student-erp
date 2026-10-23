import { useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { Link } from "react-router-dom"
import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { clearProfileClaimSession, getProfileClaimStatus, startProfileClaim, verifyProfileClaim } from "./api"
import { subscribeProfileClaimBoundary } from "./keys"
import { ClaimCompletionFields, emptyClaimCompletion, toClaimCompletion } from "./ClaimCompletionFields"
import type { ClaimPublicStatus } from "./types"
import "../registration/registration.css"
import "./profileClaims.css"

const WAITING = "邮箱已验证，申请等待管理员核对。邮箱验证不代表档案匹配或账号已激活。"
const STATUS_TEXT: Record<ClaimPublicStatus["status"], string> = {
  waiting_review: WAITING,
  approved: "待本人激活",
  rejected: "申请未获批准，请核对资料或联系管理员。",
  expired: "申请已过期，请重新填写资料并验证邮箱。",
  closed: "申请已关闭，请联系管理员或使用已有账号正常登录。",
}

export function ClaimProfilePage() {
  const auth = useOptionalAuth()
  const [fullName, setFullName] = useState("")
  const [identity, setIdentity] = useState("")
  const [documentType, setDocumentType] = useState<"cn_id" | "passport">("cn_id")
  const [issuer, setIssuer] = useState("")
  const [completion, setCompletion] = useState(emptyClaimCompletion)
  const [email, setEmail] = useState("")
  const [code, setCode] = useState("")
  const [requested, setRequested] = useState(false)
  const [status, setStatus] = useState<ClaimPublicStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [resendAt, setResendAt] = useState(0)
  const [retryAt, setRetryAt] = useState(0)
  const [expiresAt, setExpiresAt] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const flight = useRef<AbortController | null>(null)
  const active = useRef(false)
  const remaining = Math.max(0, Math.ceil((resendAt - now) / 1000))
  const retryRemaining = Math.max(0, Math.ceil((retryAt - now) / 1000))

  // App blur/background/focus does not discard in-memory fields or make requests.
  useEffect(() => {
    active.current = true
    const unsubscribe = subscribeProfileClaimBoundary(() => {
      flight.current?.abort(); flight.current = null
      setFullName(""); setIdentity(""); setEmail(""); setCode("")
      setDocumentType("cn_id"); setIssuer(""); setCompletion(emptyClaimCompletion())
      setRequested(false); setStatus(null); setError(null); setPending(false)
      setResendAt(0); setRetryAt(0); setExpiresAt(0)
    })
    return () => {
      active.current = false
      unsubscribe()
      flight.current?.abort()
      clearProfileClaimSession()
    }
  }, [])

  useEffect(() => {
    if (!resendAt && !expiresAt && !retryAt) return
    const timer = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [resendAt, expiresAt, retryAt])

  // Initial session restore can still trigger a real account boundary. Do not
  // accept a draft until that boundary has settled; later boundaries still clear it.
  if (auth?.status === "loading") {
    return <main className="registrationPage"><section className="registrationCard" role="status">正在确认当前会话…</section></main>
  }

  const expired = expiresAt > 0 && now >= expiresAt
  function changed(update: () => void) {
    if (flight.current) return
    update(); setCode(""); setRequested(false); setStatus(null); setError(null)
    setExpiresAt(0)
  }

  function failed(caught: unknown) {
    if (caught instanceof ProblemDetailsError) {
      const { status: httpStatus, code: problemCode } = caught.problem
      if (httpStatus === 401 || httpStatus === 403) {
        setRequested(false); setStatus(null); setCode(""); setExpiresAt(0)
        setError("会话已过期，请重新验证邮箱后查询。")
        return
      }
      const deliveryFailed = httpStatus >= 500
      if (httpStatus === 429 || deliveryFailed || caught.retryAfterSeconds !== null) {
        const retrySeconds = caught.retryAfterSeconds ?? 60
        const current = Date.now()
        setNow(current)
        setRetryAt((previous) => Math.max(previous, current + retrySeconds * 1000))
        setResendAt((previous) => Math.max(previous, current + retrySeconds * 1000))
      }
      if (httpStatus === 429) { setError("请求过于频繁，请在倒计时结束后重试。"); return }
      if (deliveryFailed) { setError("邮件暂时无法发送或服务暂不可用，请在倒计时结束后重试。"); return }
      if (problemCode === "verification_invalid_or_expired") { setError("验证码无效或已过期，请检查后重试或重新请求验证码。"); return }
    }
    setError("操作未成功，请检查资料并稍后重试。")
  }

  async function run(operation: (signal: AbortSignal) => Promise<void>) {
    if (flight.current || retryRemaining > 0) return
    const controller = new AbortController()
    flight.current = controller
    setPending(true); setError(null)
    try { await operation(controller.signal) }
    catch (caught) {
      if (active.current && !controller.signal.aborted && !(caught instanceof DOMException && caught.name === "AbortError")) failed(caught)
    } finally {
      if (flight.current === controller) {
        flight.current = null
        if (active.current) setPending(false)
      }
    }
  }

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || remaining > 0) return
    await run(async (signal) => {
      // The new start replaces the cookie/challenge, including when delivery fails.
      setStatus(null); setRequested(false); setCode(""); setExpiresAt(0)
      const extra = toClaimCompletion(completion)
      const result = await startProfileClaim({ full_name: fullName, identity_number: identity, email,
        ...(documentType === "passport" ? { document_type: documentType, issuing_country: issuer.trim() || null } : {}),
        ...(extra ? { completion: extra } : {}),
      }, signal)
      if (!active.current || signal.aborted) return
      setRequested(true); setStatus(null); setCode("")
      setResendAt(Date.parse(result.resend_available_at)); setExpiresAt(Date.parse(result.expires_at)); setNow(Date.now())
    })
  }

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || expired || retryRemaining > 0) return
    if (!/^[0-9]{6}$/.test(code)) { setError("请输入 6 位数字验证码"); return }
    await run(async (signal) => {
      const result = await verifyProfileClaim(code, signal)
      if (!active.current || signal.aborted) return
      setStatus(result); setCode("")
    })
  }

  return <main className="registrationPage">
    <section className="registrationCard" aria-labelledby="claim-profile-title">
      <div className="registrationBrand" aria-hidden="true">竞</div>
      <p className="registrationEyebrow">PROFILE CLAIM</p>
      <h1 id="claim-profile-title">已有档案，申请激活</h1>
      <p className="registrationLead">适用于已有待补或已审核档案的本人认领。请自行填写姓名、证件、邮箱及可确认的补充资料；验证邮箱后由管理员核对，批准后仍需本人另取新验证码并设置密码激活。</p>
      <p className="registrationLead">资料仅保留在当前页面内存中；切到邮箱 App 后返回可继续，离开页面后需重新填写。仅预填邮箱不代表已验证认领绑定；已完成核对绑定的邮箱可使用<Link to="/activate-profile">原激活入口</Link>。</p>
      <form className="registrationForm" onSubmit={start} autoComplete="off">
        <div className="registrationFieldGroup"><label htmlFor="claim-name">姓名</label>
          <input id="claim-name" value={fullName} maxLength={80} autoComplete="off" required disabled={pending}
            onChange={(event) => changed(() => setFullName(event.target.value))} /></div>
        <div className="registrationFieldGroup"><label htmlFor="claim-document">证件类型</label>
          <select id="claim-document" value={documentType} disabled={pending} onChange={(event) => changed(() => {
            setDocumentType(event.target.value as "cn_id" | "passport"); setIdentity(""); setIssuer("")
          })}><option value="cn_id">中国居民身份证</option><option value="passport">护照</option></select></div>
        <div className="registrationFieldGroup"><label htmlFor="claim-identity">{documentType === "passport" ? "护照号码" : "身份证"}</label>
          <input id="claim-identity" value={identity} maxLength={documentType === "passport" ? 80 : 18} autoComplete="off" required disabled={pending}
            onChange={(event) => changed(() => setIdentity(event.target.value))} /></div>
        {documentType === "passport" ? <div className="registrationFieldGroup"><label htmlFor="claim-issuer">签发国家（两位代码，可暂缺）</label>
          <input id="claim-issuer" value={issuer} maxLength={2} pattern="[A-Za-z]{2}" autoComplete="off" disabled={pending}
            onChange={(event) => changed(() => setIssuer(event.target.value))} />
          <small>如 US、CN。匹配须准确签发国家；不清楚时可提交，由管理员核对补齐签发信息。提交结果不表示存在档案。</small></div> : null}
        <div className="registrationFieldGroup"><label htmlFor="claim-email">本人邮箱</label>
          <input id="claim-email" type="email" value={email} autoComplete="off" required disabled={pending}
            onChange={(event) => changed(() => setEmail(event.target.value))} /></div>
        <ClaimCompletionFields value={completion} disabled={pending} onChange={(value) => changed(() => setCompletion(value))} />
        <p className="registrationLead">修改任一资料后需重新申请并验证邮箱，不会修改已验证的申请。审核批准不等于账号激活，也不会授予角色或监护权限。</p>
        <button className="primaryButton registrationSubmit" type="submit" disabled={pending || remaining > 0}>
          {requested ? "重新请求验证码" : "请求邮箱验证码"}{remaining > 0 ? `（${remaining} 秒）` : ""}
        </button>
      </form>
      <button type="button" className="secondaryButton claimReset" onClick={() => clearProfileClaimSession()}>清空资料并重新开始</button>
      {requested && !status && !expired ? <>
        <p className="registrationLead" role="status">请求已受理，请查看本人邮箱验证码。此结果不表示存在匹配档案。</p>
        <form className="registrationForm" onSubmit={verify} noValidate autoComplete="off">
          <div className="registrationFieldGroup"><label htmlFor="claim-code">验证码</label>
            <input id="claim-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code}
              onChange={(event) => setCode(event.target.value)} disabled={pending} /></div>
          <button className="primaryButton registrationSubmit" type="submit" disabled={pending || retryRemaining > 0}>验证邮箱并申请核对{retryRemaining > 0 ? `（${retryRemaining} 秒）` : ""}</button>
        </form>
      </> : null}
      {status && !expired ? <div className="registrationStatus" role="status">
        <strong>{STATUS_TEXT[status.status]}</strong>
        {status.status === "rejected" && status.reason ? <p>审核说明：{status.reason}</p> : null}
        {status.status === "approved" ? <><p>管理员已核对，尚未创建账号。请到原激活页重新获取验证码，认领验证码不能用于激活。</p><Link to="/activate-profile">前往本人激活</Link></> : null}
        <button type="button" className="secondaryButton" disabled={pending || retryRemaining > 0} onClick={() => void run(async (signal) => {
          const result = await getProfileClaimStatus(signal)
          if (active.current && !signal.aborted) setStatus(result)
        })}>查询审核状态{retryRemaining > 0 ? `（${retryRemaining} 秒）` : ""}</button>
      </div> : null}
      {expired ? <p className="registrationError" role="alert">会话已过期，请重新验证邮箱后查询。</p> : null}
      {error ? <p className="registrationError" role="alert">{error}</p> : null}
      <p className="registrationFooter"><Link to="/login">返回登录</Link></p>
    </section>
  </main>
}
