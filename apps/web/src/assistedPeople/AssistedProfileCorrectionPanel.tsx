import { Suspense, useState } from "react"
import { useOptionalAuth } from "../auth/AuthProvider"
import { useAssistedEditor } from "./editorApi"

import { createLazyModule } from "../app/loading/lazyModule"
import { ModuleBoundary } from "../app/loading/ModuleBoundary"
import { loadRouteStyles } from "../app/routeStyles"

const CorrectionDraft = createLazyModule("assisted-correction-draft", async () => {
  await loadRouteStyles("CorrectionDraft")
  return { default: (await import("./CorrectionDraft")).CorrectionDraft }
})

export function AssistedProfileCorrectionPanel({ personId }: { personId: string }) {
  const auth = useOptionalAuth()
  if (!auth?.user || auth.status !== "authenticated") return null
  return <AccountCorrection key={`${auth.user.account_id}:${personId}`} accountId={auth.user.account_id} personId={personId} />
}

function AccountCorrection({ accountId, personId }: { accountId: string; personId: string }) {
  const editor = useAssistedEditor(accountId, personId)
  const [open, setOpen] = useState(false)
  if (!editor.context?.allowed_actions.includes("correct_profile")) return null
  return <section className="detailCard" aria-label="协助档案资料更正">
    <h2>本人及监护资料更正</h2>
    {editor.blocked ? <p role="alert">管理权限暂未核实，当前草稿已保留。<button onClick={() => void editor.reload()}>重新核实管理权限</button></p> : null}
    {!open ? <button className="secondaryButton" disabled={editor.blocked} onClick={() => setOpen(true)}>更正本人及监护资料</button> :
      <ModuleBoundary moduleId="assisted-correction-draft" retry={CorrectionDraft.retry}><Suspense fallback={<p role="status">正在加载更正表单…</p>}><CorrectionDraft.Component accountId={accountId} personId={personId} context={editor.context} blocked={editor.blocked} reload={editor.reload} onClose={() => setOpen(false)} /></Suspense></ModuleBoundary>}
  </section>
}
