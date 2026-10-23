import { Component, useEffect, useState, type ReactNode } from 'react'
import { useAuth } from '../../auth/AuthProvider'
import { readBuildInfo } from './buildInfo'
import { ModuleLoadError } from './lazyModule'
import './loading.css'

type Props = { moduleId: string; retry: () => void; children: ReactNode }

function Recovery({ retry, retryable }: { retry: () => void; retryable: boolean }) {
  const [changed, setChanged] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    void readBuildInfo(controller.signal).then(result => {
      if (!controller.signal.aborted) setChanged(result.kind === 'changed')
    })
    return () => controller.abort()
  }, [])
  return (
    <section className="moduleRecovery" role="alert">
      <p>页面模块加载失败，请检查网络后重试</p>
      {changed && <p>系统已更新</p>}
      {!retryable && <p>页面样式资源无法局部重试，请刷新页面。</p>}
      <div className="moduleRecoveryActions">
        <button type="button" onClick={retry} disabled={!retryable}>重试加载</button>
        <button type="button" onClick={() => {
          if (window.confirm('刷新会丢失尚未保存的内容，是否继续？')) location.reload()
        }}>刷新页面</button>
      </div>
    </section>
  )
}

class ImportBoundary extends Component<Props & { recoveryKey: string }, { error: unknown }> {
  state: { error: unknown } = { error: null }
  static getDerivedStateFromError(error: unknown) { return { error } }
  retry = () => { this.props.retry(); this.setState({ error: null }) }
  render() {
    const { error } = this.state
    if (error !== null) {
      if (!(error instanceof ModuleLoadError) || error.moduleId !== this.props.moduleId) throw error
      return <Recovery key={this.props.recoveryKey} retry={this.retry} retryable={error.retryable} />
    }
    return this.props.children
  }
}

export function ModuleBoundary(props: Props) {
  const { user, status } = useAuth()
  // Identity changes clear private children. Status-only transitions preserve a
  // successful form; only recovery UI/probes reset when status changes.
  // Existing authorization guards and provider lifetimes remain outside.
  return <ImportBoundary key={`${props.moduleId}:${user?.account_id ?? 'anonymous'}`} recoveryKey={status} {...props} />
}
