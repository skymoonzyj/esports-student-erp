import { Component, type ReactNode } from "react"

type ModuleErrorBoundaryProps = { children: ReactNode; moduleName: string }

export class ModuleErrorBoundary extends Component<ModuleErrorBoundaryProps, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return <section className="detailCard" role="alert">
      <h2>{this.props.moduleName}暂时无法加载</h2>
      <p>其他档案内容仍可使用。请先保存或另行记录未保存的内容，再手动刷新页面；刷新会丢失未保存的内容。</p>
      <button className="secondaryButton" type="button" onClick={() => window.location.reload()}>刷新页面</button>
    </section>
  }
}
