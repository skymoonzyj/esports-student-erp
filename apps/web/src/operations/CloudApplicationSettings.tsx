import { useEffect, useState } from "react"
import { CloudAuthorizationError, getCloudApplication, saveCloudApplication } from "./cloudAuthorizationApi"
import type { AuthProvider, CloudAppView } from "./cloudAuthorizationApi"
import { useCloudAuthorizationOwner, useCloudAuthorizationRequests } from "./CloudAuthorizationDialog"

type Props = { provider: AuthProvider; onSaved: (app: CloudAppView) => void }
export function CloudApplicationSettings(props: Props) {
  const owner = useCloudAuthorizationOwner()
  return owner ? <ApplicationForm key={`${owner}-${props.provider}`} {...props} /> : null
}
function ApplicationForm({ provider, onSaved }: Props) {
  const { controller, generation } = useCloudAuthorizationRequests()
  const [app, setApp] = useState<CloudAppView | null>(null)
  const [clientId, setClientId] = useState("")
  const [secret, setSecret] = useState("")
  const [clear, setClear] = useState(false)
  const [busy, setBusy] = useState(false)
  const [blocked, setBlocked] = useState(false)
  const [message, setMessage] = useState("")
  useEffect(() => {
    const version = generation.current
    void getCloudApplication(provider, controller.current.signal).then(value => {
      if (generation.current === version && !controller.current.signal.aborted) { setApp(value); setClientId(value.client_id ?? "") }
    }).catch(() => { if (generation.current === version && !controller.current.signal.aborted) setMessage("应用配置读取失败，请关闭后重试。") })
  }, [provider, controller, generation])
  async function save() {
    if (!app || busy || blocked) return
    const version = generation.current
    setBusy(true); setMessage("")
    try {
      const value = await saveCloudApplication(provider, { client_id: clientId.trim(), ...(secret.trim() ? { client_secret: secret } : {}), clear_secret: clear, expected_revision: app.revision }, controller.current.signal)
      if (generation.current !== version || controller.current.signal.aborted) return
      setSecret(""); setClear(false); setApp(value); onSaved(value); setMessage("自有应用已保存；只影响新授权，不会替换当前网盘配置。")
    } catch (error) {
      if (generation.current !== version || controller.current.signal.aborted) return
      setSecret("")
      setMessage(error instanceof CloudAuthorizationError ? error.message : "应用保存失败，请重新读取配置后重试。")
      if (error instanceof CloudAuthorizationError && (error.status === 401 || error.status === 403)) { setBlocked(true); setClientId(""); setApp(null); controller.current.abort(); generation.current += 1 }
    } finally { if (generation.current === version) setBusy(false) }
  }
  return <section className="cloudApplicationSettings" aria-label="自有应用设置">
    <h4>自有开发者应用</h4><p>请使用您自己申请的应用；应用密钥只写入，不回显或保存在浏览器。</p>
    <label>{provider === "baidu" ? "AppKey / API Key" : "AppID / Client ID"}<input value={clientId} maxLength={255} disabled={!app || busy || blocked} autoComplete="off" onChange={event => setClientId(event.target.value)} /></label>
    {provider === "baidu" ? <><p>请填写百度开发者应用的 AppKey / API Key，不是数字 AppID。</p><label>Secret Key / AppSecret<input type="password" value={secret} maxLength={4096} disabled={!app || busy || clear || blocked} autoComplete="new-password" onChange={event => setSecret(event.target.value)} /></label><p>{app?.secret_present ? "已保存密钥；留空保持原值。" : "百度应用需要 Secret Key / AppSecret 才能发起授权。"}</p><label><input type="checkbox" checked={clear} disabled={!app?.secret_present || busy || blocked} onChange={event => { setClear(event.target.checked); setSecret("") }} />明确清除已保存的应用密钥</label></> : <p>115 使用 PKCE 授权，不需要 AppSecret。</p>}
    <button type="button" disabled={!app || busy || blocked || !clientId.trim() || (provider === "baidu" && !secret.trim() && !app.secret_present && !clear)} onClick={() => void save()}>保存自有应用</button>
    {message && <p role="status">{message}</p>}
  </section>
}
