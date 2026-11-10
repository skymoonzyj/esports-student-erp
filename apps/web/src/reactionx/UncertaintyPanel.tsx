import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { ProblemDetailsError } from '../api/client'
import { useAuth } from '../auth/AuthProvider'
import { formatShanghaiDateTime } from '../teaching/datetime'
import { canControl, identityKey, modeLabels, privateError, reactionxApi } from './api'
import type { ArchiveRequest, UncertaintyItem, UncertaintyReceipt } from './uncertainty'

function failureText(cause: unknown) {
  if (cause instanceof ProblemDetailsError) {
    if (cause.problem.code === 'reactionx_uncertainty_request_expired') return '确认请求已过期，请刷新并重新核对、发起申请。'
    if (privateError(cause)) return '记录不存在或无权操作，请重新确认当前账号权限。'
    if (cause.problem.status === 409) return '归档条件不符或原始证据已变化，需重新核对。必须由原连接器确认原设备实际关灯；本地已有接受开始命令、刺激或结果的记录不能用此入口处理。'
    if (cause.problem.status === 422) return '归档申请未通过校验，请填写 10–500 字的有效处理原因，重新核对原始证据。'
  }
  return '请求失败，状态尚未确认。请刷新核对后台状态；不要沿用旧确认请求。'
}

export function UncertaintyPanel() {
  const { user } = useAuth()
  if (!user || !canControl(user)) return null
  return <UncertaintyReview key={identityKey(user)} account={identityKey(user)} accountId={user.account_id} />
}

function UncertaintyReview({ account, accountId }: { account: string; accountId: string }) {
  const client = useQueryClient(), [denied, setDenied] = useState(false), [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<UncertaintyItem | null>(null), [reason, setReason] = useState('')
  const [confirmation, setConfirmation] = useState<ArchiveRequest | null>(null), [archived, setArchived] = useState<UncertaintyReceipt | null>(null)
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false)
  const alive = useRef(true), blocked = useRef(false), controller = useRef<AbortController | null>(null)
  const dialog = useRef<HTMLDialogElement | null>(null), cancelButton = useRef<HTMLButtonElement | null>(null)
  const records = useQuery({ queryKey: ['reactionx', account, 'uncertainties', offset], queryFn: ({ signal }) => reactionxApi.uncertainties(offset, signal), gcTime: 0, retry: false, staleTime: 0, enabled: !denied, refetchOnMount: 'always', refetchOnWindowFocus: false })
  const currentSelection = selected && records.data?.items.some(item => item.run_id === selected.run_id && item.declaration_digest === selected.declaration_digest && item.subject_name === selected.subject_name && item.mode === selected.mode && item.created_at === selected.created_at)

  useEffect(() => {
    if ((!records.isFetching && !records.isError && currentSelection) || !selected) return
    controller.current?.abort(); controller.current = null
    setBusy(false); setSelected(null); setReason(''); setConfirmation(null)
  }, [records.isFetching, records.isError, currentSelection, selected])

  useEffect(() => {
    alive.current = true
    const queryKey = ['reactionx', account, 'uncertainties']
    const clear = () => {
      blocked.current = true; controller.current?.abort(); controller.current = null
      setDenied(true); setSelected(null); setReason(''); setConfirmation(null); setArchived(null); setBusy(false)
      void client.cancelQueries({ queryKey }); client.removeQueries({ queryKey })
    }
    const boundary = (event: Event) => { if ((event as CustomEvent<string | null>).detail !== accountId) clear() }
    const foreground = () => {
      if (blocked.current || document.visibilityState === 'hidden') return
      controller.current?.abort(); controller.current = null
      setBusy(false); setSelected(null); setReason(''); setConfirmation(null); setArchived(null); setError(null)
      void client.invalidateQueries({ queryKey })
    }
    window.addEventListener('reactionx-auth-boundary', boundary)
    window.addEventListener('reactionx-before-logout', clear)
    window.addEventListener('focus', foreground)
    document.addEventListener('visibilitychange', foreground)
    return () => {
      alive.current = false; controller.current?.abort()
      void client.cancelQueries({ queryKey }); client.removeQueries({ queryKey })
      window.removeEventListener('reactionx-auth-boundary', boundary)
      window.removeEventListener('reactionx-before-logout', clear)
      window.removeEventListener('focus', foreground)
      document.removeEventListener('visibilitychange', foreground)
    }
  }, [account, accountId, client])

  useEffect(() => {
    if (!privateError(records.error)) return
    blocked.current = true; controller.current?.abort(); controller.current = null
    setDenied(true); setSelected(null); setReason(''); setConfirmation(null); setArchived(null); setBusy(false); setError(failureText(records.error))
    const queryKey = ['reactionx', account, 'uncertainties']
    void client.cancelQueries({ queryKey }); client.removeQueries({ queryKey })
  }, [records.error, account, client])

  useEffect(() => {
    if (!denied) return
    // Remove again after the disabled observer has rendered: otherwise React
    // Query can recreate an empty entry while applying enabled=false.
    const queryKey = ['reactionx', account, 'uncertainties']
    const cancelled = client.cancelQueries({ queryKey })
    client.removeQueries({ queryKey })
    void cancelled.finally(() => client.removeQueries({ queryKey }))
  }, [denied, account, client])

  useEffect(() => {
    if (!confirmation) return
    const expire = () => { setConfirmation(null); setSelected(null); setReason(''); setError('确认请求已过期，请刷新并重新核对、发起申请。') }
    const timer = window.setTimeout(expire, Math.min(300_000, Math.max(0, Date.parse(confirmation.expires_at) - Date.now())))
    return () => clearTimeout(timer)
  }, [confirmation])
  useEffect(() => {
    if (!confirmation || !dialog.current) return
    // Native modal handles focus containment and makes the rest of the page inert.
    if (typeof dialog.current.showModal === 'function') dialog.current.showModal()
    else dialog.current.setAttribute('open', '')
    cancelButton.current?.focus()
  }, [confirmation])

  function reset() { setSelected(null); setReason(''); setConfirmation(null); setArchived(null) }
  function refresh(nextOffset = offset) {
    if (blocked.current || controller.current) return
    reset(); setError(null)
    if (nextOffset !== offset) setOffset(nextOffset)
    else void records.refetch()
  }
  async function submit(confirm: boolean) {
    const row = selected, request = confirmation
    if (blocked.current || controller.current || !row || !currentSelection || records.isFetching || records.isError) return
    if (confirm ? !request || request.run_id !== row.run_id || request.declaration_digest !== row.declaration_digest || Date.parse(request.expires_at) <= Date.now() : reason.trim().length < 10 || reason.trim().length > 500) return
    const current = new AbortController(); controller.current = current; setBusy(true); setError(null)
    try {
      if (!confirm) {
        const next = await reactionxApi.requestUncertaintyArchive(row.run_id, row.declaration_digest, reason.trim(), current.signal)
        if (alive.current && !blocked.current && !current.signal.aborted) setConfirmation(next)
      } else {
        const receipt = await reactionxApi.confirmUncertaintyArchive(row.run_id, request!.request_id, row.declaration_digest, current.signal)
        if (alive.current && !blocked.current && !current.signal.aborted) { reset(); setArchived(receipt); void client.invalidateQueries({ queryKey: ['reactionx', account, 'uncertainties'] }) }
      }
    } catch (cause) {
      if (!alive.current || blocked.current || current.signal.aborted) return
      reset(); setError(failureText(cause))
      if (privateError(cause)) {
        blocked.current = true; setDenied(true)
        const queryKey = ['reactionx', account, 'uncertainties']
        void client.cancelQueries({ queryKey }); client.removeQueries({ queryKey })
      }
    } finally {
      if (controller.current === current) { controller.current = null; if (alive.current) setBusy(false) }
    }
  }

  const unavailable = denied || records.isError || records.isFetching || !records.data
  return <section className="rx-card rx-uncertainties" aria-label="执行未知待核对"><h2>执行未知待核对</h2>
    <p>仅处理原连接器提交的开始送达未知证据，不能断言未执行。不生成成绩、不参与排名，不改变原操作员、参测人员或连接器归属。</p>
    <p>必须由原连接器对原设备实际关灯，并由服务器核验确认；网页确认不能代替设备确认。归档后不自动开始或续跑。</p>
    {error || records.isError ? <p role="alert">{error ?? failureText(records.error)}</p> : null}
    {denied ? <p>当前处理已停止，请重新登录核对账号权限后重新发起。</p> : <>
      {records.isFetching ? <p role="status">正在核对当前授权范围内的异常记录…</p> : null}
      {!unavailable ? <><ul className="rx-uncertainty-list">{records.data!.items.filter(item => item.run_id !== archived?.run_id).map(item => <li key={item.run_id}>
        <div className="rx-section-heading"><h3>{item.subject_name}</h3><span className="rx-badge">执行未知 · 待核对</span></div>
        <dl className="rx-uncertainty-facts"><dt>运行编号</dt><dd>{item.run_id}</dd><dt>专项</dt><dd>{modeLabels[item.mode]}</dd><dt>原运行创建时间</dt><dd>{formatShanghaiDateTime(item.created_at)}（中国时间）</dd><dt>原始异常声明 SHA-256</dt><dd>{item.declaration_digest}</dd></dl>
        <button disabled={busy || !!selected} onClick={() => { setSelected(item); setReason(''); setError(null); setArchived(null) }}>核对并填写原因</button>
      </li>)}</ul>{!records.data!.items.length ? <p>暂无执行未知待核对记录</p> : null}</> : null}
      {selected && currentSelection && !confirmation && !unavailable ? <form className="rx-setup rx-pairing" onSubmit={event => { event.preventDefault(); void submit(false) }}>
        <h3>第一步 · 核对原始异常</h3><p className="rx-digest">当前核对：{selected.subject_name} · {selected.run_id} · {selected.declaration_digest}</p>
        <label>异常归档原因<textarea autoComplete="off" minLength={10} maxLength={500} value={reason} disabled={busy} onChange={event => setReason(event.target.value)} /></label><p>填写 10–500 字处理原因；第一步仅记录申请，不会归档。</p>
        <div className="rx-actions"><button type="submit" disabled={busy || reason.trim().length < 10 || reason.trim().length > 500}>发起归档核对</button><button type="button" disabled={busy} onClick={reset}>返回，不发起申请</button></div>
      </form> : null}
      {confirmation && selected && currentSelection && !unavailable ? <dialog ref={dialog} className="rx-uncertainty-dialog" aria-labelledby="rx-uncertainty-confirm-title" onCancel={event => { if (busy) event.preventDefault(); else reset() }}>
        <h3 id="rx-uncertainty-confirm-title">再次确认异常归档</h3><p>第二步 · 服务器将重新核验当前账号权限、原始证据及原设备实际关灯确认。</p>
        <p>{selected.subject_name} · {modeLabels[selected.mode]}</p><p className="rx-digest">运行：{selected.run_id}<br />声明：{selected.declaration_digest}</p><p>处理原因：{reason}</p>
        <p>确认后仅归档为“执行状态未知，需复测”，不生成成绩、不参与排名。不能替代为未执行或取消成功。</p><p>请求有效至 {formatShanghaiDateTime(confirmation.expires_at)}（中国时间）；过期或证据变化需重新发起。</p>
        <div className="rx-actions"><button ref={cancelButton} disabled={busy} onClick={reset}>返回，不归档</button><button disabled={busy || Date.parse(confirmation.expires_at) <= Date.now()} onClick={() => void submit(true)}>确认归档为执行未知</button></div>
      </dialog> : null}
      <nav className="rx-actions" aria-label="执行未知记录分页"><button disabled={busy || !!confirmation || offset === 0 || unavailable} onClick={() => refresh(Math.max(0, offset - 20))}>上一页</button><span>第 {Math.floor(offset / 20) + 1} 页</span><button disabled={busy || !!confirmation || unavailable || offset + 20 >= (records.data?.total ?? 0)} onClick={() => refresh(offset + 20)}>下一页</button><button disabled={busy} onClick={() => refresh()}>刷新执行未知记录</button></nav>
    </>}
    {archived && !denied ? <div role="status" className="rx-pairing"><h3>执行状态未知，需复测</h3><p>后台已归档，原记录和审计保留，不生成成绩、不参与排名。</p><p className="rx-digest">运行 {archived.run_id} · 归档编号 {archived.archive_id}</p><p>尚不能确认原连接器已收到并持久化归档凭据；本地待处理配额是否释放，请回原连接器核对。不会自动开始新测试。</p></div> : null}
  </section>
}
