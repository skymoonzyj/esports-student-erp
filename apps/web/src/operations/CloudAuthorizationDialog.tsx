import { useEffect, useRef, useState } from "react"
import { QRCodeSVG } from "qrcode.react"
import { createPortal } from "react-dom"
import { useOptionalAuth } from "../auth/AuthProvider"
import * as api from "./cloudAuthorizationApi"
import type { AuthProvider, CloudAuthSessionView, CloudAppView, CloudDirectoryResult, QrPresentation } from "./cloudAuthorizationApi"
import type { CloudTarget } from "./cloudBackupApi"
import { CloudAuthorizationImportForm } from "./CloudAuthorizationImportForm"

export function useCloudAuthorizationOwner(): string | null {
  const auth = useOptionalAuth()
  return auth?.status === "authenticated" && auth.user?.is_super_admin && auth.user.permissions.includes("operations.monitor.read") ? auth.user.account_id : null
}
/** Component-owned abort boundary. Nothing from these requests enters a query cache. */
export function useCloudAuthorizationRequests() {
  const controller = useRef(new AbortController())
  const generation = useRef(0)
  useEffect(() => {
    controller.current = new AbortController(); generation.current += 1
    return () => { generation.current += 1; controller.current.abort() }
  }, [])
  return { controller, generation }
}
const names = { baidu: "百度网盘", "115": "115 网盘" }
const labels: Record<api.AuthState, string> = { creating: "正在创建二维码", awaiting_confirmation: "等待手机确认", awaiting_import: "等待粘贴授权令牌", verifying_import: "正在验证授权", authorized: "授权成功，尚未启用备份", configuring: "配置中", test_pending: "待连接测试", ready: "测试通过，待明确应用", applying: "正在安全切换配置", applied: "已应用账号与目录，尚未验证新的云备份", expired: "二维码已过期", cancelled: "授权已取消", failed: "授权失败" }
const terminal = new Set<api.AuthState>(["applied", "expired", "cancelled", "failed"])
type Props = { provider: AuthProvider; mode?: api.AuthMode; onClose: () => void; onApplied: () => void; currentTarget?: CloudTarget; intent?: "bind" | "unbind" }

export function CloudAuthorizationDialog(props: Props) {
  const owner = useCloudAuthorizationOwner()
  const [initialOwner] = useState(owner)
  return createPortal(<div className="cloudAuthorizationOverlay"><section role="dialog" aria-modal="true" aria-label={`${names[props.provider]}${props.intent === "unbind" ? "解除绑定" : props.mode === "openlist_builtin_import" ? "免填应用信息授权" : "扫码授权"}`} className="cloudAuthorizationDialog">
    {owner && owner === initialOwner ? <AuthorizationContent key={`${owner}-${props.provider}-${props.mode ?? "own_app"}`} {...props} /> : <><p role="alert">登录身份或运维权限已改变，授权材料已清除。</p><button type="button" onClick={props.onClose}>关闭</button></>}
  </section></div>, document.body)
}
function AuthorizationContent({ provider, mode = "own_app", onClose, onApplied, currentTarget, intent = "bind" }: Props) {
  const builtin = mode === "openlist_builtin_import"
  const { controller, generation } = useCloudAuthorizationRequests()
  const [app, setApp] = useState<CloudAppView | null>(null)
  const [session, setSession] = useState<CloudAuthSessionView | null>(null)
  const [qr, setQr] = useState<QrPresentation | null>(null)
  const [directoryResult, setDirectoryResult] = useState<CloudDirectoryResult | null>(null)
  const [busy, setBusy] = useState(true)
  const [haltReason, setHaltReason] = useState<"recoverable" | "permission" | null>(null)
  const halted = haltReason !== null
  const [message, setMessage] = useState("")
  const [clearVersion, setClearVersion] = useState(0)
  const [importStatus, setImportStatus] = useState<"none" | "pending" | "unknown" | "retry">("none")
  const importOperation = useRef<{ id: string; revision: number; sessionId: string } | null>(null)
  const importQueryBusy = useRef(false)
  const [retryAt, setRetryAt] = useState(0)
  const [retryWait, setRetryWait] = useState(0)
  const [confirmed, setConfirmed] = useState(false)
  const [testRequested, setTestRequested] = useState(false)
  const requestedTestOperation = useRef<string | null>(null)
  const [unbinding, setUnbinding] = useState(false)
  const appliedNotified = useRef(false)
  const closing = useRef(false)
  const closeButton = useRef<HTMLButtonElement>(null)
  const dialog = useRef<HTMLDivElement>(null)
  const sessionRef = useRef(session)
  useEffect(() => { sessionRef.current = session }, [session])
  const active = (version: number) => version === generation.current && !controller.current.signal.aborted
  function accept(value: CloudAuthSessionView) {
    if (sessionRef.current && sessionRef.current.id !== value.id) { setClearVersion(version => version + 1); importOperation.current = null; setImportStatus("none") }
    sessionRef.current = value
    setSession(value)
    if (terminal.has(value.state)) {
      setClearVersion(version => version + 1); importOperation.current = null; setImportStatus("none")
      controller.current.abort(); generation.current += 1
      setDirectoryResult(null); setBusy(false); closing.current = false
    }
    if (value.state !== "awaiting_confirmation") setQr(null)
    if (value.state !== "ready") setConfirmed(false)
    if (value.state === "ready" || terminal.has(value.state)) {
      requestedTestOperation.current = null; setTestRequested(false)
    } else if (value.state === "test_pending" && value.probe_attempt && (!requestedTestOperation.current || value.probe_attempt.operation_id === requestedTestOperation.current)) {
      // Failure is actionable only after the backend proves this attempt's cleanup finished.
      // A missing or older attempt cannot release a newly submitted command.
      if (value.probe_attempt.status === "failed") setTestRequested(false)
      else setTestRequested(true)
    }
  }
  function failure(error: unknown) {
    if (error instanceof DOMException && error.name === "AbortError") return
    const safe = error instanceof api.CloudAuthorizationError ? error : new api.CloudAuthorizationError(0, "cloud_auth_failed")
    setMessage(safe.message)
    if (safe.status === 401 || safe.status === 403) {
      setClearVersion(version => version + 1); importOperation.current = null; setImportStatus("none")
      setHaltReason("permission"); setQr(null); setSession(null); setDirectoryResult(null); controller.current.abort(); generation.current += 1
    } else if (safe.status === 429) {
      const seconds = Math.max(1, safe.retryAfterSeconds ?? 30)
      setRetryWait(seconds); setRetryAt(Date.now() + seconds * 1000)
    } else { setHaltReason("recoverable"); setQr(null) }
  }
  async function fetchQr(value: CloudAuthSessionView, version: number) {
    if (builtin || value.state !== "awaiting_confirmation") return
    try {
      const result = await api.getCloudAuthorizationQr(provider, value.id, controller.current.signal)
      if (active(version) && sessionRef.current?.state === "awaiting_confirmation" && Date.parse(result.expires_at) > Date.now()) setQr(result)
    } catch (error) {
      if (!active(version)) return
      if (error instanceof api.CloudAuthorizationError && error.code === "cloud_auth_qr_unavailable") return
      failure(error)
    }
  }
  async function start(refresh = false) {
    if ((!builtin && (!app?.configured || app.prerequisites.length)) || busy || haltReason === "permission") return
    setClearVersion(version => version + 1); importOperation.current = null; setImportStatus("none")
    setBusy(true); setMessage(""); setQr(null); setDirectoryResult(null); setConfirmed(false); setHaltReason(null)
    requestedTestOperation.current = null; setTestRequested(false)
    controller.current.abort(); controller.current = new AbortController(); generation.current += 1
    const version = generation.current
    try {
      const old = sessionRef.current
      if (refresh && old && !terminal.has(old.state)) await api.cancelCloudAuthorization(provider, old.id, old.revision, controller.current.signal)
      if (!active(version)) return
      const value = builtin ? await api.createCloudAuthorization(provider, crypto.randomUUID(), controller.current.signal, mode) : await api.createCloudAuthorization(provider, crypto.randomUUID(), controller.current.signal)
      if (!active(version)) return
      sessionRef.current = value; accept(value); await fetchQr(value, version)
    } catch (error) { if (active(version)) failure(error) }
    finally { if (active(version)) setBusy(false) }
  }
  useEffect(() => {
    const version = generation.current
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeButton.current?.focus()
    void (async () => {
      try {
        if (builtin && intent === "bind") {
          const created = await api.createCloudAuthorization(provider, crypto.randomUUID(), controller.current.signal, mode)
          if (active(version)) accept(created)
          return
        }
        const value = await api.getCloudApplication(provider, controller.current.signal)
        if (!active(version)) return
        setApp(value)
        if (intent === "bind" && value.configured && !value.prerequisites.length) {
          const created = await api.createCloudAuthorization(provider, crypto.randomUUID(), controller.current.signal)
          if (!active(version)) return
          sessionRef.current = created; accept(created); await fetchQr(created, version)
        }
      } catch (error) { if (active(version)) failure(error) }
      finally { if (active(version)) setBusy(false) }
    })()
    // This component is keyed by provider and owner; initial creation happens once.
    return () => { if (previousFocus?.isConnected) previousFocus.focus() }
  }, [])
  useEffect(() => {
    if (!retryAt) return
    const timer = window.setInterval(() => { const remaining = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000)); setRetryWait(remaining); if (!remaining) setRetryAt(0) }, 1000)
    return () => window.clearInterval(timer)
  }, [retryAt])
  useEffect(() => {
    if (!session || terminal.has(session.state) || halted || busy || retryWait || importStatus !== "none" || ["awaiting_import", "verifying_import"].includes(session.state)) return
    let timer: number | undefined
    let polling = false
    let stopped = false
    const version = generation.current
    const delay = Math.max(1, session.next_poll_after_seconds) * 1000
    async function poll() {
      if (stopped || polling || document.hidden || !active(version)) return
      polling = true
      try {
        const value = await api.getCloudAuthorization(provider, session!.id, controller.current.signal)
        if (!active(version) || stopped) return
        sessionRef.current = value
        let result: CloudDirectoryResult | null = null
        if (directoryResult?.status === "pending" && !terminal.has(value.state)) {
          result = await api.getCloudDirectories(provider, value.id, directoryResult.operation_id, controller.current.signal)
          if (!active(version) || stopped) return
        }
        if (!qr && value.state === "awaiting_confirmation") await fetchQr(value, version)
        if (!active(version) || stopped) return
        accept(value)
        if (result) { setDirectoryResult(result); if (result.status === "failed") setMessage(api.cloudAuthorizationReason(result.error_code)) }
      } catch (error) { if (active(version) && !stopped) failure(error) }
      finally { polling = false; if (!stopped && active(version) && !document.hidden) timer = window.setTimeout(() => void poll(), delay) }
    }
    const visibility = () => { window.clearTimeout(timer); if (!document.hidden) void poll() }
    document.addEventListener("visibilitychange", visibility)
    window.addEventListener("focus", visibility)
    if (!document.hidden) timer = window.setTimeout(() => void poll(), delay)
    return () => { stopped = true; window.clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); window.removeEventListener("focus", visibility) }
  }, [session, busy, halted, retryWait, directoryResult, qr, importStatus])
  function acceptImport(result: api.CloudImportOperationView) {
    const expected = importOperation.current
    if (!expected || result.operation_id !== expected.id || result.session.id !== expected.sessionId || result.session.mode !== mode) { setMessage(api.cloudAuthorizationReason("cloud_auth_response_invalid")); setImportStatus("unknown"); return }
    setClearVersion(version => version + 1)
    setMessage(result.error_code ? api.cloudAuthorizationReason(result.error_code) : "")
    setImportStatus(result.status === "pending" ? "pending" : "none")
    if (result.status !== "pending") importOperation.current = null
    accept(result.session)
  }
  async function queryImport(version: number) {
    const operation = importOperation.current
    if (!operation || importQueryBusy.current || !active(version)) return
    importQueryBusy.current = true
    try {
      const result = await api.getCloudImportOperation(provider, operation.sessionId, operation.id, controller.current.signal)
      if (active(version) && importOperation.current === operation) acceptImport(result)
    } catch (error) {
      if (!active(version) || importOperation.current !== operation) return
      if (error instanceof api.CloudAuthorizationError && (error.status === 401 || error.status === 403)) failure(error)
      else { setImportStatus("unknown"); setMessage("验证状态未确认；不会自动重发。将查询原操作，请勿重复获取或复用旧刷新令牌。") }
    } finally { importQueryBusy.current = false }
  }
  async function submitImport(access: string, refresh: string) {
    if (!session || busy || halted || retryWait || !["none", "retry"].includes(importStatus) || session.state !== "awaiting_import" || Date.parse(session.expires_at) <= Date.now()) return
    setBusy(true); setMessage("")
    const version = generation.current
    const operation = importOperation.current ?? { id: crypto.randomUUID(), revision: session.revision, sessionId: session.id }
    importOperation.current = operation
    try {
      const result = await api.submitCloudAuthorizationImport(provider, operation.sessionId, { operation_id: operation.id, expected_revision: operation.revision, access_token: access, refresh_token: refresh }, controller.current.signal)
      access = ""; refresh = ""
      if (active(version)) acceptImport(result)
    } catch (error) {
      // eslint-disable-next-line no-useless-assignment -- Release credential references before awaiting an uncertain operation lookup.
      access = ""; refresh = ""
      if (!active(version)) return
      if (!(error instanceof api.CloudAuthorizationError) || error.status === 0 || error.status === 408 || error.status >= 500) {
        setImportStatus("unknown"); setMessage("验证状态未确认；不会自动重发。正在查询原操作。")
        await queryImport(version)
      } else { setClearVersion(value => value + 1); importOperation.current = null; setImportStatus("none"); failure(error) }
    } finally { if (active(version)) setBusy(false) }
  }
  useEffect(() => {
    if (!builtin || !["pending", "unknown"].includes(importStatus) || !session || busy || halted) return
    const version = generation.current
    const timer = window.setInterval(() => { if (!document.hidden) void queryImport(version) }, Math.max(1, session.next_poll_after_seconds) * 1000)
    const resume = () => { if (!document.hidden) void queryImport(version) }
    window.addEventListener("focus", resume); document.addEventListener("visibilitychange", resume)
    return () => { window.clearInterval(timer); window.removeEventListener("focus", resume); document.removeEventListener("visibilitychange", resume) }
  }, [builtin, importStatus, session, busy, halted])
  useEffect(() => {
    const expiry = qr?.expires_at ?? session?.expires_at
    if (!expiry || !session || terminal.has(session.state)) return
    const version = generation.current
    const timer = window.setTimeout(() => {
      if (active(version)) { setQr(null); setDirectoryResult(null); setConfirmed(false); accept({ ...session, state: "expired" }); setMessage("授权或配置期限已过期，请重新获取。") }
    }, Math.min(2147483647, Math.max(0, Date.parse(expiry) - Date.now())))
    return () => window.clearTimeout(timer)
  }, [qr, session])
  useEffect(() => {
    if (session?.state === "applied" && !appliedNotified.current) { appliedNotified.current = true; onApplied() }
  }, [session, onApplied])
  async function action(kind: "directories" | "select" | "test" | "apply" | "cancel" | "unbind", path = "/") {
    if (busy || halted || retryWait || (kind !== "unbind" && (!session || terminal.has(session.state) || Date.parse(session.expires_at) <= Date.now()))) return
    if (testRequested && ["directories", "select", "test"].includes(kind)) return
    if (kind === "cancel") { setClearVersion(value => value + 1); importOperation.current = null; setImportStatus("none") }
    setBusy(true); setMessage("")
    const version = generation.current
    try {
      const signal = controller.current.signal
      if (kind === "unbind") {
        if (!confirmed || !currentTarget) return
        const value = await api.unbindCloudAuthorization(provider, crypto.randomUUID(), currentTarget.revision, signal)
        if (!active(version)) return
        setUnbinding(true); setConfirmed(false)
        const status = await api.getCloudAuthorization(provider, value.session_id, signal)
        if (active(version)) accept(status)
      } else if (session) {
        const operation = crypto.randomUUID()
        if (kind === "directories") {
          const result = await api.requestCloudDirectories(provider, session.id, operation, session.revision, path, signal)
          if (active(version)) { setDirectoryResult(result); if (result.status === "failed") setMessage(api.cloudAuthorizationReason(result.error_code)) }
        } else {
          if (kind === "test") { requestedTestOperation.current = operation; setTestRequested(true) }
          const result = kind === "select" ? await api.selectCloudDirectory(provider, session.id, operation, session.revision, path, signal)
            : kind === "test" ? await api.testCloudAuthorization(provider, session.id, operation, session.revision, signal)
              : kind === "apply" ? await api.applyCloudAuthorization(provider, session.id, operation, session.revision, signal)
                : await api.cancelCloudAuthorization(provider, session.id, session.revision, signal)
          if (!active(version)) return
          accept(result)
          if (kind === "select") { setDirectoryResult(null); requestedTestOperation.current = null; setTestRequested(false) }
          if (kind === "cancel") onClose()
        }
      }
    } catch (error) { if (active(version)) failure(error) }
    finally { if (active(version)) setBusy(false) }
  }
  const qrVisible = qr && session?.state === "awaiting_confirmation" && !halted
  const directorySessionActive = !!session && ["authorized", "configuring", "test_pending", "ready"].includes(session.state) && !testRequested && !halted && !controller.current.signal.aborted
  function close() {
    if (closing.current) return
    const old = sessionRef.current
    setClearVersion(value => value + 1); importOperation.current = null; setImportStatus("none")
    controller.current.abort(); generation.current += 1; setQr(null); setDirectoryResult(null); setConfirmed(false)
    if (!old || terminal.has(old.state) || halted || old.state === "applying") { onClose(); return }
    closing.current = true; controller.current = new AbortController(); setBusy(true)
    const version = generation.current
    void api.cancelCloudAuthorization(provider, old.id, old.revision, controller.current.signal).then(value => {
      if (active(version)) { accept(value); onClose() }
    }).catch(error => { if (active(version)) failure(error) }).finally(() => { closing.current = false; if (active(version)) setBusy(false) })
  }
  return <div ref={dialog} onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); close() }
    if (event.key === "Tab") {
      const items = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled)')
      if (!items?.length) return
      const first = items[0], last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
  }}>
    <div className="operationsPanelHeading"><h3>{names[provider]}{intent === "unbind" ? "解除绑定" : builtin ? "免填应用信息授权" : "扫码授权"}</h3><button ref={closeButton} type="button" onClick={close}>关闭</button></div>
    {intent === "bind" ? <>
      {!builtin && <><p>使用您自己的开发者应用。申请权限：网盘文件读写与授权持续访问；厂商授权可能大于单个目录，ERP 只使用所选备份目录。</p><p>请使用对应网盘 App 扫码并确认。扫码完成、写入读回测试通过与已有可恢复云备份是三个不同阶段。</p></>}
      {busy && !session && <p role="status">正在读取应用与创建授权…</p>}
      {app?.prerequisites.map(reason => <p key={reason} className="operationsHelp">{api.cloudAuthorizationReason(reason)}</p>)}
      {session && <><p role="status">{builtin && session.state === "authorized" ? "授权已验证，尚未启用备份" : builtin && session.state === "expired" ? "授权或配置期限已过期" : labels[session.state]}</p><p>{builtin ? "本次导入或配置期限至" : "有效期至"}：{new Date(session.expires_at).toLocaleString("zh-CN")}</p>{session.error_code && <p role="alert">{api.cloudAuthorizationReason(session.error_code)}</p>}</>}
      {builtin && (!session || ["awaiting_import", "verifying_import"].includes(session.state)) && <CloudAuthorizationImportForm provider={provider} disabled={busy || halted || retryWait > 0 || !session || ["pending", "unknown"].includes(importStatus) || session.state !== "awaiting_import"} clearVersion={clearVersion} onSubmit={(access, refresh) => void submitImport(access, refresh)} />}
      {builtin && importStatus === "unknown" && <button type="button" disabled={busy || halted} onClick={() => { setImportStatus("retry"); setMessage("仅沿用原操作重试；请保持同一组授权结果，服务器将核对一致性。") }}>明确重试原操作</button>}
      {builtin && session?.verified_at && <p>最近授权验证：{new Date(session.verified_at).toLocaleString("zh-CN")}</p>}
      {builtin && session?.expiry_source === "unreported" && <p>令牌到期时间未知</p>}
      {builtin && session?.token_expires_at && <p>厂商报告令牌有效期至：{new Date(session.token_expires_at).toLocaleString("zh-CN")}</p>}
      {qrVisible && <div className="cloudAuthorizationQr">{qr.kind === "image" ? <img src={qr.content} alt={`${names[provider]}授权二维码`} /> : <QRCodeSVG value={qr.content} size={220} role="img" aria-label={`${names[provider]}授权二维码`} />}{qr.official_open_url && <a href={qr.official_open_url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">打开官方授权页面（需对应网盘 App）</a>}</div>}
      {session?.account_label && <p>候选账号：{session.account_label}</p>}
      {session?.account_label && ["authorized", "configuring", "test_pending", "ready"].includes(session.state) && (!directoryResult || directoryResult.status === "failed") && !testRequested && <button type="button" disabled={busy || halted || retryWait > 0} onClick={() => void action("directories")}>{session.selected_directory ? "重新选择备份目录" : "确认此账号并读取目录"}</button>}
      {directorySessionActive && directoryResult?.status === "pending" && <p role="status">目录读取中…</p>}
      {directorySessionActive && directoryResult?.status === "ready" && <div className="cloudAuthorizationDirectories" aria-label="网盘目录">{directoryResult.entries.length === 0 ? <p>此目录下暂无子目录，请选择其他目录。</p> : directoryResult.entries.map(entry => <div key={entry.path}><span>{entry.name}</span><button type="button" disabled={busy || halted || !entry.selectable || entry.path === "/"} onClick={() => void action("select", entry.path)}>选择 {entry.name}</button><button type="button" disabled={busy || halted} onClick={() => void action("directories", entry.path)}>打开 {entry.name}</button></div>)}<button type="button" disabled={busy || halted} onClick={() => void action("directories", "/")}>返回根目录</button></div>}
      {session?.selected_directory && <p>候选备份目录：{session.selected_directory}</p>}
      {session?.state === "test_pending" && <button type="button" disabled={busy || halted || retryWait > 0 || testRequested} onClick={() => void action("test")}>{testRequested ? "正在验证写入与完整读回" : "验证写入与完整读回"}</button>}
      {session?.state === "ready" && <div className="cloudAuthorizationCompare"><p>原配置：{currentTarget?.configured ? `连接器账号 ${currentTarget.username ?? "未知"} · ${currentTarget.selected_directory ?? currentTarget.remote_path ?? "未知目录"}；原网盘身份不可由连接器用户名确认` : "尚未配置"}</p><p>新配置：{session.account_label ?? "账号身份未确认"} · {session.selected_directory}</p><p>旧云备份文件保留；新授权可能影响厂商旧令牌，应用不代表已有新账号可恢复副本。受管目标还需真实网盘上传、令牌刷新与隔离恢复验收；完成前自动复制保持停用，可手动复制用于验收。</p><label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我确认替换为此账号与目录</label><button type="button" disabled={!confirmed || !session.account_label || !session.selected_directory || busy || halted || retryWait > 0} onClick={() => void action("apply")}>使用此账号与目录</button></div>}
      <div className="operationsCloudActions"><button type="button" disabled={busy || retryWait > 0 || (!builtin && (!app?.configured || !!app.prerequisites.length)) || haltReason === "permission" || (controller.current.signal.aborted && (!session || !terminal.has(session.state))) || session?.state === "applying" || importStatus !== "none"} onClick={() => void start(true)}>{builtin ? "重新获取导入会话" : "重新获取二维码"}</button>{session && !terminal.has(session.state) && <button type="button" disabled={busy || halted || retryWait > 0} onClick={() => void action("cancel")}>取消授权</button>}</div>
    </> : <><p>解除绑定仅停止 ERP 使用并清理对应受管凭据，不会删除云端备份。若官方撤销无法自动完成，仍需到网盘官方授权管理页面撤销。</p>{!unbinding ? <><label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我确认解除 ERP 绑定，保留云备份</label><button type="button" disabled={!confirmed || busy || halted || !currentTarget?.configured} onClick={() => void action("unbind")}>确认解除绑定</button></> : session && terminal.has(session.state) && session.state !== "applied" ? <p role="alert">解绑未完成：{api.cloudAuthorizationReason(session.error_code)}</p> : <p role="status">{session?.state === "applied" ? "ERP 解绑已完成；请到网盘官方页面撤销应用授权。" : "解绑清理正在进行，尚未完成。"}</p>}</>}
    {message && <p role="alert">{message}</p>}{retryWait > 0 && <p role="status">{retryWait} 秒后可重试；当前暂停轮询。</p>}
  </div>
}
