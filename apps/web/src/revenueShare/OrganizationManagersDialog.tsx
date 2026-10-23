import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { ProblemDetailsError } from "../api/client"
import type { RevenueShareOrganization } from "./api"
import { formatDateOnly } from "../shared/date"
import { grantOrganizationManager, listOrganizationManagerCandidates, listOrganizationManagers, revokeOrganizationManager } from "./organizationManagersApi"
import type { OrganizationManagerCandidate } from "./organizationManagersApi"

type Props = { accountId: string; organization: RevenueShareOrganization; onClose: () => void }
const pageSize = 20

export function OrganizationManagersDialog(props: Props) {
  return <ManagersDialog key={`${props.accountId}:${props.organization.id}`} {...props} />
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function ManagersDialog({ accountId, organization, onClose }: Props) {
  const client = useQueryClient()
  const [q, setQ] = useState("")
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState("")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const write = useRef<AbortController | null>(null)
  const mounted = useRef(true)
  const dialog = useRef<HTMLElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const managerKey = ["revenue-share", accountId, "organization-managers", organization.id] as const
  const candidateRoot = ["revenue-share", accountId, "organization-manager-candidates"] as const
  const managers = useQuery({
    queryKey: managerKey,
    queryFn: ({ signal }) => listOrganizationManagers(organization.id, signal),
    retry: false,
    gcTime: 0,
  })
  const candidates = useQuery({
    queryKey: [...candidateRoot, q.trim(), page, pageSize],
    queryFn: ({ signal }) => listOrganizationManagerCandidates({ q: q.trim(), page, page_size: pageSize }, signal),
    enabled: organization.is_active,
    retry: false,
    gcTime: 0,
  })
  const candidateItems = candidates.isError || candidates.isFetching ? [] : candidates.data?.items ?? []
  const managerItems = managers.isError || managers.isFetching ? [] : managers.data ?? []
  const selected = candidateItems.find((item) => item.account_id === selectedId)

  useEffect(() => {
    mounted.current = true
    const previousFocus = document.activeElement as HTMLElement | null
    closeButton.current?.focus()
    return () => {
      mounted.current = false
      write.current?.abort()
      void client.cancelQueries({ queryKey: ["revenue-share", accountId, "organization-managers", organization.id], exact: true })
      void client.cancelQueries({ queryKey: ["revenue-share", accountId, "organization-manager-candidates"] })
      client.removeQueries({ queryKey: ["revenue-share", accountId, "organization-manager-candidates"] })
      previousFocus?.focus()
    }
  }, [accountId, organization.id, client])

  async function changeManager(person: OrganizationManagerCandidate, remove: boolean): Promise<void> {
    if (write.current || (!remove && !organization.is_active)) return
    const identity = `${person.full_name}${person.nickname ? `（${person.nickname}）` : ""} · 账号 ${person.account_id}`
    if (!window.confirm(remove
      ? `确认移除“${organization.name}”的负责人 ${identity}？移除后将立即停止该机构的邀请住宿读取授权。`
      : `确认将 ${identity} 添加为“${organization.name}”的负责人？`)) return
    const controller = new AbortController()
    write.current = controller
    setPending(true)
    setError(null)
    setNotice(null)
    try {
      if (remove) await revokeOrganizationManager(organization.id, person.account_id, controller.signal)
      else await grantOrganizationManager(organization.id, person.account_id, controller.signal)
      if (!mounted.current || controller.signal.aborted) return
      setSelectedId("")
      await client.invalidateQueries({ queryKey: managerKey, exact: true }, { throwOnError: true })
      if (mounted.current && !controller.signal.aborted) setNotice(remove ? "负责人已移除" : "负责人已添加")
    } catch (caught) {
      if (mounted.current && !controller.signal.aborted) setError(errorMessage(caught, "负责人变更未能确认，请刷新当前负责人后重试"))
    } finally {
      if (mounted.current && !controller.signal.aborted) { write.current = null; setPending(false) }
    }
  }

  return (
    <div className="revenueShareDialogBackdrop" onMouseDown={onClose}>
      <section ref={dialog} className="revenueShareDialog organizationManagersDialog" role="dialog" aria-modal="true" aria-labelledby="organization-managers-title" onMouseDown={(event) => event.stopPropagation()} onKeyDown={(event) => {
        if (event.key === "Escape") { event.preventDefault(); onClose() }
        if (event.key !== "Tab") return
        const focusable = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]')
        const first = focusable?.[0]; const last = focusable?.[(focusable?.length ?? 0) - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }}>
        <header><h2 id="organization-managers-title">{organization.name} · 负责人</h2><button ref={closeButton} className="secondaryButton" type="button" onClick={onClose}>关闭</button></header>
        <p>负责人仅可查看本机构已有邀请关系中的住宿摘要，不改变邀请归属、分成或财务权限。</p>
        {error ? <p className="revenueShareError" role="alert">{error}</p> : null}
        {notice ? <p className="revenueShareNotice" role="status">{notice}</p> : null}
        <h3>当前负责人</h3>
        {managers.isError ? <p role="alert">{errorMessage(managers.error, "负责人加载失败，请重试")}</p> : managers.isFetching ? <p role="status">正在加载负责人…</p> : managerItems.length === 0 ? <p>暂无负责人</p> : (
          <ul className="organizationManagersList">{managerItems.map((person) => <li key={person.account_id}><div><strong>{person.full_name}</strong>{person.nickname ? <span>（{person.nickname}）</span> : null}<small>账号 {person.account_id}</small><small>授权日期：<time>{formatDateOnly(person.granted_at)}</time></small></div><button className="dangerTextButton" type="button" aria-label={`移除负责人：${person.full_name} · ${person.account_id}`} disabled={pending} onClick={() => void changeManager(person, true)}>移除负责人</button></li>)}</ul>
        )}
        {managers.isError ? <button className="secondaryButton" type="button" disabled={managers.isFetching} onClick={() => void managers.refetch()}>重试加载负责人</button> : null}
        {!organization.is_active ? <p>停用机构不能新增负责人；仍可查看和移除已有负责人。</p> : (
          <div className="revenueShareForm">
            <h3>添加负责人</h3>
            <label><span>查找负责人账号</span><input value={q} maxLength={120} disabled={pending} onChange={(event) => { setQ(event.target.value); setPage(1); setSelectedId(""); setError(null); setNotice(null) }} placeholder="输入姓名、昵称或账号编号" /></label>
            {candidates.isError ? <p role="alert">{errorMessage(candidates.error, "候选账号加载失败，请重试")}</p> : candidates.isFetching ? <p role="status">正在查找账号…</p> : candidateItems.length === 0 ? <p>没有可选账号</p> : (
              <fieldset className="organizationManagerCandidates"><legend>选择现有账号</legend>{candidateItems.map((person) => <label key={person.account_id}><input type="radio" name="organization-manager-account" value={person.account_id} checked={selectedId === person.account_id} disabled={pending || managerItems.some((item) => item.account_id === person.account_id)} onChange={() => setSelectedId(person.account_id)} /><span>{person.full_name}{person.nickname ? `（${person.nickname}）` : ""}<small>账号 {person.account_id}</small></span></label>)}</fieldset>
            )}
            {candidates.isError ? <button className="secondaryButton" type="button" disabled={pending} onClick={() => { setSelectedId(""); void candidates.refetch() }}>重试查找账号</button> : null}
            <div className="organizationManagerPagination"><button className="secondaryButton" type="button" disabled={pending || page === 1 || candidates.isFetching} onClick={() => { setPage(page - 1); setSelectedId("") }}>上一页</button><span>第 {page} 页{!candidates.isError && !candidates.isFetching && candidates.data ? ` · 共 ${candidates.data.total} 个账号` : ""}</span><button className="secondaryButton" type="button" disabled={pending || candidates.isError || candidates.isFetching || !candidates.data || page * pageSize >= candidates.data.total} onClick={() => { setPage(page + 1); setSelectedId("") }}>下一页</button></div>
            <button className="primaryButton" type="button" disabled={pending || !selected || managers.isFetching || managers.isError} onClick={() => { if (selected) void changeManager(selected, false) }}>添加负责人</button>
          </div>
        )}
      </section>
    </div>
  )
}
