import { useEffect, useRef, useState } from "react"
import { ProblemDetailsError } from "../api/client"
import { quickImportApi } from "./quickImportApi"
import type { QuickPreview, QuickResult, TeamChoices } from "./quickImportApi"
import "./quickImport.css"

type Props = { accountId: string; enabled: boolean; revalidating: boolean }
type Outcome = { kind: "success"; result: QuickResult } | { kind: "error" | "unknown"; message: string }

export function QuickImportPanel({ accountId, enabled, revalidating }: Props) {
  // Unmount the entire private editor when authority or account identity changes.
  if (!enabled) return <p role="alert">当前无一键建档权限，导入草稿已清除。</p>
  return <ImportEditor key={accountId} revalidating={revalidating} />
}

function ImportEditor({ revalidating }: { revalidating: boolean }) {
  const [text, setText] = useState("")
  const [preview, setPreview] = useState<QuickPreview | null>(null)
  const [choices, setChoices] = useState<TeamChoices>({})
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState("")
  const [denied, setDenied] = useState(false)
  const [outcomes, setOutcomes] = useState<Record<number, Outcome>>({})
  const keys = useRef<Record<number, string>>({})
  const active = useRef<AbortController | null>(null)
  const generation = useRef(0)
  const authorityRefreshing = useRef(revalidating)
  authorityRefreshing.current = revalidating
  useEffect(() => () => { generation.current++; active.current?.abort() }, [])
  const ready = preview?.rows.filter(row => row.ticket && row.errors.length === 0 && outcomes[row.row_number]?.kind !== "success") ?? []
  const succeeded = Object.values(outcomes).filter(item => item.kind === "success").length
  const attempted = Object.keys(outcomes).length > 0

  function clearPrivate() {
    setText(""); setPreview(null); setChoices({}); setOutcomes({}); keys.current = {}; setConfirmed(false)
  }
  function permissionFailure(error: unknown) {
    if (error instanceof ProblemDetailsError && [401, 403].includes(error.problem.status)) {
      generation.current++; active.current?.abort(); clearPrivate(); setDenied(true)
      setMessage("权限已变更或登录已失效，导入草稿已清除，请重新进入管理建档。")
      setBusy(false)
      return true
    }
    return false
  }
  async function recognize(nextChoices = choices) {
    if (busy || revalidating || denied) return
    const current = ++generation.current
    active.current?.abort()
    const controller = new AbortController(); active.current = controller
    setBusy(true); setMessage(""); setConfirmed(false)
    try {
      const next = await quickImportApi.preview(text, preview?.import_date, nextChoices, controller.signal)
      if (current !== generation.current || controller.signal.aborted) return
      setPreview(next); setChoices(nextChoices); setOutcomes({})
      keys.current = Object.fromEntries(next.rows.filter(row => row.ticket).map(row => [row.row_number, crypto.randomUUID()]))
    } catch (error) {
      if (current !== generation.current || controller.signal.aborted) return
      if (!permissionFailure(error)) {
        setPreview(null)
        setMessage(error instanceof ProblemDetailsError ? error.problem.detail : "识别失败，请检查网络后重试；尚未写入任何人员。")
      }
    } finally { if (current === generation.current) setBusy(false) }
  }
  async function submit() {
    if (!confirmed || busy || revalidating || denied || !ready.length) return
    const current = ++generation.current
    const controller = new AbortController(); active.current = controller
    setBusy(true); setMessage("")
    for (const row of ready) {
      if (controller.signal.aborted || current !== generation.current || authorityRefreshing.current) break
      try {
        const result = await quickImportApi.commit(row.ticket!, keys.current[row.row_number], controller.signal)
        if (controller.signal.aborted || current !== generation.current) return
        setOutcomes(old => ({ ...old, [row.row_number]: { kind: "success", result } }))
      } catch (error) {
        if (controller.signal.aborted || current !== generation.current) return
        if (permissionFailure(error)) return
        const certainFailure = error instanceof ProblemDetailsError && error.problem.status >= 400 && error.problem.status < 500
        setOutcomes(old => ({ ...old, [row.row_number]: certainFailure
          ? { kind: "error", message: (error as ProblemDetailsError).problem.detail }
          : { kind: "unknown", message: "结果尚未确认，请用原操作重试；不要重复建立档案。" } }))
        // Unknown results may be a disconnected server. Stop the batch, keep the exact keys.
        if (!certainFailure) break
      }
    }
    if (current === generation.current) setBusy(false)
  }
  function reset() {
    generation.current++; active.current?.abort(); clearPrivate(); setMessage("")
  }
  return <section className="quickImportPanel" aria-label="一键建档入住">
    <h2>一键建档并安排入住</h2>
    <p>支持 Excel 复制的表格、Markdown 表格及空格分隔文本，先预览，再确认导入。每行最多创建一份新档案和一条入住记录；冲突行不写入。</p>
    <p>表格列顺序：房号｜房型（可省略此列）｜姓名｜入住日期｜岗位/项目/队伍｜离开日期｜身份证号｜手机号。表格日期留空时，入住按本次识别日期；离开日期留空或填“常驻／常住”均按长期入住，最多 100 行。</p>
    <p>空格文本顺序：房号 房型（可省略） 姓名 入住日期 项目/队伍 岗位 离开日期（可省略） 身份证号 手机号；末尾也可单独加“常驻”或“常住”。入住日期须明确填写，“经理”按队伍经理识别；姓名含空格或字段无法确定时请使用表格格式。</p>
    <p>已有证件、其他证件类型或未成年监护资料缺失时，请转手动建档核实。不会自动更改房型、房间状态或已有人员。建档不等于账号激活或角色授权。</p>
    {message ? <p role="alert">{message}</p> : null}
    {!denied ? <>
      <label className="quickImportSource">粘贴人员表格<textarea rows={5} maxLength={200000} autoComplete="off" spellCheck={false} value={text}
        disabled={busy || attempted} onChange={event => { setText(event.target.value); setPreview(null); setChoices({}); setConfirmed(false); keys.current = {} }} /></label>
      <div className="quickImportActions"><button type="button" disabled={busy || revalidating || !text.trim() || attempted} onClick={() => void recognize()}>识别并预览</button>
        <button type="button" className="secondaryButton" disabled={busy} onClick={reset}>清空并开始新一批</button></div>
      {busy ? <p role="status">正在处理，请保持页面打开…</p> : null}
      {preview ? <>
        <p>本次默认入住日期：{preview.import_date}。共 {preview.rows.length} 行，待提交 {ready.length} 行，已成功 {succeeded} 行。</p>
        <div className="quickImportTableWrap"><table><caption>导入预览与逐行结果</caption><thead><tr><th>行号 / 人员</th><th>房间及日期</th><th>岗位 / 队伍</th><th>证件及联系</th><th>校验及结果</th></tr></thead><tbody>
          {preview.rows.map(row => { const outcome = outcomes[row.row_number]; return <tr key={row.row_number}>
            <td>第 {row.row_number} 行<strong>{row.full_name || "待确认"}</strong><span>{row.sex === "male" ? "男" : row.sex === "female" ? "女" : "性别待确认"} · {row.birth_date || "生日待确认"}</span></td>
            <td><strong>{row.room_label || "房间待确认"}</strong><span>入住：{row.arrival_date || "待确认"}</span><span>离开：{row.departure_date || "常住"}</span></td>
            <td>{row.identity_label}<span>{row.project_label}</span>{row.team_options.length ? <label>第 {row.row_number} 行队伍<select aria-label={`第 ${row.row_number} 行队伍`} disabled={busy || attempted || revalidating}
              value={choices[row.row_number] ?? row.selected_team_id ?? ""} onChange={event => { const next = { ...choices, [row.row_number]: event.target.value }; void recognize(next) }}>
              <option value="" disabled>请选择</option><option value="pending">待划分</option>{row.team_options.map(team => <option key={team.id} value={team.id}>{team.project_name} / {team.name}</option>)}</select></label> : <span>归属待完善</span>}</td>
            <td><span>{row.masked_identity}</span><span>{row.masked_phone}</span></td>
            <td>{row.errors.map((error, index) => <p className="quickImportError" key={index}>{error}</p>)}{row.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
              {outcome?.kind === "success" ? <><strong className="quickImportSuccess">{outcome.result.stay_status === "planned" ? "已建档并预约入住" : "已建档并入住"}</strong>{outcome.result.role_pending ? <p>角色需超管另行批准，不影响本次建档及入住。</p> : null}</>
                : outcome ? <p role="status" className="quickImportError">{outcome.message}</p> : row.ticket ? <span>可提交</span> : <strong>暂缓，未写入</strong>}</td>
          </tr> })}
        </tbody></table></div>
        <label className="quickImportConfirm"><input type="checkbox" checked={confirmed} disabled={busy || revalidating} onChange={event => setConfirmed(event.target.checked)} />我已核对预览中的人员、房间、日期与岗位，确认仅导入校验通过的行</label>
        <button type="button" disabled={busy || revalidating || !confirmed || !ready.length} onClick={() => void submit()}>{attempted ? "重试未完成行" : "确认建档并安排入住"}</button>
        <p>预览 15 分钟内有效；提交时再次核实房间及权限。网络中断请保留本页使用“重试未完成行”，已成功行不会再次提交。</p>
      </> : null}
    </> : null}
  </section>
}
