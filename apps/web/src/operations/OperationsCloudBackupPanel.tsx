import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { formatAuditDateTime } from "../shared/date"
import { enqueueCloudBackup, getCloudBackups, saveCloudBackup, type CloudTarget } from "./cloudBackupApi"
import { CloudApplicationSettings } from "./CloudApplicationSettings"
import { CloudAuthorizationDialog, useCloudAuthorizationOwner, useCloudAuthorizationRequests } from "./CloudAuthorizationDialog"
import { cloudAuthorizationReason, getCloudApplication } from "./cloudAuthorizationApi"
import type { AuthMode, AuthProvider, CloudAppView } from "./cloudAuthorizationApi"

const names = { baidu: "百度网盘", "115": "115 网盘", quark: "夸克网盘（实验性）" }
const jobStatus = { queued: "排队中", running: "执行中", succeeded: "已完成", failed: "失败" }
const date = (value: string | null) => value ? formatAuditDateTime(value) : "暂无记录"
const reasons: Record<string, string> = {
  cloud_connector_failed: "连接器读写失败，请检查连接器状态与权限。",
  cloud_redirect_rejected: "连接器返回重定向，请配置为代理下载。",
  cloud_remote_corrupt: "远端备份校验失败，不能作为可恢复副本。",
  cloud_local_invalid: "本地备份校验失败，未上传。",
  cloud_no_local_backup: "没有可复制的完整本地备份。",
  cloud_disk_space: "备份校验空间不足。",
  cloud_invalid_path: "远端逻辑路径无效。",
  cloud_backup_failed: "备份复制失败，请检查备份和连接器。",
  cloud_worker_timeout: "运行器执行超时，请检查运行器状态。",
  cloud_credentials_invalid: "连接器凭据无效，请重新保存密码。",
  cloud_config_changed: "配置已变更，请重新测试连接。",
  cloud_provider_acceptance_required: "此受管目标待真实网盘上传、令牌刷新及隔离恢复验收，暂不能启用自动复制。",
  cloud_authorization_invalid: "此网盘授权已失效，请重新授权绑定；本地备份与其他网盘继续运行。",
}
export function cloudErrorReason(code: string | null) { return code ? reasons[code] ?? "操作未完成，请检查连接器与备份状态后重试。" : "无" }

type AuthorizationSelection = { provider: AuthProvider; intent: "bind" | "unbind"; mode: AuthMode }
function AuthorizationControls({ provider, target, onOpen }: { provider: AuthProvider; target: CloudTarget; onOpen: (value: AuthorizationSelection) => void }) {
  const { controller, generation } = useCloudAuthorizationRequests()
  const [app, setApp] = useState<CloudAppView | null>(null)
  const [settings, setSettings] = useState(false)
  const [message, setMessage] = useState("")
  useEffect(() => {
    const version = generation.current
    void getCloudApplication(provider, controller.current.signal).then(value => {
      if (generation.current === version && !controller.current.signal.aborted) setApp(value)
    }).catch(() => { if (generation.current === version && !controller.current.signal.aborted) setMessage("授权条件读取失败，请刷新页面后重试。") })
  }, [provider, controller, generation])
  const missing = app ? app.prerequisites : []
  return <section className="cloudAuthorizationControls" aria-label="原生网盘授权">
    <div className="operationsCloudActions"><button type="button" disabled={missing.some(code => code !== "cloud_app_unconfigured")} onClick={() => onOpen({ provider, intent: "bind", mode: "openlist_builtin_import" })}>免填应用信息绑定</button><button type="button" onClick={() => setSettings(value => !value)}>{settings ? "收起高级选项" : "高级选项：自有应用"}</button><button type="button" disabled={!target.configured || !target.revision} onClick={() => onOpen({ provider, intent: "unbind", mode: target.authorization_mode ?? "own_app" })}>解除 ERP 绑定</button></div>
    {settings && <><p>自有应用：直接扫码。缺少应用配置时不能使用此模式，不影响免填应用信息导入。</p><button type="button" disabled={!app?.configured || !!missing.length} onClick={() => onOpen({ provider, intent: "bind", mode: "own_app" })}>扫码绑定</button>{!app && !message && <p>正在读取扫码条件…</p>}{app && !app.configured && !missing.includes("cloud_app_unconfigured") && <p>请先配置自有应用，再发起扫码授权。</p>}{missing.map(code => <p key={code} className="operationsHelp">{cloudAuthorizationReason(code)}</p>)}<CloudApplicationSettings provider={provider} onSaved={setApp} /></>}
    {message && <p role="alert">{message}</p>}
    <p className="operationsHelp">授权验证和连接测试通过不代表已有云备份。原 WebDAV 手动配置继续保留。</p>
  </section>
}

function TargetCard({ target, busyJob, refresh, onAuthorize }: { target: CloudTarget; busyJob: boolean; refresh: () => Promise<unknown>; onAuthorize?: (value: AuthorizationSelection) => void }) {
  const [path, setPath] = useState(target.remote_path ?? "")
  const [username, setUsername] = useState(target.username ?? "")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState(false)
  const managed = target.managed === true
  const authorizationValid = target.authorization_status === "valid"
  const dirty = path !== (target.remote_path ?? "") || username !== (target.username ?? "") || password !== ""
  async function action(kind: "save" | "enable" | "test" | "copy") {
    setBusy(true); setMessage(""); setError(false)
    try {
      if (kind === "save" || kind === "enable") {
        await saveCloudBackup(target.provider, { remote_path: path, username, enabled: kind === "enable" ? !target.enabled : dirty ? false : target.enabled, ...(password ? { password } : {}) })
        setPassword("")
        setMessage(kind === "save" ? "配置已保存；配置变更后需重新测试连接。" : "自动复制设置已更新。")
      } else {
        await enqueueCloudBackup(target.provider, kind)
        setMessage(kind === "test" ? "写入/读回测试已排队，尚未完成。" : "最新完整备份复制已排队，尚未验证成功。")
      }
      await refresh()
    } catch { setError(true); setMessage("操作失败，请检查配置、连接器或进行中的任务后重试。") }
    finally { setBusy(false) }
  }
  return <fieldset className="operationsCloudCard" disabled={busy || busyJob}>
    <legend>{names[target.provider]}</legend>
    {target.provider === "quark" ? <p className="operationsHelp">实验性连接器，非官方维护接口，可能因网盘变更而失效；本期不开放扫码，保留手动配置。</p> : onAuthorize && <AuthorizationControls provider={target.provider} target={target} onOpen={onAuthorize} />}
    {managed && <p>当前授权来源：{target.authorization_mode === "own_app" ? "自有应用：直接扫码" : target.authorization_mode === "openlist_builtin_import" ? "免填应用信息：外部授权后导入" : "授权来源未知"}</p>}
    {managed && <p className="operationsHelp">受管目标待真实网盘上传、令牌刷新及隔离恢复验收，暂不能启用自动复制；授权与连接探测不代表已有可恢复备份。可手动复制完整备份用于验收。账号与目录由授权配置管理，需重新绑定才能更换。</p>}
    {managed && !authorizationValid && <p className="formError">此网盘授权已失效或尚待确认，已停止新上传；请重新授权绑定，本地备份与其他网盘继续运行。</p>}
    <label>远端逻辑路径<input value={managed ? target.selected_directory ?? path : path} disabled={managed} onChange={e => setPath(e.target.value)} placeholder="/网盘挂载/ERP备份" autoComplete="off" /></label>
    <label>连接器用户名<input value={username} disabled={managed} onChange={e => setUsername(e.target.value)} autoComplete="off" /></label>
    <label>连接器密码<input type="password" value={password} disabled={managed} onChange={e => setPassword(e.target.value)} autoComplete="new-password" /></label>
    <small>{target.credential_present ? "已保存密码；留空保持原密码，不会回显。" : "尚未保存连接器密码。"}</small>
    <p>连接：{target.connection_status === "ok" ? "写入/读回测试通过" : target.connection_status === "failed" ? "测试失败" : "待测试"} · 自动复制：{target.enabled ? "已启用" : "未启用"}</p>
    <p>最近连接测试：{date(target.last_tested_at)}</p>
    <p>{target.last_success_at ? `${managed && !authorizationValid ? "历史云备份记录（不代表当前授权可用）" : "已验证云备份"}：${date(target.last_success_at)} · ${target.last_backup_id ?? ""}` : "暂无已验证云备份"}</p>
    {target.last_error_code && <p className="formError">{cloudErrorReason(target.last_error_code)}</p>}
    <div className="operationsCloudActions">
      <button type="button" disabled={managed} onClick={() => void action("save")}>保存配置</button>
      <button type="button" disabled={dirty || !target.configured || (managed && !authorizationValid)} onClick={() => void action("test")}>写入/读回测试</button>
      <button type="button" disabled={dirty || (!target.enabled && (managed || target.connection_status !== "ok"))} onClick={() => void action("enable")}>{target.enabled ? "停用自动复制" : "启用自动复制"}</button>
      <button type="button" disabled={dirty || target.connection_status !== "ok" || (managed ? !authorizationValid : !target.enabled)} onClick={() => void action("copy")}>{managed ? "手动复制完整备份用于验收" : "重试复制最新完整备份"}</button>
    </div>
    {busyJob && <p role="status">此目标有任务正在执行或排队，请完成后再操作。</p>}
    {message && <p role={error ? "alert" : "status"} className={error ? "formError" : "operationsHelp"}>{message}</p>}
  </fieldset>
}
/** Auth lifecycle owns this entire layer. Logout unmounts it, even on same-account login. */
function CloudTargets({ data, owner, refresh }: { data: Awaited<ReturnType<typeof getCloudBackups>>; owner: string | null; refresh: () => Promise<unknown> }) {
  const [dialog, setDialog] = useState<AuthorizationSelection | null>(null)
  return <><div className="operationsCloudGrid">{data.targets.map(target => <TargetCard key={`${target.provider}-${target.revision}`} target={target} busyJob={data.jobs.some(job => job.provider === target.provider && (job.status === "queued" || job.status === "running"))} refresh={refresh} onAuthorize={owner ? setDialog : undefined} />)}</div>
    {owner && dialog && <CloudAuthorizationDialog key={`${dialog.provider}-${dialog.mode}`} provider={dialog.provider} mode={dialog.mode} intent={dialog.intent} currentTarget={data.targets.find(target => target.provider === dialog.provider)} onClose={() => setDialog(null)} onApplied={() => { void refresh() }} />}
  </>
}
export function OperationsCloudBackupPanel({ autoRefresh }: { autoRefresh: boolean }) {
  const owner = useCloudAuthorizationOwner()
  const query = useQuery({ queryKey: ["operations", "cloud-backups"], queryFn: ({ signal }) => getCloudBackups(signal), refetchInterval: q => autoRefresh || q.state.data?.jobs.some(j => j.status === "queued" || j.status === "running") ? 5000 : false })
  return <section className="operationsCloudPanel" aria-label="云端加密备份">
    <div className="operationsPanelHeading"><h3>云端加密备份</h3><button type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新云备份状态</button></div>
    <p className="operationsHelp">ERP 每日北京时间 03:00 创建本地备份；独立运行器每两分钟检查并复制到已启用目标。百度与 115 默认免填应用信息，外部授权后导入；自有应用直接扫码在高级选项中保留。两种模式均需私有连接器和在线授权运行器；夸克继续使用实验性手动配置。配置或连接测试通过不代表已有可恢复的云备份。</p>
    <p className="operationsHelp">只复制加密完整备份；恢复密钥需单独离线保管，不会上传。远端历史版本需手动清理，本系统不会删除云端备份。</p>
    {query.isPending ? <p>正在读取云备份状态…</p> : !query.data ? <p role="alert" className="formError">云备份状态读取失败，请重试。</p> : <>
      {query.isError && <p role="alert" className="formError">云备份状态刷新失败，保留上次状态与当前授权表单，请稍后重试。</p>}
      <p>运行器最近在线：{date(query.data.worker_last_seen_at)}</p>
      <CloudTargets key={owner ?? "unauthenticated"} data={query.data} owner={owner} refresh={query.refetch} />
      <h4>最近任务</h4>
      {query.data.jobs.length === 0 ? <p>暂无云备份任务</p> : <div className="operationsCloudJobs">{query.data.jobs.map(job => <article key={job.id}><strong>{names[job.provider]} · {job.kind === "test" ? "连接测试" : "备份复制"} · {jobStatus[job.status]}</strong><p>创建：{date(job.created_at)} · 开始：{date(job.started_at)} · 完成：{date(job.completed_at)}</p>{job.backup_id && <p>备份：{job.backup_id}</p>}{job.error_code && <p className="formError">{cloudErrorReason(job.error_code)}</p>}</article>)}</div>}
    </>}
  </section>
}
