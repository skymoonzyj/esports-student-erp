import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { ProblemDetailsError } from "../api/client"
import { newEditorOperationKey } from "../assistedPeople/editorApi"
import { useOptionalAuth } from "../auth/AuthProvider"
import { PersonAccountRolesPanel } from "./PersonAccountRolesPanel"
import { personAssignmentsApi, personAssignmentsKey } from "./personAssignmentsApi"
import type { PersonAssignmentsUpdate, PersonAssignmentsView } from "./personAssignmentsApi"

type Props = { personId: string; onSaved?: (view: PersonAssignmentsView) => void; onLoaded?: (view: PersonAssignmentsView) => void }

export function PersonAssignmentsPanel(props: Props) {
  const auth = useOptionalAuth()
  if (auth?.status !== "authenticated" || !auth.user) return null
  const scope = JSON.stringify([Boolean(auth.user.is_super_admin), [...auth.user.permissions].sort(), [...(auth.user.scoped_permissions ?? [])].sort()])
  return <Assignments key={`${auth.user.account_id}:${props.personId}:${scope}`} {...props} actorId={auth.user.account_id} scope={scope} />
}

function denied(error: unknown) { return error instanceof ProblemDetailsError && [401, 403, 404].includes(error.problem.status) }
function summary(context: PersonAssignmentsView) {
  const kindLabels = { student: "学员", player: "队员", both: "学员兼队员" }
  return <>
    <p>当前业务身份：{context.business_kind ? kindLabels[context.business_kind] : "未设置学员／队员身份"}</p>
    <p>当前项目：{context.student_project_ids.map(id => [...(context.current_projects ?? []), ...context.eligible_projects].find(item => item.id === id)?.name ?? "不可用项目").join("、") || "待分配"}</p>
    <p>当前负责教练：{context.coach_person_ids.map(id => [...(context.current_coaches ?? []), ...context.eligible_coaches].find(item => item.id === id)?.full_name ?? "不可用教练").join("、") || "待分配"}</p>
  </>
}

function Assignments({ personId, actorId, scope, onSaved, onLoaded }: Props & { actorId: string; scope: string }) {
  const client = useQueryClient()
  const auth = useOptionalAuth()
  const [revoked, setRevoked] = useState(false)
  const key = personAssignmentsKey(actorId, personId, scope)
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => personAssignmentsApi.get(personId, signal), retry: false, gcTime: 0, staleTime: 0, refetchOnWindowFocus: false, enabled: !revoked })
  const lost = revoked || denied(query.error)
  useEffect(() => {
    if (query.data && !lost) onLoaded?.(query.data)
  }, [query.data, lost, onLoaded])
  useEffect(() => {
    if (!lost) return
    void client.cancelQueries({ queryKey: key })
    client.removeQueries({ queryKey: key })
  }, [lost, client, actorId, personId, scope]) // key is the same primitive boundary as these values.
  const clearAccess = () => {
    setRevoked(true)
    void client.cancelQueries({ queryKey: key })
    client.removeQueries({ queryKey: key })
    void auth?.refreshUser({ background: true }).catch(() => undefined)
  }
  if (lost) return <p role="alert">身份与归属权限已失效，请刷新当前权限。</p>
  if (!query.data) return <section className="detailCard" aria-label="身份与归属"><h2>身份与归属</h2>{query.isError ? <p role="alert">暂时无法读取身份与归属。<button type="button" onClick={() => void query.refetch()}>重新读取身份与归属</button></p> : <p>正在读取身份与归属…</p>}</section>
  const context = query.data
  return <>
    <section className="detailCard" aria-label="身份与归属"><h2>身份与归属</h2>
      {summary(context)}
      <p className="formHint">新增业务身份不会自动授予登录权限，也不会报名、收费、建订单或变更队伍。已有身份不可删除，以保留学籍和业务历史。</p>
      <p className="formHint">保存业务归属不会自动授予登录权限；激活账号后，请由有权人员在账号权限角色中授予对应的学员／队员角色。原审批激活身份保持不变。</p>
      {context.capabilities.can_edit ? <AssignmentDraft key={`${context.capabilities.can_edit}`} personId={personId} context={context} blocked={query.isError} onDenied={clearAccess}
        reload={async () => { const result = await query.refetch(); return result.isError ? null : result.data ?? null }}
        onSaved={view => { client.setQueryData(key, view); onSaved?.(view) }} /> : <p>当前归属仅供查看。</p>}
    </section>
    <PersonAccountRolesPanel personId={personId} accountId={context.account_id} accountStatus={context.account_status} canManageAccountRoles={context.capabilities.can_manage_account_roles} />
  </>
}

function AssignmentDraft({ personId, context, blocked, onDenied, onSaved, reload }: {
  personId: string; context: PersonAssignmentsView; blocked: boolean; onDenied: () => void;
  onSaved: (view: PersonAssignmentsView) => void; reload: () => Promise<PersonAssignmentsView | null>
}) {
  const [baseline, setBaseline] = useState(context)
  const [student, setStudent] = useState(context.business_kind === "student" || context.business_kind === "both")
  const [player, setPlayer] = useState(context.business_kind === "player" || context.business_kind === "both")
  const [projects, setProjects] = useState(context.student_project_ids)
  const [coaches, setCoaches] = useState(context.coach_person_ids)
  const [reason, setReason] = useState("")
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState("")
  const [saved, setSaved] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [review, setReview] = useState<PersonAssignmentsView | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const operation = useRef<{ payload: string; input: PersonAssignmentsUpdate } | null>(null)
  const flight = useRef<AbortController | null>(null)
  const active = useRef(false)
  useEffect(() => { active.current = true; return () => { active.current = false; flight.current?.abort() } }, [])
  const lockedStudent = [baseline.business_kind, context.business_kind].some(kind => kind === "student" || kind === "both")
  const lockedPlayer = [baseline.business_kind, context.business_kind].some(kind => kind === "player" || kind === "both")
  const isStudent = student || lockedStudent
  const isPlayer = player || lockedPlayer
  const eligibleCoaches = context.eligible_coaches.filter(coach => coach.project_ids.some(id => projects.includes(id)))
  const unavailableProjects = projects.filter(id => !context.eligible_projects.some(project => project.id === id))
  const unavailableCoaches = coaches.filter(id => !eligibleCoaches.some(coach => coach.id === id))
  const sameIds = (left: string[], right: string[]) => left.length === right.length && left.every(id => right.includes(id))
  // Existing coach-only relations predate project assignments. Preserve them unchanged;
  // editing either set requires current project eligibility rather than silently erasing history.
  const unchangedCoaches = sameIds(coaches, baseline.coach_person_ids) && sameIds(projects, baseline.student_project_ids)
  const validAssignments = isStudent
    ? !unavailableProjects.length && (!unavailableCoaches.length || unchangedCoaches)
    : projects.length === 0 && sameIds(coaches, baseline.coach_person_ids)
  const locked = pending || conflict || blocked
  const valid = reason.trim().length > 0 && reason.trim().length <= 200 && validAssignments
  function changed() { setSaved(false); setMessage("") }
  async function save(event: FormEvent) {
    event.preventDefault()
    if (locked || !valid || flight.current) return
    const desired = { business_kind: isStudent ? (isPlayer ? "both" : "student") : isPlayer ? "player" : null, student_project_ids: [...new Set(projects)].sort(), coach_person_ids: [...new Set(coaches)].sort(), expected_version: baseline.version, reason: reason.trim() } as Omit<PersonAssignmentsUpdate, "idempotency_key">
    const fingerprint = JSON.stringify(desired)
    if (operation.current?.payload !== fingerprint) operation.current = { payload: fingerprint, input: { ...desired, idempotency_key: newEditorOperationKey() } }
    const controller = new AbortController()
    flight.current = controller
    setPending(true); setMessage(""); setSaved(false)
    try {
      const result = await personAssignmentsApi.put(personId, operation.current.input, controller.signal)
      if (!active.current || controller.signal.aborted) return
      setBaseline(result); setStudent(result.business_kind === "student" || result.business_kind === "both"); setPlayer(result.business_kind === "player" || result.business_kind === "both")
      setProjects(result.student_project_ids); setCoaches(result.coach_person_ids); setReason(""); operation.current = null
      setSaved(true); onSaved(result)
    } catch (error) {
      if (!active.current || controller.signal.aborted) return
      if (denied(error)) { onDenied(); return }
      if (error instanceof ProblemDetailsError) {
        if (error.problem.status === 409) setConflict(true)
        setMessage([error.problem.detail, ...(error.problem.errors ?? []).map(item => item.message), "当前草稿已保留。"].join(" "))
      } else setMessage("暂时无法保存身份与归属，当前草稿已保留，可重试原操作。")
    } finally {
      if (active.current) { flight.current = null; setPending(false) }
    }
  }
  return <form className="staffProfileForm" onSubmit={event => void save(event)}>
    <fieldset disabled={locked}><legend>业务身份</legend>
      <label><input type="checkbox" checked={isStudent} disabled={lockedStudent} onChange={event => { setStudent(event.target.checked); changed() }} />学员身份</label>
      <label><input type="checkbox" checked={isPlayer} disabled={lockedPlayer} onChange={event => { setPlayer(event.target.checked); changed() }} />队员身份</label>
    </fieldset>
    {isStudent ? <>
      <fieldset disabled={locked}><legend>学员项目（可多选，留空表示待分配）</legend>
        {context.eligible_projects.map(project => <label key={project.id}><input type="checkbox" checked={projects.includes(project.id)} onChange={event => { setProjects(event.target.checked ? [...projects, project.id] : projects.filter(id => id !== project.id)); changed() }} />{project.name}</label>)}
        {unavailableProjects.map(id => <label key={id}><input type="checkbox" checked onChange={() => { setProjects(projects.filter(value => value !== id)); changed() }} />{context.current_projects?.find(project => project.id === id)?.name ?? "原项目"}（不可用，请移除后保存）</label>)}
      </fieldset>
      <fieldset disabled={locked}><legend>负责教练（所选项目合格负责人并集，可留空待分配）</legend>
        {eligibleCoaches.map(coach => <label key={coach.id}><input type="checkbox" checked={coaches.includes(coach.id)} onChange={event => { setCoaches(event.target.checked ? [...coaches, coach.id] : coaches.filter(id => id !== coach.id)); changed() }} />{coach.full_name}</label>)}
        {unavailableCoaches.map(id => <label key={id}><input type="checkbox" checked onChange={() => { setCoaches(coaches.filter(value => value !== id)); changed() }} />{[...(context.current_coaches ?? []), ...context.eligible_coaches].find(coach => coach.id === id)?.full_name ?? "原教练"}（不可用或不适用于所选项目，{unchangedCoaches ? "历史关系可原样保留；变更需重新核对" : "请移除后保存"}）</label>)}
      </fieldset>
    </> : null}
    {!isStudent && coaches.length > 0 ? <p className="formHint">历史负责教练原样保留；非学员身份只新增业务身份，不在此替换教练关系。</p> : null}
    {!validAssignments ? <p className="formHint">请核对不可用选项；项目和教练变更需要学员身份以及当前合格项目、教练，不会自动清除原选择。</p> : null}
    <p className="formHint">项目与教练可暂不分配；教练是负责人并集，不代表逐项目唯一教练。队伍由独立队伍面板维护。</p>
    <label>身份与归属变更原因<textarea maxLength={200} value={reason} disabled={pending || blocked} autoComplete="off" onChange={event => { setReason(event.target.value); changed() }} /></label>
    <p className="formHint">请填写 1–200 字安全原因，不要包含身份证、联系方式、密码或其他秘密。</p>
    {blocked ? <p role="alert">管理权限暂未核实，当前草稿已保留，请重新读取身份与归属。</p> : null}
    {message ? <p role="alert">{message}</p> : null}
    {saved ? <p role="status">身份与归属已保存</p> : null}
    {conflict ? <div><button type="button" disabled={reviewing} onClick={async () => { setReviewing(true); const latest = await reload(); if (!active.current) return; setReview(latest); setReviewing(false) }}>重新读取最新归属</button>
      {review ? <><p>最新归属已显示在上方；请核对当前身份、项目、教练和保留草稿，再采用最新版本。不会自动重新提交。</p><button type="button" onClick={() => { setBaseline(review); setReview(null); setConflict(false); operation.current = null; setMessage("") }}>已核对，采用最新版本</button></> : null}
    </div> : null}
    <button className="primaryButton" type="submit" disabled={locked || !valid}>{pending ? "保存中…" : "保存身份与归属"}</button>
  </form>
}
