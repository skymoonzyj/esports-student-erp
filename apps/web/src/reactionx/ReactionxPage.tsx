import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth, type AuthUser } from '../auth/AuthProvider'
import { ProblemDetailsError } from '../api/client'
import { ConnectionPanel } from './ConnectionPanel'
import { RunPanel } from './RunPanel'
import { ReactionxReportsPanel } from './ReactionxReportsPanel'
import { UncertaintyPanel } from './UncertaintyPanel'
import { canControl, canReadReports, documentControl, errorText, identityKey, modeLabels, privateError, reactionxApi, type ClaimView, type ConnectorView, type ModeView, type PairingView, type PrepareInput, type RecoveryRequest, type RunView, type SubjectView } from './api'
import type { RunLayout } from './contracts'
import './reactionx.css'

export function ReactionxPage() {
  const { user } = useAuth()
  if (!user) return <p>请先登录。</p>
  return <AccountPage key={identityKey(user)} user={user} />
}
function AccountPage({ user }: { user: AuthUser }) {
  const manage = canControl(user)
  return <div className="rx-page"><header className="rx-heading"><div><p className="rx-eyebrow">REACTIONX · PHYSICAL TRAINING</p><h1>实体灯专项</h1><p>电脑连接、布局确认、独立保存。专项不计入原八项测试轮次。</p></div><Link to="/talent-tests">返回天赋测试</Link></header>
    <aside className="rx-notice"><strong>内部验证阶段</strong><span>八种固定规则模式尚未通过硬件验收，正式测试暂未开放。自由训练不参与排名。</span></aside>
    {manage ? <ControlWorkspace user={user} /> : <section className="rx-card"><p>当前账号仅可查看已授权报告，没有实体灯操控权限。</p></section>}
    {manage ? <UploadRecovery /> : null}
    {manage ? <UncertaintyPanel /> : null}
    {canReadReports(user) ? <ReactionxReportsPanel /> : null}
  </div>
}

function ControlWorkspace({ user }: { user: AuthUser }) {
  const account = identityKey(user), client = useQueryClient()
  const [proof] = useState(() => documentControl.forAccount(user.account_id))
  const [localId, setLocalId] = useState(proof.connectorId)
  const [runId, setRunId] = useState(proof.runId)
  const [pairing, setPairing] = useState<PairingView | null>(null)
  const [claim, setClaim] = useState<ClaimView | null>(null)
  const [busy, setBusy] = useState(false)
  const [stopPending, setStopPending] = useState(false)
  const [stopping, setStopping] = useState(proof.stoppingRunId === proof.runId && proof.runId !== null)
  const stopRequest = useRef<AbortController | null>(null), leaseRequest = useRef<AbortController | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [blocked, setBlocked] = useState(false)
  const alive = useRef(true), inflight = useRef(new Set<AbortController>())
  const windows = /Windows/i.test(navigator.userAgent)
  const key = ['reactionx', account, 'control']
  const connectors = useQuery({ queryKey: [...key, 'connectors'], queryFn: ({ signal }) => reactionxApi.connectors(signal), retry: false, gcTime: 0, refetchInterval: query => privateError(query.state.error) ? false : 5000, enabled: !blocked })
  const modes = useQuery({ queryKey: [...key, 'modes'], queryFn: ({ signal }) => reactionxApi.modes(signal), retry: false, gcTime: 0, enabled: !blocked })
  const release = useQuery({ queryKey: [...key, 'release'], queryFn: ({ signal }) => reactionxApi.release(signal), retry: false, gcTime: 0, enabled: !blocked })
  const run = useQuery({ queryKey: [...key, 'run', runId], queryFn: ({ signal }) => reactionxApi.run(runId!, signal), retry: false, gcTime: 0, enabled: !!runId && !blocked, refetchInterval: query => privateError(query.state.error) || ['completed', 'cancelled', 'uncertain_closed'].includes(query.state.data?.status ?? '') ? false : 2000 })
  const local = connectors.data?.find(item => item.connector_id === localId)
  const accessDenied = privateError(run.error) || privateError(connectors.error)
  const canOperate = windows && !blocked && !run.isError && documentControl.isCurrent(proof) && !connectors.isError && Boolean(local?.online && local.compatible && local.locally_confirmed && proof.connectorId === localId)
  const canStop = windows && !blocked && !accessDenied && documentControl.isCurrent(proof) && !!runId && proof.runId === runId && !run.data?.stop_confirmed

  useEffect(() => {
    alive.current = true
    const controllers = inflight.current
    const clear = () => {
      controllers.forEach(item => item.abort()); controllers.clear()
      setBlocked(true); setLocalId(null); setRunId(null); setPairing(null); setClaim(null)
    }
    window.addEventListener('reactionx-control-cleared', clear)
    return () => { alive.current = false; controllers.forEach(item => item.abort()); controllers.clear(); window.removeEventListener('reactionx-control-cleared', clear) }
  }, [])
  useEffect(() => {
    if (!accessDenied) return
    setError(errorText(run.error ?? connectors.error))
    documentControl.clear()
    const queryKey = ['reactionx', account, 'control']
    void client.cancelQueries({ queryKey })
    client.removeQueries({ queryKey })
  }, [accessDenied, account, client, run.error, connectors.error])
  // Polling is status-only; it never starts a run or adopts another document's proof.
  useEffect(() => {
    if (!pairing) return
    const controller = new AbortController()
    let active = true
    async function poll() {
      if (!active || Date.parse(pairing!.expires_at) <= Date.now()) return
      try {
        const next = await reactionxApi.pairing(pairing!.pairing_id, controller.signal)
        if (!active || !alive.current) return
        setClaim(next)
        if (next.status === 'active' && next.browser_confirmed && next.device_confirmed && next.connector_id && next.actor_account_id === user.account_id) {
          proof.connectorId = next.connector_id
          setLocalId(next.connector_id); setPairing(null); setClaim(null)
          void client.invalidateQueries({ queryKey: ['reactionx', account, 'control', 'connectors'] })
        }
      } catch (cause) {
        // B2 intentionally returns 404 until this known unexpired challenge is
        // claimed. It conveys no discovery permission, and is not a takeover.
        if (cause instanceof ProblemDetailsError && cause.problem.status === 404) return
        if (active) { setError(errorText(cause)); if (privateError(cause)) { setBlocked(true); active = false } }
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 1500)
    // This rerender also makes timeout visible when a connector never claims.
    const expiry = window.setTimeout(() => { if (active) setError('配对已超时，请取消后重新配对。') }, Math.min(300000, Math.max(0, Date.parse(pairing.expires_at) - Date.now())))
    return () => { active = false; controller.abort(); clearInterval(timer); clearTimeout(expiry) }
  }, [account, client, pairing, proof, user.account_id])
  useEffect(() => {
    if (!run.data || !canOperate || stopping || run.data.stop_confirmed || ['stopping', 'unconfirmed'].includes(run.data.status) || proof.runId !== run.data.run_id) return
    const controller = new AbortController(), id = run.data.run_id
    leaseRequest.current = controller
    const timer = window.setInterval(() => {
      // A hung non-stop request must not keep an uncertain run authorized forever.
      // Skipping a tick does not reset the timer for fast manual commands.
      if (inflight.current.size || proof.stoppingRunId === id || !documentControl.isCurrent(proof)) return
      void reactionxApi.lease(id, proof.tabId, controller.signal).catch(cause => { if (!controller.signal.aborted && alive.current) { setBlocked(true); setError(errorText(cause)) } })
    }, 5000)
    return () => { clearInterval(timer); controller.abort(); if (leaseRequest.current === controller) leaseRequest.current = null }
  }, [canOperate, proof, stopping, run.data?.run_id, run.data?.status, run.data?.stop_confirmed])
  useEffect(() => { if (run.data?.result_saved) void client.invalidateQueries({ queryKey: ['reactionx', account, 'reports'] }) }, [account, client, run.data?.result_saved])

  async function action(work: (signal: AbortSignal) => Promise<void>) {
    if (busy || blocked || !documentControl.isCurrent(proof) || (proof.stoppingRunId === runId && runId !== null && !run.data?.stop_confirmed)) return
    const controller = new AbortController(); inflight.current.add(controller)
    setBusy(true); setError(null)
    try { await work(controller.signal) } catch (cause) { if (alive.current && !controller.signal.aborted) { setError(errorText(cause)); if (privateError(cause)) setBlocked(true) } }
    finally { inflight.current.delete(controller); if (alive.current && !controller.signal.aborted) setBusy(false) }
  }
  function refresh() { void client.invalidateQueries({ queryKey: ['reactionx', account] }) }
  async function pair(signal: AbortSignal) {
    if (!windows || connectors.isError || !connectors.data || connectors.data.some(item => item.active_run_id || item.attention_required)) return
    const next = await reactionxApi.pair(proof.tabId, signal)
    if (!alive.current || signal.aborted) return
    if (!/^reactionx-erp:\/\/pair\?challenge=[A-Za-z0-9_-]{43}$/.test(next.pair_uri)) throw new Error('Invalid local URI')
    setPairing(next); setClaim(null)
    // A separate explicit click opens the local URI after this tab binding exists.
  }
  async function prepare(input: PrepareInput, signal: AbortSignal) {
    if (!canOperate || local?.attention_required) return
    const result = await reactionxApi.prepare(input, proof.tabId, signal)
    if (!alive.current || signal.aborted) return
    proof.runId = result.run_id; setRunId(result.run_id)
    proof.stoppingRunId = null; setStopping(false)
    client.setQueryData([...key, 'run', result.run_id], result); refresh()
  }
  async function operation(kind: 'start' | 'stop', signal: AbortSignal) {
    if (!canOperate || !runId || proof.runId !== runId) return
    const result = await reactionxApi.operation(runId, kind, proof.tabId, signal)
    if (!alive.current || signal.aborted) return
    client.setQueryData<RunView>([...key, 'run', runId], old => old ? { ...old, status: result.run_status } : old)
    refresh()
  }
  async function priorityStop() {
    if (!canStop || !runId || stopRequest.current) return
    const id = runId
    proof.stoppingRunId = id; setStopping(true)
    leaseRequest.current?.abort()
    inflight.current.forEach(controller => controller.abort()); inflight.current.clear()
    setBusy(false); setStopPending(true); setError(null)
    const controller = new AbortController(); stopRequest.current = controller; inflight.current.add(controller)
    const timeout = window.setTimeout(() => controller.abort(), 8000)
    try {
      await reactionxApi.operation(id, 'stop', proof.tabId, controller.signal)
      // Read status rather than promoting a 202 to physical OFF. The stop latch
      // survives late ready/running polls and SPA remounts until a new safe run.
      if (alive.current && !controller.signal.aborted && documentControl.isCurrent(proof)) refresh()
    } catch (cause) {
      if (alive.current && documentControl.isCurrent(proof)) setError(errorText(cause))
    } finally {
      clearTimeout(timeout); inflight.current.delete(controller)
      if (stopRequest.current === controller) stopRequest.current = null
      if (alive.current) setStopPending(false)
    }
  }
  async function download(signal: AbortSignal) {
    if (!release.data || !windows) return
    const blob = await reactionxApi.download(release.data, signal)
    if (!alive.current || signal.aborted) return
    const url = URL.createObjectURL(blob), link = document.createElement('a')
    link.href = url; link.download = 'ReactionXConnectorSetup.exe'; link.click(); URL.revokeObjectURL(url)
  }
  return <><div className="rx-toolbar"><p>操作员：{user.full_name} · 当前标签页独立控制</p><button onClick={refresh}>刷新状态</button></div>
    <div className="rx-workspace"><ConnectionPanel windows={windows} canManage={!blocked} release={release.data ?? null} connectors={connectors.data ?? []} pairedConnectorId={localId} pairing={pairing} claim={claim} busy={busy} error={error ?? (connectors.isError ? errorText(connectors.error) : release.isError ? '安装包信息读取失败，尚不能确认可用。' : null)} onPair={() => void action(pair)} onLaunch={() => { if (windows && !blocked && pairing && Date.parse(pairing.expires_at) > Date.now()) window.location.assign(pairing.pair_uri) }} onConfirm={() => void action(async signal => { if (!pairing || claim?.actor_account_id !== user.account_id || claim.pairing_id !== pairing.pairing_id || claim.verification_phrase !== pairing.verification_phrase) return; const next = await reactionxApi.confirm(pairing.pairing_id, pairing.verification_phrase, signal); if (alive.current && !signal.aborted) setClaim(next) })} onCancel={() => void action(async signal => { if (!pairing) return; await reactionxApi.cancel(pairing.pairing_id, signal); if (alive.current && !signal.aborted) { setPairing(null); setClaim(null) } })} onDownload={() => void action(download)} />
    <section className="rx-card" aria-label="固定规则模式"><div className="rx-section-heading"><span className="rx-step">02</span><h2>专项与训练</h2></div><p>固定规则测试 · 等待实体六灯验收</p><div className="rx-modes">{(Object.keys(modeLabels) as (keyof typeof modeLabels)[]).filter(mode => mode !== 'manual').map(mode => <div key={mode}><strong>{modeLabels[mode]}</strong><span>未开放 · 未验证</span></div>)}</div><h3>自由训练</h3><p>独立练习，不参与排名。只有服务器允许、当前电脑完成两端配对后才能准备。</p>{canOperate && local && !local.active_run_id && !local.attention_required && (!run.data || run.data.stop_confirmed) ? <PracticeSetup key={local.connector_id} connector={local} account={account} modes={modes.data ?? []} busy={busy} onPrepare={input => void action(signal => prepare(input, signal))} onDevice={(kind, payload) => void action(async signal => { await reactionxApi.device(local.connector_id, proof.tabId, kind, payload, signal); if (alive.current) refresh() })} /> : <p>完成本机配对、版本兼容和设备熄灯确认后，才可准备训练。</p>}</section></div>
    {!blocked && !accessDenied ? connectors.data?.filter(item => item.connector_id !== localId && item.active_run_id).map(item => <p className="rx-card" key={item.connector_id}>其他标签页的运行仅供恢复查看，不会取得控制权。<button onClick={() => setRunId(item.active_run_id)}>查看已有运行状态</button></p>) : null}
    {run.isError && !blocked ? <p role="alert">{errorText(run.error)}</p> : null}
    {run.isError && !accessDenied && run.data ? <p>以下仅为最后已知状态；新刺激与续租已暂停，仍可请求安全停止。</p> : null}
    {!blocked && !accessDenied ? <RunPanel run={run.data ?? null} canOperate={canOperate && proof.runId === runId} canStop={canStop} busy={busy} stopping={stopping} stopPending={stopPending} onStart={() => void action(signal => operation('start', signal))} onStop={() => void priorityStop()} onActivate={device => void action(async signal => { if (runId && canOperate && proof.runId === runId) await reactionxApi.activate(runId, proof.tabId, device, run.data?.manifest.config.color ?? 2, signal) })} /> : null}
    {!run.data && canStop ? <button disabled={stopPending} onClick={() => void priorityStop()}>停止并确认熄灯</button> : null}
  </>
}

function UploadRecovery() {
  const [runId, setRunId] = useState(''), [digest, setDigest] = useState(''), [reason, setReason] = useState('')
  const [checked, setChecked] = useState(false), [request, setRequest] = useState<RecoveryRequest | null>(null)
  const [status, setStatus] = useState<string | null>(null), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])
  const valid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId) && /^[0-9a-f]{64}$/.test(digest) && checked
  async function submit(kind: 'original' | 'request' | 'confirm') {
    if (busy || !valid || (kind === 'request' && reason.trim().length < 10)) return
    controller.current?.abort(); const current = new AbortController(); controller.current = current
    setBusy(true); setError(null); setStatus(null)
    try {
      if (kind === 'request') {
        const next = await reactionxApi.recover(runId, digest, reason.trim(), current.signal)
        if (!current.signal.aborted) setRequest(next)
      } else {
        const result = kind === 'original' ? await reactionxApi.authorizeUpload(runId, digest, current.signal) : request ? await reactionxApi.confirmRecovery(request.run_id, request.request_id, current.signal) : null
        if (!current.signal.aborted && result?.status === 'authorized' && result.run_id === runId && result.digest === digest) { setRequest(null); setStatus('已授权原连接器重传；尚未收到服务器保存回执，不代表结果已保存。请回原连接器重试同步。') }
      }
    } catch (cause) { if (!current.signal.aborted) setError(errorText(cause)) }
    finally { if (!current.signal.aborted) setBusy(false) }
  }
  function reset() { setRequest(null); setStatus(null); setChecked(false) }
  return <section className="rx-card"><h2>待同步原始结果恢复</h2><p>仅处理原连接器本地保留的原始结果包。请从连接器的待同步详情取得运行编号及摘要；不枚举他人的待同步记录，不改变原操作员或参测人员，也不会恢复设备控制。</p>
    <div className="rx-setup"><label>待同步运行编号<input autoComplete="off" value={runId} disabled={busy || !!request} onChange={event => { setRunId(event.target.value.trim()); reset() }} /></label><label>原始结果 SHA-256<input autoComplete="off" maxLength={64} value={digest} disabled={busy || !!request} onChange={event => { setDigest(event.target.value.trim()); reset() }} /></label><label className="rx-check"><input type="checkbox" checked={checked} disabled={busy || !!request} onChange={event => setChecked(event.target.checked)} />我已在原连接器核对运行编号、摘要和原参测人员，确认保留的是原始包。</label>
      <button disabled={!valid || busy || !!request} onClick={() => void submit('original')}>以原操作员重新授权上传</button><p>原操作员需重新登录并仍有授权；正常补传窗口由服务器核验。</p>
      <label>管理员恢复原因<textarea minLength={10} maxLength={500} value={reason} disabled={busy || !!request} onChange={event => setReason(event.target.value)} /></label>
      {request ? <div className="rx-pairing"><p>第一步申请已记录。再次核对原运行与摘要后才可授权；五分钟内有效，服务器会重新检查当前账号及权限。</p><p className="rx-digest">运行 {request.run_id} · 摘要 {request.digest}</p><button disabled={busy || Date.parse(request.expires_at) <= Date.now()} onClick={() => void submit('confirm')}>再次确认授权恢复</button><button disabled={busy} onClick={() => setRequest(null)}>返回，不授权</button></div> : <button disabled={!valid || busy || reason.trim().length < 10} onClick={() => void submit('request')}>申请管理员恢复</button>}
      {status ? <p role="status">{status}</p> : null}{error ? <p role="alert">{error}</p> : null}
    </div></section>
}

export function PracticeSetup({ connector, account, modes, busy, onPrepare, onDevice }: { connector: ConnectorView; account: string; modes: ModeView[]; busy: boolean; onPrepare: (input: PrepareInput) => void; onDevice: (kind: 'scan' | 'connect' | 'disconnect' | 'identify', payload: object) => void }) {
  const [search, setSearch] = useState(''), [person, setPerson] = useState<SubjectView | null>(null)
  const [keys, setKeys] = useState<string[]>([]), [confirmed, setConfirmed] = useState(false)
  const [posture, setPosture] = useState<RunLayout['posture']>('standing'), [spacing, setSpacing] = useState(100)
  const [color, setColor] = useState(2), [timeout, setTimeoutValue] = useState(3000)
  const people = useQuery({ queryKey: ['reactionx', account, 'subjects', search], queryFn: ({ signal }) => reactionxApi.people(search, signal), gcTime: 0, retry: false })
  const devices = keys.flatMap(key => { const item = connector.devices.find(candidate => candidate.device_key === key && candidate.connected); return item ? [item] : [] })
  function changeKeys(next: string[]) { setKeys(next); setConfirmed(false) }
  return <form className="rx-setup" onSubmit={event => { event.preventDefault(); if (!person || !confirmed || !devices.length || devices.length !== keys.length || !modes.some(mode => mode.mode === 'manual' && mode.available)) return
    onPrepare({ person_id: person.id, connector_id: connector.connector_id, mode: 'manual', devices: devices.map((device, order) => ({ device_key: device.device_key, display_number: device.display_number, protocol_version: device.protocol_version, order })), layout: { posture, positions: devices.map((device, index) => ({ device_key: device.device_key, x_cm: index * spacing, y_cm: 0, z_cm: posture === 'seated' ? 70 : 100 })) }, config: { lamp_count: devices.length, sensor: 0, flash: 0, on_sound: false, off_sound: false, color, stimulus_interval_ms: 500, timeout_ms: timeout, restart_interval_ms: null, duration_ms: null, task_count: null, seed: crypto.getRandomValues(new Uint32Array(1))[0], schedule_algorithm: 'balanced_cycle_v1', base_device_key: null, target_color: null, distractor_color: null } })
  }}>
    <label>搜索授权参测人员<input type="search" maxLength={80} value={search} onChange={event => { setSearch(event.target.value); setPerson(null); setConfirmed(false) }} /></label>
    {people.isError ? <p role="alert">授权人员读取失败；不会使用通用人员列表。</p> : <label>参测人员<select value={person?.id ?? ''} onChange={event => { setPerson(people.data?.find(item => item.id === event.target.value) ?? null); setConfirmed(false) }}><option value="">请选择已授权人员</option>{people.data?.map(item => <option key={item.id} value={item.id}>{item.name}{item.nickname ? `（${item.nickname}）` : ''}</option>)}</select></label>}
    <p>操作员与参测人员分别记录；仅显示当前授权范围内的最小人员信息。</p>
    <div className="rx-actions"><button type="button" disabled={busy} onClick={() => onDevice('scan', { duration_ms: 5000 })}>扫描实体灯</button></div>
    <fieldset disabled={busy}><legend>设备与明确顺序</legend>{connector.devices.length ? connector.devices.map(device => <div className="rx-device" key={device.device_key}><label><input type="checkbox" checked={keys.includes(device.device_key)} disabled={!device.connected} onChange={event => changeKeys(event.target.checked ? [...keys, device.device_key] : keys.filter(key => key !== device.device_key))} />{device.label} · 灯 {device.display_number} · {device.connected ? '已连接' : '未连接'}</label><button type="button" onClick={() => onDevice(device.connected ? 'disconnect' : 'connect', { device_keys: [device.device_key] })}>{device.connected ? '断开' : '连接'}</button><button type="button" disabled={!device.connected} onClick={() => onDevice('identify', { device_key: device.device_key, color: 2, duration_ms: 500 })}>识别</button></div>) : <p>尚未发现设备，请扫描后刷新状态。</p>}
    <ol>{devices.map((device, index) => <li key={device.device_key}>{device.label} · 灯 {device.display_number}<button type="button" aria-label={`上移第 ${index + 1} 位`} disabled={!index} onClick={() => { const next = [...keys]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; changeKeys(next) }}>上移</button></li>)}</ol></fieldset>
    <div className="rx-fields"><label>布局姿势<select value={posture} onChange={event => { setPosture(event.target.value as RunLayout['posture']); setConfirmed(false) }}><option value="standing">站立</option><option value="seated">坐姿</option></select></label><label>直线间距（厘米）<input type="number" min={10} max={500} step={1} value={spacing} onChange={event => { setSpacing(Number(event.target.value)); setConfirmed(false) }} /></label><label>灯光颜色<select value={color} onChange={event => { setColor(Number(event.target.value)); setConfirmed(false) }}><option value={2}>绿色</option><option value={1}>红色</option><option value={4}>蓝色</option></select></label><label>超时（毫秒）<input type="number" min={10} max={655350} step={10} value={timeout} onChange={event => { setTimeoutValue(Number(event.target.value)); setConfirmed(false) }} /></label></div>
    <p>直线布局：按上述顺序从左向右，第一盏 x=0，y=0；灯高 {posture === 'seated' ? 70 : 100} 厘米。请将实体位置调整一致。</p>
    <label className="rx-check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />已核对参测人员、设备顺序及现场布局；自由训练不参与排名。</label>
    <button className="primaryButton" disabled={busy || !confirmed || !person || !devices.length || devices.length !== keys.length || !modes.some(mode => mode.mode === 'manual' && mode.available)}>准备自由训练</button>
  </form>
}
