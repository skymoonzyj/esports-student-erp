import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { Link, useSearchParams } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { getPeopleCounts, listPeople } from "./api"
import type { PeopleFilters, PersonCounts, PersonType, StaffType } from "./api"
import { ageText, personAccountStatusLabels, personTypeText, teamAllocationText, teamProjectionText } from "./presentation"
import "./people.css"

type CountCard = {
  key: keyof PersonCounts
  label: string
  type?: PersonType
  staffType?: StaffType
  role?: "team_member" | "team_manager"
}

const countCards: CountCard[] = [
  { key: "total", label: "全部人员" },
  { key: "student", label: "学员", type: "student" },
  { key: "guardian", label: "监护人", type: "guardian" },
  { key: "staff", label: "工作人员", type: "staff" },
  { key: "coach", label: "教练", type: "staff", staffType: "coach" },
  {
    key: "life_teacher",
    label: "生活老师",
    type: "staff",
    staffType: "life_teacher",
  },
  {
    key: "customer_service",
    label: "客服",
    type: "staff",
    staffType: "customer_service",
  },
  {
    key: "administration",
    label: "行政",
    type: "staff",
    staffType: "administration",
  },
  {
    key: "other_staff",
    label: "其他工作人员",
    type: "staff",
    staffType: "other",
  },
  { key: "visitor", label: "访客", type: "visitor" },
  { key: "team_member", label: "队员", role: "team_member" },
  { key: "team_manager", label: "队伍管理", role: "team_manager" },
]

function personTypeParam(value: string | null): PersonType | "" {
  return value && ["student", "guardian", "staff", "visitor"].includes(value)
    ? (value as PersonType)
    : ""
}

function staffTypeParam(value: string | null): StaffType | "" {
  return value && [
    "coach",
    "life_teacher",
    "customer_service",
    "administration",
    "other",
  ].includes(value)
    ? (value as StaffType)
    : ""
}

function countKeyFor(
  personType: PersonType | "",
  staffType: StaffType | "",
): keyof PersonCounts | null {
  if (!personType) return "total"
  if (personType === "staff") {
    if (staffType) return staffType === "other" ? "other_staff" : staffType
    return "staff"
  }
  return personType
}

function isAuthorizationDenied(error: unknown): boolean {
  return error instanceof ProblemDetailsError && [401, 403, 404].includes(error.problem.status)
}

export function PeoplePage({ canCreate = false }: { canCreate?: boolean }) {
  const auth = useOptionalAuth()
  const scope = auth ? JSON.stringify([auth.status, auth.user?.account_id, auth.user?.is_super_admin, [...auth.user?.permissions ?? []].sort(), [...auth.user?.scoped_permissions ?? []].sort()]) : "legacy"
  if (auth && (auth.status !== "authenticated" || !auth.user)) return <div className="peopleState">正在读取当前权限…</div>
  if (auth?.user && ![...auth.user.permissions, ...auth.user.scoped_permissions ?? []].some(permission => permission === "people.list" || permission === "people.read")) return <div className="peopleState" role="alert">人员名单权限已撤销，正在刷新当前账号权限。</div>
  return <AccountPeoplePage key={scope} canCreate={canCreate} scope={scope} />
}

function AccountPeoplePage({ canCreate, scope }: { canCreate: boolean; scope: string }) {
  const queryClient = useQueryClient()
  const auth = useOptionalAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const initialPhone = searchParams.get("phone") ?? ""
  const initialName = searchParams.get("name") ?? ""
  const initialStaffType = staffTypeParam(searchParams.get("staff_type"))
  const initialType = initialStaffType
    ? "staff"
    : personTypeParam(searchParams.get("type"))
  const initialRole = searchParams.get("role") === "team_member" || searchParams.get("role") === "team_manager"
    ? searchParams.get("role") as "team_member" | "team_manager" : ""
  const allocationParam = searchParams.get("team_allocation_status")
  const initialAllocation = allocationParam === "pending" || allocationParam === "assigned" ? allocationParam : ""
  const initialPage = Math.max(1, Number(searchParams.get("page")) || 1)
  const [phone, setPhone] = useState(initialPhone)
  const [name, setName] = useState(initialName)
  const [personType, setPersonType] = useState<PersonType | "">(initialType)
  const [staffType, setStaffType] = useState<StaffType | "">(initialStaffType)
  const [role, setRole] = useState<"team_member" | "team_manager" | "">(initialRole)
  const [allocation, setAllocation] = useState<"pending" | "assigned" | "">(initialAllocation)
  const [activeCountKey, setActiveCountKey] = useState<keyof PersonCounts | null>(
    initialRole || countKeyFor(initialType, initialStaffType),
  )
  const [accessRevoked, setAccessRevoked] = useState(false)
  const [filters, setFilters] = useState<PeopleFilters>({
    phone: initialPhone || undefined,
    name: initialName || undefined,
    type: initialType,
    staffType: initialStaffType,
    role: initialRole,
    teamAllocationStatus: initialAllocation,
    page: initialPage,
    pageSize: 20,
  })
  const peopleCounts = useQuery({
    queryKey: ["people-counts", scope],
    queryFn: ({ signal }) => getPeopleCounts(signal),
    enabled: !accessRevoked,
    retry: false,
  })
  const canFilterAllocation = typeof peopleCounts.data?.pending_team_allocation === "number" && typeof peopleCounts.data?.assigned_team_allocation === "number"
  const allocationVisibility = canFilterAllocation ? "allocation-visible" : peopleCounts.data?.pending_team_allocation === null && peopleCounts.data?.assigned_team_allocation === null ? "allocation-private" : "allocation-unknown"
  const effectiveFilters = canFilterAllocation ? filters : { ...filters, teamAllocationStatus: undefined }
  const people = useQuery({
    queryKey: ["people", effectiveFilters, allocationVisibility, scope],
    queryFn: ({ signal }) => listPeople(effectiveFilters, signal),
    enabled: !accessRevoked && peopleCounts.isFetched && !isAuthorizationDenied(peopleCounts.error),
    retry: false,
  })
  const explicitAuthorizationDenial =
    isAuthorizationDenied(people.error) || isAuthorizationDenied(peopleCounts.error)
  const permissionRevoked = accessRevoked || explicitAuthorizationDenial

  useEffect(() => () => {
    const predicate = (query: { queryKey: readonly unknown[] }) => ["people", "people-counts"].includes(String(query.queryKey[0])) && query.queryKey.at(-1) === scope
    void queryClient.cancelQueries({ predicate })
    queryClient.removeQueries({ predicate })
  }, [queryClient, scope])

  useEffect(() => {
    if (!peopleCounts.isFetched || canFilterAllocation) return
    const predicate = (query: { queryKey: readonly unknown[] }) => query.queryKey[0] === "people" && query.queryKey.at(-1) === scope && query.queryKey[2] === "allocation-visible"
    void queryClient.cancelQueries({ predicate })
    queryClient.removeQueries({ predicate })
    if (!filters.teamAllocationStatus) return
    setAllocation("")
    setFilters(current => ({ ...current, teamAllocationStatus: undefined, page: 1 }))
    setSearchParams(current => { const next = new URLSearchParams(current); next.delete("team_allocation_status"); next.delete("page"); return next }, { replace: true })
  }, [canFilterAllocation, filters.teamAllocationStatus, peopleCounts.isFetched, queryClient, scope, setSearchParams])

  useEffect(() => {
    if (!explicitAuthorizationDenial || accessRevoked) return
    setAccessRevoked(true)
    const roots = [["people"], ["people-counts"]] as const
    const clearSensitiveQueries = () => {
      for (const queryKey of roots) {
        void queryClient.cancelQueries({ queryKey })
        queryClient.removeQueries({ queryKey })
      }
    }
    clearSensitiveQueries()
    if (auth?.user) {
      void auth.refreshUser({ background: true })
        .catch(() => undefined)
        .finally(clearSensitiveQueries)
    }
  }, [accessRevoked, auth, explicitAuthorizationDenial, queryClient])

  function search(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const nextPhone = phone.trim()
    const nextName = name.trim()
    const nextStaffType = personType === "staff" ? staffType : ""
    const nextFilters = {
      phone: nextPhone || undefined,
      name: nextName || undefined,
      type: personType,
      staffType: nextStaffType,
      role,
      teamAllocationStatus: canFilterAllocation ? allocation : undefined,
      page: 1,
      pageSize: 20,
    } satisfies PeopleFilters
    setFilters(nextFilters)
    const nextParams = new URLSearchParams()
    if (nextPhone) nextParams.set("phone", nextPhone)
    if (nextName) nextParams.set("name", nextName)
    if (personType) nextParams.set("type", personType)
    if (nextStaffType) nextParams.set("staff_type", nextStaffType)
    if (role) nextParams.set("role", role)
    if (canFilterAllocation && allocation) nextParams.set("team_allocation_status", allocation)
    setSearchParams(nextParams, { replace: true })
    setActiveCountKey(
      nextPhone || nextName ? null : role || countKeyFor(personType, nextStaffType),
    )
  }

  function reset(): void {
    setPhone("")
    setName("")
    setPersonType("")
    setStaffType("")
    setRole("")
    setAllocation("")
    setActiveCountKey("total")
    setFilters({ page: 1, pageSize: 20 })
    setSearchParams({}, { replace: true })
  }

  function selectCountCard(card: CountCard): void {
    const nextType = card.type ?? ""
    const nextStaffType = card.staffType ?? ""
    setPhone("")
    setName("")
    setPersonType(nextType)
    setStaffType(nextStaffType)
    setRole(card.role ?? "")
    setActiveCountKey(card.key)
    setFilters({
      type: nextType,
      staffType: nextStaffType,
      role: card.role ?? "",
      teamAllocationStatus: canFilterAllocation ? allocation : undefined,
      page: 1,
      pageSize: 20,
    })
    const nextParams = new URLSearchParams()
    if (nextType) nextParams.set("type", nextType)
    if (nextStaffType) nextParams.set("staff_type", nextStaffType)
    if (card.role) nextParams.set("role", card.role)
    if (canFilterAllocation && allocation) nextParams.set("team_allocation_status", allocation)
    setSearchParams(nextParams, { replace: true })
  }

  function changePage(page: number) {
    setFilters(current => ({ ...current, page }))
    setSearchParams(current => { const next = new URLSearchParams(current); if (page > 1) next.set("page", String(page)); else next.delete("page"); return next }, { replace: true })
  }

  return (
    <div className="peoplePage">
      <div className="peoplePageHeading">
        <div>
          <p className="pageEyebrow">IDENTITY DIRECTORY</p>
          <h1>人员中心</h1>
          <p>统一维护学员、监护人、教练、生活老师和其他人员档案。</p>
        </div>
        {canCreate ? (
          <Link className="primaryButton peopleCreateButton" to="/people/new">
            新建人员
          </Link>
        ) : null}
      </div>

      {!permissionRevoked ? <section className="peopleCounts" aria-label="人员数量统计">
        {countCards.map((card) => (
          <button
            className={`peopleCountCard${activeCountKey === card.key ? " isActive" : ""}`}
            type="button"
            key={card.key}
            aria-pressed={activeCountKey === card.key}
            onClick={() => selectCountCard(card)}
          >
            <span>{card.label}</span>
            <strong>{peopleCounts.data?.[card.key] ?? "—"}</strong>
          </button>
        ))}
        <div className="peopleCountCard"><span>在队人员</span><strong>{peopleCounts.data?.in_team ?? "—"}</strong></div>
        {canFilterAllocation ? <>
          <div className="peopleCountCard"><span>待划分队伍</span><strong>{peopleCounts.data?.pending_team_allocation}</strong></div>
          <div className="peopleCountCard"><span>已划分队伍</span><strong>{peopleCounts.data?.assigned_team_allocation}</strong></div>
        </> : null}
      </section> : null}

      <form className="peopleFilters" onSubmit={search}>
        <div className="peopleFilterField">
          <label htmlFor="people-phone">手机号查询</label>
          <input
            id="people-phone"
            type="tel"
            inputMode="numeric"
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
            placeholder="输入完整手机号精确查询"
          />
        </div>
        <div className="peopleFilterField">
          <label htmlFor="people-name">姓名或昵称</label>
          <input
            id="people-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="输入姓名或昵称"
          />
        </div>
        <div className="peopleFilterField">
          <label htmlFor="people-role">角色筛选</label>
          <select id="people-role" value={role} onChange={(event) => { setRole(event.target.value as typeof role); setActiveCountKey(null) }}>
            <option value="">全部角色</option>
            <option value="team_member">队员</option>
            <option value="team_manager">队伍管理</option>
          </select>
        </div>
        <div className="peopleFilterField">
          <label htmlFor="people-type">人员类型</label>
          <select
            id="people-type"
            value={personType}
            onChange={(event) => {
              const nextType = event.target.value as PersonType | ""
              setPersonType(nextType)
              if (nextType !== "staff") setStaffType("")
              setActiveCountKey(null)
            }}
          >
            <option value="">全部类型</option>
            <option value="student">学员</option>
            <option value="guardian">监护人</option>
            <option value="staff">工作人员</option>
            <option value="visitor">访客</option>
          </select>
        </div>
        {!permissionRevoked && canFilterAllocation ? <div className="peopleFilterField">
          <label htmlFor="people-allocation">队伍归属</label>
          <select id="people-allocation" value={allocation} onChange={event => setAllocation(event.target.value as typeof allocation)}>
            <option value="">全部</option><option value="pending">待划分队伍</option><option value="assigned">已划分队伍</option>
          </select>
        </div> : null}
        <div className="peopleFilterActions">
          <button className="primaryButton" type="submit">
            查询
          </button>
          <button className="secondaryButton" type="button" onClick={reset}>
            清空
          </button>
        </div>
      </form>

      <section className="peopleDirectory" aria-labelledby="people-result-title">
        <div className="peopleResultHeader">
          <div>
            <h2 id="people-result-title">人员名单</h2>
            <span>{!permissionRevoked && people.data ? `共 ${people.data.total} 人` : "正在统计"}</span>
          </div>
          {people.isFetching ? <span className="subtleStatus">正在更新…</span> : null}
        </div>

        {permissionRevoked ? (
          <div className="peopleState" role="alert">
            人员名单权限已撤销，正在刷新当前账号权限。
          </div>
        ) : people.isError ? (
          <div className="peopleState" role="alert">
            暂时无法读取人员名单，请稍后重试。
          </div>
        ) : null}
        {!permissionRevoked && people.isLoading ? <div className="peopleState">正在加载人员档案…</div> : null}
        {!permissionRevoked && people.data?.items.length === 0 ? (
          <div className="peopleState">没有找到符合条件的人员。</div>
        ) : null}

        {!permissionRevoked && people.data?.items.length ? (
          <div className="peopleTable" role="table" aria-label="人员名单">
            <div className="peopleTableHeader" role="row">
              <span role="columnheader">姓名 / 类型</span>
              <span role="columnheader">昵称</span>
              <span role="columnheader">联系方式</span>
              <span role="columnheader">证件号码</span>
              <span role="columnheader">出生日期 / 成年状态</span>
              <span role="columnheader">操作</span>
            </div>
            {people.data.items.map((person) => (
              <div className="peopleTableRow" role="row" key={person.id}>
                <div className="peopleIdentityCell" role="cell" data-label="姓名 / 类型">
                  <span className="peopleInitial" aria-hidden="true">
                    {person.full_name.slice(0, 1)}
                  </span>
                  <span>
                    <Link className="peopleNameLink" to={`/people/${person.id}`}>
                      <strong>{person.full_name}</strong>
                    </Link>
                    <small>{personTypeText(person.person_types, person.staff_type, person.roles, person.assisted_kind)}</small>
                    {person.account_status ? <small>{personAccountStatusLabels[person.account_status]}</small> : null}
                    <small>队伍：<span>{teamProjectionText(person.team)}</span></small>
                    {person.team_allocation ? <small>队伍划分：<span>{teamAllocationText(person.team_allocation)}</span></small> : null}
                  </span>
                </div>
                <div className="peopleNicknameCell" role="cell" data-label="昵称">
                  <strong className={person.nickname ? undefined : "isEmpty"}>
                    {person.nickname ?? "未设置"}
                  </strong>
                </div>
                <div role="cell" data-label="联系方式">
                  <strong>{person.phone ?? "未填写手机号"}</strong>
                  <small>{person.email ?? "未填写邮箱"}</small>
                </div>
                <div role="cell" data-label="证件号码">
                  <span className="maskedValue">{person.identity_number}</span>
                </div>
                <div
                  className="peopleAgeCell"
                  role="cell"
                  data-label="出生日期 / 成年状态"
                >
                  <span>{ageText(person.date_of_birth)}{person.date_of_birth ? ` · ${person.date_of_birth}` : ""}</span>
                  {person.age_status !== "unknown" && person.date_of_birth ? <span
                    className={`ageBadge ${person.age_status}`}
                    aria-label={person.age_status === "adult" ? "成年" : "未成年"}
                  >
                    {person.age_status === "adult" ? "成年" : "未成年"}
                  </span> : null}
                  {person.guardian_status === "pending" ? (
                    <Link className="guardianPendingBadge" to={`/people/${person.id}`}>
                      待绑定监护人
                    </Link>
                  ) : person.guardian_status === "ready" ? (
                    <span className="guardianReadyBadge">监护人已绑定</span>
                  ) : null}
                </div>
                <div role="cell" data-label="操作">
                  <Link className="peopleDetailLink" to={`/people/${person.id}`}>
                    查看档案
                  </Link>
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {!permissionRevoked && people.data && people.data.total > (filters.pageSize ?? 20) ? <nav className="peoplePagination" aria-label="人员名单分页">
          <button className="secondaryButton" type="button" disabled={(filters.page ?? 1) <= 1 || people.isFetching} onClick={() => changePage((filters.page ?? 1) - 1)}>上一页</button>
          <span>第 {filters.page ?? 1} 页</span>
          <button className="secondaryButton" type="button" disabled={(filters.page ?? 1) * (filters.pageSize ?? 20) >= people.data.total || people.isFetching} onClick={() => changePage((filters.page ?? 1) + 1)}>下一页</button>
        </nav> : null}
      </section>
    </div>
  )
}
