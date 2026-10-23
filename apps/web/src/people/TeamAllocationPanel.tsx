import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { Link } from "react-router-dom"
import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { updateTeamAllocation } from "./api"
import type { TeamAllocationProjection, TeamAllocationUpdate } from "./api"
import { teamAllocationText } from "./presentation"

export function TeamAllocationPanel({ personId, projection }: {
  personId: string; projection: TeamAllocationProjection | undefined
}) {
  const client = useQueryClient()
  const auth = useOptionalAuth()
  const [target, setTarget] = useState<TeamAllocationUpdate["status"] | null>(null)
  const [reason, setReason] = useState("")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [revoked, setRevoked] = useState(false)
  const [conflict, setConflict] = useState(false)
  const operation = useRef<TeamAllocationUpdate | null>(null)
  const draftVersion = useRef<number | null>(null)
  const allowed = !revoked && projection?.visibility === "allowed" && projection.can_update === true && typeof projection.version === "number" && projection.version >= 1
  const access = `${auth?.user?.account_id ?? "legacy"}:${personId}:${allowed}:${projection?.visibility}`
  const currentAccess = useRef(access)
  const accessGeneration = useRef(0)
  if (currentAccess.current !== access) {
    currentAccess.current = access
    accessGeneration.current += 1
  }
  const previousAccess = useRef(access)
  useEffect(() => {
    if (previousAccess.current !== access) {
      previousAccess.current = access
      setTarget(null)
      setReason("")
      setPending(false)
      setError(null)
      setSaved(false)
      setConflict(false)
      operation.current = null
      draftVersion.current = null
    }
    return () => { accessGeneration.current += 1 }
  }, [access])

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!allowed || !target || pending || conflict || !reason.trim() || reason.trim().length > 200) return
    const version = draftVersion.current
    if (typeof version !== "number") return
    const input = operation.current ?? { status: target, expected_version: version, idempotency_key: crypto.randomUUID(), reason: reason.trim() }
    operation.current = input
    const requestGeneration = accessGeneration.current
    setPending(true)
    setError(null)
    try {
      await updateTeamAllocation(personId, input)
      if (accessGeneration.current !== requestGeneration) return
      setTarget(null)
      setReason("")
      operation.current = null
      setSaved(true)
      await Promise.all([["people"], ["people-counts"], ["person", personId]].map(queryKey => client.invalidateQueries({ queryKey })))
    } catch (failure) {
      if (accessGeneration.current !== requestGeneration) return
      if (failure instanceof ProblemDetailsError && [401, 403, 404].includes(failure.problem.status)) {
        setRevoked(true)
        setTarget(null)
        setReason("")
        operation.current = null
        const clear = () => {
          for (const queryKey of [["people"], ["people-counts"], ["person", personId]]) {
            void client.cancelQueries({ queryKey })
            client.removeQueries({ queryKey })
          }
        }
        clear()
        void auth?.refreshUser({ background: true }).catch(() => undefined).finally(clear)
      } else if (failure instanceof ProblemDetailsError && failure.problem.status === 409) {
        setConflict(true)
        setError("归属状态或实际队伍关系已变化，请取消并刷新后重新操作。")
        void client.invalidateQueries({ queryKey: ["person", personId] })
      } else {
        setError(failure instanceof ProblemDetailsError ? failure.problem.detail : "归属标记暂时无法保存，可重试原操作。")
      }
    } finally {
      if (accessGeneration.current === requestGeneration) setPending(false)
    }
  }

  if (revoked) return <p role="alert">队伍归属权限已失效，请刷新当前权限。</p>
  return <section className="detailCard teamAllocationCard" aria-label="队伍划分状态">
    <h2>队伍划分状态</h2>
    <p>{teamAllocationText(projection)}</p>
    <p className="formHint">队伍划分、账号激活和资料完整度分别维护；归属标记不变更队伍成员或账号角色。</p>
    {saved ? <p role="status">归属标记已保存</p> : null}
    {allowed ? <>
      <p><Link to="/people/teams">队员加入队伍</Link> · <Link to="/people/teams">队伍管理任职</Link>（沿用各自资格核对）</p>
      {target ? <form className="staffProfileForm" onSubmit={save}>
        <p>将标记为：{target === "pending" ? "待划分队伍" : "无需分队"}</p>
        <label>队伍归属变更原因<textarea value={reason} maxLength={200} rows={3} disabled={pending || Boolean(operation.current)} onChange={event => setReason(event.target.value)} required /></label>
        {error ? <p role="alert">{error}</p> : null}
        <div className="personDetailActions"><button className="primaryButton" type="submit" disabled={pending || conflict || !reason.trim()}>{pending ? "保存中…" : "保存归属标记"}</button><button className="secondaryButton" type="button" disabled={pending} onClick={() => { setTarget(null); setReason(""); operation.current = null; setError(null); setConflict(false) }}>取消</button></div>
      </form> : <div className="personDetailActions">
        {projection.status !== "pending" ? <button className="secondaryButton" type="button" onClick={() => { draftVersion.current = projection.version; setTarget("pending"); setSaved(false) }}>标记待划分队伍</button> : null}
        {projection.status !== "not_required" ? <button className="secondaryButton" type="button" onClick={() => { draftVersion.current = projection.version; setTarget("not_required"); setSaved(false) }}>标记无需分队</button> : null}
      </div>}
    </> : null}
  </section>
}
