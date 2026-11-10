export function RouteErrorPage() {
  return <main className="routeLoading" role="alert">
    <h1>页面暂时无法加载</h1>
    <p>请先另行记录未保存的内容，再手动刷新页面；刷新会丢失未保存的内容。若刷新后仍无法使用，请联系管理员。</p>
    <button className="secondaryButton" type="button" onClick={() => window.location.reload()}>刷新页面</button>
  </main>
}
