import type { ClaimView, ConnectorView, PairingView, ReleaseView } from './api'

export function ConnectionPanel({ windows, canManage, release, connectors, pairedConnectorId, busy, pairing, claim, error, onPair, onConfirm, onCancel, onDownload, onLaunch }: {
  windows: boolean; canManage: boolean; release: ReleaseView | null; connectors: ConnectorView[]; pairedConnectorId: string | null; busy: boolean; pairing: PairingView | null; claim: ClaimView | null; error: string | null;
  onPair: () => void; onConfirm: () => void; onCancel: () => void; onDownload: () => void; onLaunch?: () => void;
}) {
  const local = connectors.find(item => item.connector_id === pairedConnectorId)
  const expired = pairing && Date.parse(pairing.expires_at) <= Date.now()
  const unsafe = connectors.some(item => item.active_run_id || item.attention_required)
  return <section className="rx-card" aria-label="本机连接器"><div className="rx-section-heading"><span className="rx-step">01</span><h2>本机连接器</h2><span className="rx-badge">{local?.online ? '在线' : '未确认连接'}</span></div>
    {!local ? <p>未检测到本标签页已确认的连接器；这不代表未安装。其他标签页或电脑的在线状态不能证明本机已连接。</p> : <><p>{local.name} · {local.connector_version ?? '版本未知'}</p>{!local.compatible ? <p role="alert">连接器版本不兼容，仅可读取状态，请安装匹配版本。</p> : !local.online ? <p role="alert">连接器离线；操作不可用，已保存报告仍可查看。</p> : null}{local.attention_required ? <p role="alert">设备停止未确认，请在原连接器确认熄灯；不能开始新训练。</p> : null}</>}
    {!windows ? <p>实体灯控制仅支持 Windows；当前设备可查看已授权报告。</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {pairing ? <div className="rx-pairing">{expired ? <p role="alert">配对已超时，请取消后重新配对。</p> : <><h3>请在浏览器和本机连接器两端核对</h3><p>确认当前账号、站点和相同校验短语：</p>{claim ? <p>{claim.actor_name} · {claim.origin}</p> : <p>等待本机连接器响应…</p>}<strong>{pairing.verification_phrase}</strong><p>浏览器：{claim?.browser_confirmed ? '已确认' : '待确认'} · 连接器：{claim?.device_confirmed ? '已确认' : '待确认'}</p>
      {windows && canManage && onLaunch ? <button disabled={busy} onClick={onLaunch}>唤起本机连接器</button> : null}
      {windows && canManage && !claim?.browser_confirmed ? <button disabled={busy || !claim?.connector_id} onClick={onConfirm}>确认两端信息一致</button> : null}</>}
      <button disabled={busy} onClick={onCancel}>取消配对</button></div> : windows && canManage ? <><button className="primaryButton" disabled={busy || unsafe} onClick={onPair}>配对本机连接器</button>{unsafe ? <p>已有运行或停止未确认；请在原标签页与原连接器安全结束后再配对。</p> : null}</> : null}
    <div className="rx-install"><h3>Windows 连接器安装</h3>{release?.available ? <><p>版本 {release.version} · 仅内部测试</p><p>未嵌入 Authenticode 签名，尚未完成 Windows 与实体设备验收。请遵循系统安全提示，不要绕过保护。</p><p className="rx-digest">SHA-256：{release.sha256}</p>{windows && canManage ? <button disabled={busy} onClick={onDownload}>下载已核验安装包</button> : null}</> : <p>安装包尚未就绪</p>}</div>
  </section>
}
