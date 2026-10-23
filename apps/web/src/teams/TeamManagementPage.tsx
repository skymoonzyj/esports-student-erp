import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useState } from "react"

import { useOptionalAuth } from "../auth/AuthProvider"
import { formatDateOnly } from "../shared/date"
import { getTeamContext, listMemberships, listTeams } from "./api"
import type { TeamAction, TeamMemberView, TeamSummary } from "./api"
import { teamKeys } from "./keys"
import { clearDeniedTeamResource, isDeniedResource } from "./resourceDenial"
import { accountStatusLabels, membershipEndLabels } from "./presentation"
import { TeamActionDialogs } from "./TeamActionDialogs"
import { TeamManagerPanel } from "./TeamManagerPanel"
import "./teams.css"

async function allAuthorizedTeams(): Promise<TeamSummary[]> {
  const items: TeamSummary[] = []
  let page = 1
  while (true) {
    const response = await listTeams({ page, pageSize: 100 })
    items.push(...response.items)
    if (page * response.page_size >= response.total || response.items.length === 0) return items
    page += 1
  }
}

export function TeamManagementPage({ accountId }: { accountId: string }) {
  return <AccountTeamManagementPage key={accountId} accountId={accountId} />
}

function AccountTeamManagementPage({ accountId }: { accountId: string }) {
  const auth = useOptionalAuth()
  const client = useQueryClient()
  const keys = useMemo(() => teamKeys(accountId), [accountId])
  const [projectId, setProjectId] = useState("")
  const [teamName, setTeamName] = useState("")
  const [teamStatus, setTeamStatus] = useState<"all" | "active" | "inactive">("all")
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null)
  const [memberStatus, setMemberStatus] = useState<"current" | "left" | "all">("current")
  const [memberPage, setMemberPage] = useState(1)
  const [dialog, setDialog] = useState<{ action: "join" | "leave" | "transfer"; member?: TeamMemberView } | null>(null)
  const [deniedTeamId, setDeniedTeamId] = useState<string | null>(null)
  const [listDenied, setListDenied] = useState(false)
  const onDenied = useCallback((teamId: string) => {
    setDeniedTeamId(teamId)
    setDialog(null)
    setSelectedTeamId(null)
    clearDeniedTeamResource(client, accountId, teamId)
  }, [accountId, client])

  const context = useQuery({
    queryKey: keys.context(), queryFn: getTeamContext, refetchOnWindowFocus: "always",
  })
  const teamList = useQuery({
    queryKey: [...keys.root, "all-authorized"], queryFn: allAuthorizedTeams,
    enabled: context.data?.can_open_management === true && !context.isError && !listDenied,
  })
  const safeTeamList = !listDenied && !teamList.isError ? teamList.data : undefined
  const selectedTeam = safeTeamList?.find((item) => item.id === selectedTeamId)
  const memberOptions = { status: memberStatus, page: memberPage, pageSize: 20 } as const
  const members = useQuery({
    queryKey: keys.members(selectedTeamId ?? "", memberOptions),
    queryFn: () => listMemberships(selectedTeamId!, memberOptions),
    enabled: Boolean(selectedTeamId && selectedTeamId !== deniedTeamId && selectedTeam?.allowed_actions.includes("read") && context.data?.can_open_management && !context.isError),
  })
  useEffect(() => {
    if (!isDeniedResource(teamList.error)) return
    setListDenied(true)
    setDialog(null)
    setSelectedTeamId(null)
    const queryKey = [...keys.root, "all-authorized"]
    void client.cancelQueries({ queryKey, exact: true })
    client.removeQueries({ queryKey, exact: true })
    void client.invalidateQueries({ queryKey: keys.context() })
  }, [client, keys, teamList.error])
  useEffect(() => {
    if (!selectedTeamId) return
    const cache = client.getQueryCache()
    return cache.subscribe((event) => {
      if (event.type !== "updated" || !isDeniedResource(event.query.state.error)) return
      const queryKey = event.query.queryKey
      if (queryKey[0] !== "teams" || queryKey[1] !== accountId || queryKey[3] !== selectedTeamId) return
      if (!["members", "assignments", "candidates", "manager-candidates"].includes(String(queryKey[2]))) return
      onDenied(selectedTeamId)
    })
  }, [accountId, client, onDenied, selectedTeamId])
  useEffect(() => {
    if (context.data?.can_open_management !== false || context.isFetching) return
    setDialog(null)
    setSelectedTeamId(null)
    for (const query of client.getQueryCache().findAll({ queryKey: keys.root })) {
      if (query.queryKey[2] === "context" || query.queryKey[2] === "self") continue
      void client.cancelQueries({ queryKey: query.queryKey, exact: true })
      client.removeQueries({ queryKey: query.queryKey, exact: true })
    }
  }, [client, context.data?.can_open_management, context.isFetching, keys])
  const projects = useMemo(() => {
    const unique = new Map<string, string>()
    for (const team of safeTeamList ?? []) unique.set(team.project_id, team.project_name)
    return [...unique].sort((a, b) => a[1].localeCompare(b[1], "zh-CN"))
  }, [safeTeamList])
  const filteredTeams = useMemo(() => (safeTeamList ?? []).filter((team) =>
    team.id !== deniedTeamId &&
    (!projectId || team.project_id === projectId) &&
    (!teamName.trim() || team.name.includes(teamName.trim())) &&
    (teamStatus === "all" || team.is_active === (teamStatus === "active")),
  ), [deniedTeamId, projectId, safeTeamList, teamName, teamStatus])

  async function onChanged(): Promise<void> {
    await Promise.all([
      client.invalidateQueries({ queryKey: keys.root }),
      client.invalidateQueries({ queryKey: ["people"] }),
      client.invalidateQueries({ queryKey: ["person"] }),
      client.invalidateQueries({ queryKey: ["people-counts"] }),
    ])
  }

  function selectTeam(teamId: string): void {
    setSelectedTeamId(teamId)
    setMemberPage(1)
    setMemberStatus("current")
    setDialog(null)
  }

  function permits(action: TeamAction): boolean {
    return selectedTeam?.allowed_actions.includes(action) === true
  }

  return (
    <main className="teamManagementPage">
      <header className="teamPageHeader"><div><p>人员中心</p><h1>队伍管理</h1></div>
        {context.data?.can_manage_catalog && auth?.user?.is_super_admin ? <a href="/teaching/catalog">编辑目录</a> : null}
      </header>
      {context.isLoading ? <p>正在确认队伍权限…</p> : null}
      {context.isError ? <p role="alert">暂时无法确认队伍权限。</p> : null}
      {context.data && !context.data.can_open_management ? <p>无查看权限</p> : null}
      {context.data?.can_open_management ? <>
        <div className="teamFilters" role="group" aria-label="队伍筛选">
          <label>分部<select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">全部分部</option>{projects.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
          <label>队伍<input value={teamName} onChange={(event) => setTeamName(event.target.value)} placeholder="队伍名称" /></label>
          <label>状态<select value={teamStatus} onChange={(event) => setTeamStatus(event.target.value as typeof teamStatus)}><option value="all">全部状态</option><option value="active">启用</option><option value="inactive">停用</option></select></label>
        </div>
        {teamList.isLoading ? <p>正在读取授权队伍…</p> : null}
        {teamList.isError || listDenied ? <p role="alert">暂时无法读取授权队伍。</p> : null}
        {safeTeamList ? <p>授权队伍 {filteredTeams.length} 支</p> : null}
        {safeTeamList && filteredTeams.length === 0 ? <p>没有符合条件的授权队伍。</p> : null}
        <div className="teamCardGrid">
          {filteredTeams.map((team) => <article className="teamCard" key={team.id}>
            <h2>{team.name}</h2>
            <span className="teamCardMeta">{team.project_name} · {team.is_active ? "启用" : "停用"} · 在队 {team.member_count} 人</span>
            {team.allowed_actions.includes("read") ? <button type="button" aria-label={`查看 ${team.name}`} onClick={() => selectTeam(team.id)}>查看 {team.name}</button> : null}
          </article>)}
        </div>
        {selectedTeam ? <section className="teamCard" aria-label={`${selectedTeam.name} 名单`}>
          <header className="teamPageHeader"><div><h2>{selectedTeam.project_name} · {selectedTeam.name}</h2><p>授权在队人数 {selectedTeam.member_count}</p></div><button type="button" onClick={() => setSelectedTeamId(null)}>关闭名单</button></header>
          <div className="teamActionRow">
            <label>名单状态<select value={memberStatus} onChange={(event) => { setMemberStatus(event.target.value as typeof memberStatus); setMemberPage(1) }}><option value="current">在队</option><option value="left">历史</option><option value="all">全部</option></select></label>
            {permits("manage") && selectedTeam.is_active ? <button type="button" className="teamPrimaryButton" onClick={() => setDialog({ action: "join" })}>添加队员</button> : null}
          </div>
          {members.isLoading ? <p>正在读取名单…</p> : null}
          {members.isError ? <p role="alert">暂时无法读取名单或查看权限已变更。</p> : null}
          {members.data?.items.length === 0 ? <p>此状态下暂无队员。</p> : null}
          <ul className="teamMemberList">
            {members.data?.items.map((member) => <li key={member.id}>
              <div><strong>{member.full_name}</strong><div className="teamMemberMeta">{member.nickname ?? "无昵称"} · {member.age}岁 · {formatDateOnly(member.joined_on)}{member.left_on ? ` 至 ${formatDateOnly(member.left_on)}` : ""}</div></div>
              <div className="teamMemberMeta"><span>队员</span> · {member.is_minor ? <><span>未成年</span> · </> : null}<span>{accountStatusLabels[member.account_status]}</span> · <span>{member.status === "current" ? "在队" : "已离队"}</span>{member.end_kind ? <> · <span>{membershipEndLabels[member.end_kind]}</span></> : null}</div>
              {member.status === "current" ? <div className="teamActionRow">
                {permits("manage") ? <button type="button" aria-label={`移出 ${member.full_name}`} onClick={() => setDialog({ action: "leave", member })}>移出</button> : null}
                {permits("transfer") ? <button type="button" aria-label={`调队 ${member.full_name}`} onClick={() => setDialog({ action: "transfer", member })}>调队</button> : null}
              </div> : null}
            </li>)}
          </ul>
          {members.data ? <nav className="teamPager" aria-label="名单分页"><button type="button" disabled={memberPage <= 1} onClick={() => setMemberPage(memberPage - 1)}>上一页</button><span>第 {memberPage} 页，共 {members.data.total} 条</span><button type="button" disabled={memberPage * members.data.page_size >= members.data.total} onClick={() => setMemberPage(memberPage + 1)}>下一页</button></nav> : null}
          {permits("assign_managers") ? <TeamManagerPanel key={`${accountId}:${selectedTeam.id}`} accountId={accountId} teamId={selectedTeam.id} teamIsActive={selectedTeam.is_active} allowedActions={selectedTeam.allowed_actions} onChanged={onChanged} onDenied={onDenied} /> : null}
        </section> : null}
      </> : null}
      {selectedTeam && dialog ? <TeamActionDialogs key={`${selectedTeam.id}-${dialog.action}-${dialog.member?.id ?? "new"}`} accountId={accountId} team={selectedTeam} member={dialog.member} action={dialog.action} allowedActions={selectedTeam.allowed_actions} onClose={() => setDialog(null)} onChanged={onChanged} onDenied={onDenied} /> : null}
    </main>
  )
}
