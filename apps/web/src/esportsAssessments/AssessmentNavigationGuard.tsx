import { useContext, useEffect, useRef, useState } from 'react'
import { UNSAFE_DataRouterContext, useBlocker } from 'react-router-dom'

interface Props {
  active: boolean
  pending: boolean
  volatile: boolean
  onLeave: () => Promise<boolean>
}

/** Route protection has no history mutation; standalone Hosts still get unload protection. */
export function AssessmentNavigationGuard(props: Props) {
  const router = useContext(UNSAFE_DataRouterContext)
  useEffect(() => {
    if (!props.active) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [props.active])
  return router ? <RouteGuard {...props} /> : null
}

function RouteGuard({ active, pending, volatile, onLeave }: Props) {
  const blocker = useBlocker(active)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  async function confirm() {
    if (busy || blocker.state !== 'blocked') return
    setBusy(true); setFailed(false)
    let allowed = false
    try { allowed = await onLeave() } catch { /* Keep the route blocked when exit cannot be verified. */ }
    if (!mounted.current) return
    setBusy(false)
    if (allowed) blocker.proceed()
    else setFailed(true)
  }
  if (blocker.state !== 'blocked') return null
  return <div style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'grid', placeItems: 'center', background: 'rgba(0,0,0,.65)' }}>
    <section role="dialog" aria-modal="true" aria-label="离开当前测试？" style={{ maxWidth: 480, padding: 24, margin: 16, borderRadius: 12, background: '#172033', color: '#fff' }}>
      <h3>离开当前测试？</h3>
      <p>{pending ? volatile ? '结果仅保存在当前页面，请先完成保存；现在不能安全退出。' : '完整结果已暂存，退出不会删除；稍后可回到本项手动重试保存。' : '测试计时不会暂停。继续测试会留在当前页面；确认退出将结束未完成尝试，不能恢复本次计时。'}</p>
      {failed && <p role="alert">退出未完成，请保留本页并重试或重新对账。</p>}
      <button type="button" disabled={busy} onClick={() => { setFailed(false); blocker.reset() }}>继续测试</button>
      <button type="button" disabled={busy || (pending && volatile)} onClick={() => void confirm()}>{busy ? '正在确认退出…' : '确认退出'}</button>
    </section>
  </div>
}
