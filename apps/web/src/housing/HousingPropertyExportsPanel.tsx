import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { ProblemDetailsError } from "../api/client"
import { useHousingPropertyAccess } from "./propertyAccess"
import { exportHousingProperty, saveHousingPropertyExport } from "./propertyApi"
import type { HousingPropertyExportFile, HousingPropertyExportInput } from "./propertyApi"
import "./housing-property.css"

export function HousingPropertyExportsPanel() {
  const access = useHousingPropertyAccess()
  if (access.error) return <p className="housingProperty__error" role="alert">无法核验物业导出权限，请刷新重试。</p>
  if (!access.catalog || access.capabilities?.show_basic_export !== true || access.capabilities.can_export !== true) return null
  return <Exports key={access.signature} access={access} />
}

function Exports({ access }: { access: ReturnType<typeof useHousingPropertyAccess> }) {
  const client = useQueryClient()
  const live = useRef(true)
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  const [kind, setKind] = useState<HousingPropertyExportInput["kind"]>("occupants")
  const [format, setFormat] = useState<HousingPropertyExportInput["format"]>("xlsx")
  const [buildings, setBuildings] = useState<string[]>([])
  const [purpose, setPurpose] = useState("")
  const [busy, setBusy] = useState(false)
  const [blocked, setBlocked] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [pendingFile, setPendingFile] = useState<HousingPropertyExportFile | null>(null)
  useEffect(() => {
    if (!pendingFile || !access.ready || !access.isAuthorizedNow("can_export") || !access.isAuthorizedNow("show_basic_export") || !live.current) return
    saveHousingPropertyExport(pendingFile)
    setPendingFile(null); setNotice("导出文件已生成。")
  }, [access, pendingFile])
  async function download() {
    if (busy || blocked || pendingFile || !access.ready || !access.isAuthorizedNow("can_export") || !access.isAuthorizedNow("show_basic_export") || purpose.trim().length < 2) return
    setBusy(true); setError(""); setNotice("")
    try {
      const file = await exportHousingProperty({ kind, format, purpose: purpose.trim(), building_ids: buildings })
      if (!live.current) return
      setPendingFile(file)
    } catch (caught) {
      if (!live.current) return
      if (caught instanceof ProblemDetailsError && [401, 403].includes(caught.problem.status)) { setBlocked(true); setPurpose(""); setBuildings([]); void client.invalidateQueries({ queryKey: access.scope }) }
      setError(caught instanceof Error ? caught.message : "导出失败，请重试。")
    } finally { if (live.current) setBusy(false) }
  }
  return <><section className="housingProperty" aria-label="物业基础导出" hidden={!access.ready} inert={!access.ready}><header><h2>物业基础导出</h2><p>仅导出授权范围的基础清单，不含证件号码、手机号、邮箱或监护人资料。申请未通过的建议不会进入导出。</p></header>
    {error && <p role="alert" className="housingProperty__error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!blocked && <form onSubmit={(event) => { event.preventDefault(); void download() }}><fieldset disabled={busy || !!pendingFile || !access.ready}><legend>导出条件</legend>
      <div className="housingProperty__grid"><label>清单类型<select value={kind} onChange={(e) => setKind(e.target.value as HousingPropertyExportInput["kind"])}><option value="occupants">当前住宿人员列表</option><option value="problems">问题房清单（含备注）</option></select></label><label>文件格式<select value={format} onChange={(e) => setFormat(e.target.value as HousingPropertyExportInput["format"])}><option value="xlsx">XLSX</option><option value="csv">CSV</option></select></label></div>
      <fieldset><legend>楼栋（不选为全部授权楼栋，不受当前页限制）</legend><div className="housingProperty__actions">{access.catalog?.buildings.map((building) => <label className="housingProperty__check" key={building.id}><input type="checkbox" checked={buildings.includes(building.id)} onChange={(e) => setBuildings(e.target.checked ? [...buildings, building.id] : buildings.filter((id) => id !== building.id))} />{building.name}</label>)}<button type="button" disabled={!buildings.length} onClick={() => setBuildings([])}>清除楼栋选择</button></div></fieldset>
      <label>导出用途<textarea required minLength={2} maxLength={200} value={purpose} onChange={(e) => setPurpose(e.target.value)} /></label>
      <button type="submit" disabled={purpose.trim().length < 2 || busy}>导出文件</button>
    </fieldset></form>}
  </section>{!access.ready && <p role="status">正在重新核验物业导出权限…</p>}</>
}
