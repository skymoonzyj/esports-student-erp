import { useEffect, useState } from "react"
import type { AuthProvider } from "./cloudAuthorizationApi"
type Props = { provider: AuthProvider; disabled: boolean; clearVersion: number; onSubmit: (access: string, refresh: string) => void }
export function CloudAuthorizationImportForm({ provider, disabled, clearVersion, onSubmit }: Props) {
  const [consented, setConsented] = useState(false)
  const [access, setAccess] = useState("")
  const [refresh, setRefresh] = useState("")
  const [invalid, setInvalid] = useState(false)
  useEffect(() => { setAccess(""); setRefresh(""); setInvalid(false) }, [clearVersion, provider])
  function submit() {
    const values = [access.trim(), refresh.trim()]
    if (!consented || values.some(value => !value || value.length > 4096 || /\s/.test(value) || Array.from(value).some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) || /^(?:https?:|<|\{|\[)/i.test(value))) { setInvalid(true); return }
    setInvalid(false)
    onSubmit(values[0], values[1])
  }
  return <section className="cloudAuthorizationImport" aria-label="免填应用信息授权导入">
    <p>免填应用信息：外部授权后导入。使用 OpenList 提供的应用授权，第三方服务会处理网盘授权凭据。</p>
    {provider === "baidu" && <p>百度后续续期也依赖该第三方服务；本地加密不能消除此信任关系。</p>}
    <p>选择{provider === "baidu" ? "百度网盘" : "115 网盘"}及“使用 OpenList 提供的参数”，应用信息留空。二维码或登录方式由外部页面提供；ERP 无法读取扫码状态。密码仅在网盘登录页面输入。</p>
    <label><input type="checkbox" checked={consented} disabled={disabled} onChange={event => setConsented(event.target.checked)} />我已阅读并同意外部授权及第三方凭据处理说明</label>
    {consented && !disabled ? <a href="https://api.oplist.org/" target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">打开授权页面</a> : <button type="button" disabled>打开授权页面</button>}
    <p>返回后粘贴本次新取得、未被其他挂载使用的访问令牌和刷新令牌。两项属于同一次首次绑定，不需每次备份填写。请勿粘贴网页、Cookie、UID、AppKey、回调 URL 或图片；复用刷新链可能影响旧授权。</p>
    <label>访问令牌（Access Token）<input type="password" value={access} onChange={event => setAccess(event.target.value)} disabled={disabled} maxLength={4096} autoComplete="off" autoCapitalize="none" spellCheck={false} /></label>
    <label>刷新令牌（Refresh Token）<input type="password" value={refresh} onChange={event => setRefresh(event.target.value)} disabled={disabled} maxLength={4096} autoComplete="off" autoCapitalize="none" spellCheck={false} /></label>
    <button type="button" disabled={disabled || !consented} onClick={submit}>验证授权</button>
    {invalid && <p role="alert">请分别粘贴两项有效令牌，每项不超过 4096 字符，不可包含内部空白或其他授权材料。</p>}
  </section>
}
