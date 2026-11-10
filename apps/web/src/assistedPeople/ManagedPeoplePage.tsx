import { useInfiniteQuery } from "@tanstack/react-query"
import { useState } from "react"
import { useAuth } from "../auth/AuthProvider"
import { InvitedHousingSummary, housingStatusLabels } from "../invitations/InvitedHousingSummary"
import { assistedPeopleApi } from "./api"
import type { AssistedContext, ManagedPeopleFilters } from "./api"
import { assistedPeopleKeys } from "./keys"
import { assistedQueryOptions, useAssistedPeopleContext } from "./useAssistedPeopleContext"
import "./assisted-people.css"

export function ManagedPeoplePage() {
  const { user, status } = useAuth()
  if (!user || status !== "authenticated") return <p>请先登录。</p>
  return <ManagedAccount key={user.account_id} accountId={user.account_id} />
}

function ManagedAccount({ accountId }: { accountId: string }) {
  const query = useAssistedPeopleContext(accountId)
  if (query.isPending) return <p role="status">正在核实所属人员权限…</p>
  if (!query.context?.can_read_managed) return <section><p role="alert">无所属人员查看权限。</p><button onClick={() => void query.refetch()}>重新核实权限</button></section>
  return <ManagedWorkspace accountId={accountId} context={query.context} />
}

function ManagedWorkspace({ accountId, context }: { accountId: string; context: AssistedContext }) {
  const [{ filters, generation }, setFilterState] = useState<{ filters: ManagedPeopleFilters; generation: number }>({ filters: {}, generation: 0 })
  // The entire infinite-query identity changes with filters: no retained page or
  // cursor is ever reused, including when revisiting a previously used filter.
  function filter(next: Partial<ManagedPeopleFilters>) {
    setFilterState((current) => ({ filters: { ...current.filters, ...next }, generation: current.generation + 1 }))
  }
  return <main className="peoplePage managedPeoplePage assistedPeoplePage"><h1>我的学员／队员</h1>
    <p>仅显示当前授权负责的学员、队员及住宿摘要。</p>
    <div className="managedPeopleFilters">
      <label>姓名或昵称<input autoComplete="off" maxLength={80} value={filters.query ?? ""} onChange={(event) => filter({ query: event.target.value || undefined })} /></label>
      <label>身份筛选<select value={filters.kind ?? ""} onChange={(event) => filter({ kind: event.target.value as ManagedPeopleFilters["kind"] || undefined })}>
        <option value="">全部</option><option value="student">学员</option><option value="player">队员</option><option value="both">学员兼队员</option>
      </select></label>
      <label>住宿状态筛选<select value={filters.housing_status ?? ""} onChange={(event) => filter({ housing_status: event.target.value as ManagedPeopleFilters["housing_status"] || undefined })}>
        <option value="">全部</option>{Object.entries(housingStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      {context.eligible_teams.length ? <label>所属队伍筛选<select value={filters.team_id ?? ""} onChange={(event) => filter({ team_id: event.target.value || undefined })}>
        <option value="">全部授权队伍</option>{context.eligible_teams.filter((team) => context.allowed_team_ids.includes(team.id)).map((team) => <option key={team.id} value={team.id}>{team.label}</option>)}
      </select></label> : null}
    </div>
    <ManagedResults key={generation} accountId={accountId} filters={filters} generation={generation} />
  </main>
}

function ManagedResults({ accountId, filters, generation }: { accountId: string; filters: ManagedPeopleFilters; generation: number }) {
  const query = useInfiniteQuery({
    queryKey: assistedPeopleKeys.managed(accountId, { ...filters, generation }), initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) => assistedPeopleApi.managedPeople({ ...filters, cursor: pageParam }, signal),
    getNextPageParam: (last) => last.next_cursor ?? undefined, ...assistedQueryOptions,
  })
  const pages = query.isError ? undefined : query.data?.pages
  const items = pages?.flatMap((page) => page.items) ?? []
  return <>
    {query.isPending ? <p role="status">正在加载所属人员…</p> : null}
    {query.isError ? <p role="alert">所属人员暂不可用。<button onClick={() => void query.refetch()}>重试列表</button></p> : null}
    {pages ? <><p>共 {pages[pages.length - 1].total} 人</p>
      {!items.length ? <p>没有符合条件的学员／队员。</p> : null}
      <div className="managedPeopleCards">{items.map((person) => <article className="managedPersonCard" key={person.person_id}>
        <h2>{person.full_name}</h2>{person.nickname ? <p>{person.nickname}</p> : null}
        <p>{person.kinds.map((kind) => kind === "student" ? "学员" : "队员").join("、")}</p>
        {person.authorized_affiliations.map((affiliation) => <p key={`${affiliation.kind}:${affiliation.id}`}>{affiliation.kind === "team" ? "所属队伍" : "负责教练"}：{affiliation.label}</p>)}
        <InvitedHousingSummary summary={person.housing} />
      </article>)}</div>
      {query.hasNextPage ? <button disabled={query.isFetching} onClick={() => void query.fetchNextPage()}>加载更多</button> : null}
    </> : null}
  </>
}
