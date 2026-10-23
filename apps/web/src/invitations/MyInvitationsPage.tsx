import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { getInvitedHousing, type InvitedHousingFilters } from "./api"
import { InvitedHousingSummary, housingStatusLabels } from "./InvitedHousingSummary"
import { invitationKeys } from "./keys"
import { useInvitationAccess } from "./useInvitationAccess"

import "./invitations.css"

type InvitationListProps = {
  accountId: string
  filters: InvitedHousingFilters
  epoch: number
  onPage: (page: number) => void
  onError: (epoch: number) => void
}

function InvitationList({
  accountId, filters, epoch, onPage, onError,
}: InvitationListProps) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: invitationKeys.housing(accountId, filters, epoch),
    queryFn: ({ signal }) => getInvitedHousing(filters, signal),
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  })

  useEffect(() => {
    if (!query.error) return
    onError(epoch)
    const permissionError = query.error instanceof ProblemDetailsError
      && [403, 404].includes(query.error.problem.status)
    const queryKey = permissionError
      ? invitationKeys.root(accountId)
      : invitationKeys.housingRoot(accountId)
    const remove = () => client.removeQueries({ queryKey })
    remove()
    void client.cancelQueries({ queryKey }).finally(remove)
  }, [accountId, client, epoch, onError, query.error])

  if (query.isFetching || query.isPending) {
    return <p role="status">正在读取受邀人员…</p>
  }
  if (query.isError) {
    return <p role="alert">暂时无法读取邀请信息，请重新验证授权。</p>
  }
  if (!query.data.items.length) {
    return (
      <>
        <p>{query.data.total ? "当前页暂无受邀人员" : "暂无受邀人员"}</p>
        {filters.page > 1 ? (
          <button onClick={() => onPage(1)}>返回第一页</button>
        ) : null}
      </>
    )
  }

  return (
    <>
      <p>共 {query.data.total} 人</p>
      <table className="invitedPeople" aria-label="受邀人员住宿">
        <thead>
          <tr>
            <th>受邀人员</th>
            <th>邀请来源</th>
            <th>住宿信息</th>
          </tr>
        </thead>
        <tbody>
          {query.data.items.map((item) => (
            <tr key={item.person_id}>
              <td data-label="受邀人员">
                <strong>{item.full_name}</strong>
                {item.nickname ? <p>{item.nickname}</p> : null}
              </td>
              <td data-label="邀请来源">{item.source.name}</td>
              <td data-label="住宿信息">
                <InvitedHousingSummary summary={item.housing} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <nav aria-label="邀请分页">
        <button disabled={filters.page === 1} onClick={() => onPage(filters.page - 1)}>
          上一页
        </button>
        <span>第 {filters.page} / {Math.max(1, Math.ceil(query.data.total / filters.pageSize))} 页</span>
        <button
          disabled={filters.page * filters.pageSize >= query.data.total}
          onClick={() => onPage(filters.page + 1)}
        >
          下一页
        </button>
      </nav>
    </>
  )
}

function InvitationContent({ accountId }: { accountId: string }) {
  const access = useInvitationAccess(accountId)
  const [selection, setSelection] = useState("all")
  const [status, setStatus] = useState<InvitedHousingFilters["status"]>()
  const [page, setPage] = useState(1)
  const [failedEpoch, setFailedEpoch] = useState<number | null>(null)
  const validSelection = selection === "all" || access.sources.some((source) => (
    source.source_kind === "person" ? "person" : source.organization_id
  ) === selection)
  const selected = validSelection ? selection : "all"

  useEffect(() => {
    if (access.ready && !validSelection) {
      setSelection("all")
      setPage(1)
    }
  }, [access.ready, validSelection])

  const chosen = access.sources.find((source) => source.organization_id === selected)
  const filters: InvitedHousingFilters = {
    sourceKind: selected === "person" ? "person" : chosen ? "organization" : "all",
    ...(chosen ? { organizationId: chosen.organization_id! } : {}),
    status,
    page: validSelection ? page : 1,
    pageSize: 20,
  }

  return (
    <div className="invitationPage">
      <header>
        <h1>我的邀请</h1>
        <p>仅查看已授权受邀人员的住宿摘要，不含财务信息。</p>
      </header>
      {access.validating ? (
        <p role="status">正在验证邀请授权…</p>
      ) : access.error || failedEpoch === access.epoch ? (
        <div role="alert">
          <p>暂时无法确认邀请授权，旧信息已隐藏。</p>
          <button onClick={() => void access.revalidate()}>重试</button>
        </div>
      ) : !access.sources.length ? (
        <p>当前没有可用邀请来源</p>
      ) : (
        <>
          <div className="invitationFilters">
            <label>
              邀请来源
              <select
                value={selected}
                onChange={(event) => {
                  setSelection(event.target.value)
                  setPage(1)
                }}
              >
                <option value="all">全部授权来源</option>
                {access.sources.map((source) => (
                  <option
                    key={source.organization_id ?? "person"}
                    value={source.organization_id ?? "person"}
                  >
                    {source.source_kind === "person" ? "我邀请的" : source.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              住宿状态
              <select
                value={status ?? ""}
                onChange={(event) => {
                  setStatus((event.target.value || undefined) as InvitedHousingFilters["status"])
                  setPage(1)
                }}
              >
                <option value="">全部状态</option>
                {Object.entries(housingStatusLabels).map(([value, label]) => (
                  <option value={value} key={value}>{label}</option>
                ))}
              </select>
            </label>
            <button onClick={() => void access.revalidate()}>重新验证授权</button>
          </div>
          <InvitationList
            key={`${selected}:${status}:${page}:${access.epoch}`}
            accountId={accountId}
            filters={filters}
            epoch={access.epoch}
            onPage={setPage}
            onError={setFailedEpoch}
          />
        </>
      )}
    </div>
  )
}

export function MyInvitationsPage() {
  const { user } = useAuth()
  return user ? (
    <InvitationContent key={user.account_id} accountId={user.account_id} />
  ) : null
}
