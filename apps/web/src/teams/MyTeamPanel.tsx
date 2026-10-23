import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { formatDateOnly } from "../shared/date"
import { getTeamContext, myTeam } from "./api"
import { teamKeys } from "./keys"
import { accountStatusLabels, membershipEndLabels } from "./presentation"
import "./teams.css"

export function MyTeamPanel({ accountId }: { accountId: string }) {
  return <AccountMyTeamPanel key={accountId} accountId={accountId} />
}

function AccountMyTeamPanel({ accountId }: { accountId: string }) {
  const client = useQueryClient()
  const [page, setPage] = useState(1)
  const [resourceDenied, setResourceDenied] = useState(false)
  const keys = useMemo(() => teamKeys(accountId), [accountId])
  const context = useQuery({
    queryKey: keys.context(), queryFn: getTeamContext, refetchOnWindowFocus: "always",
  })
  const response = useQuery({
    queryKey: keys.self(page), queryFn: () => myTeam({ page, pageSize: 20 }),
    enabled: context.data?.can_read_self === true && !context.isError && !resourceDenied,
  })
  useEffect(() => {
    const error = response.error
    if (!(error instanceof ProblemDetailsError) || ![403, 404].includes(error.problem.status)) return
    setResourceDenied(true)
    const queryKey = keys.self(page)
    void client.cancelQueries({ queryKey, exact: true })
    client.removeQueries({ queryKey, exact: true })
    void client.invalidateQueries({ queryKey: keys.context() })
  }, [client, keys, page, response.error])
  useEffect(() => {
    if (context.data?.can_read_self !== false || context.isFetching) return
    for (const query of client.getQueryCache().findAll({ queryKey: [...keys.root, "self"] })) {
      void client.cancelQueries({ queryKey: query.queryKey, exact: true })
      client.removeQueries({ queryKey: query.queryKey, exact: true })
    }
  }, [client, context.data?.can_read_self, context.isFetching, keys])
  const canShowAffiliation = context.data?.can_read_self === true && !context.isError && !response.isError && !resourceDenied

  return (
    <section className="teamSelfPanel" aria-label="我的队伍">
      <h2>我的队伍</h2>
      {context.isLoading ? <p>正在读取归属…</p> : null}
      {context.isError ? <p role="alert">暂时无法确认查看权限。</p> : null}
      {context.data && !context.data.can_read_self ? <p>无查看权限</p> : null}
      {canShowAffiliation && response.isLoading ? <p>正在读取我的队伍…</p> : null}
      {context.data?.can_read_self && (response.isError || resourceDenied) ? <p role="alert">暂时无法读取我的队伍。</p> : null}
      {canShowAffiliation && response.data && !response.data.current ? <p>未分配队伍</p> : null}
      {canShowAffiliation && response.data?.current ? (
        <div className="teamSelfCurrent">
          <strong>{response.data.current.project_name} · {response.data.current.team_name}</strong>
          <span>加入日期：{formatDateOnly(response.data.current.joined_on)}</span>
        </div>
      ) : null}
      {canShowAffiliation && response.data ? (
        <div className="teamHistory">
          <h3>历史归属</h3>
          {response.data.history.items.length === 0 ? <p>暂无历史记录</p> : null}
          <ul>
            {response.data.history.items.map((record) => (
              <li key={record.id}>
                <strong>{record.project_name} · {record.team_name}</strong>
                <span>{formatDateOnly(record.joined_on)} 至 {formatDateOnly(record.left_on)}</span>
                <span>{accountStatusLabels[record.account_status]}</span>
                <span>{record.status === "current" ? "在队" : "已离队"}</span>
                {record.end_kind ? <span>{membershipEndLabels[record.end_kind]}</span> : null}
                {record.transfer_reason ? <span>调队原因：{record.transfer_reason}</span> : null}
              </li>
            ))}
          </ul>
          <nav className="teamPager" aria-label="历史分页">
            <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
            <span>第 {page} 页，共 {response.data.history.total} 条</span>
            <button type="button" disabled={page * response.data.history.page_size >= response.data.history.total} onClick={() => setPage(page + 1)}>下一页</button>
          </nav>
        </div>
      ) : null}
    </section>
  )
}
