import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import type { FormEvent } from "react"
import { Link, useSearchParams } from "react-router-dom"

import { listPeople } from "./api"
import type { PeopleFilters, PersonType } from "./api"
import "./people.css"

const typeLabels: Record<PersonType, string> = {
  student: "学员",
  guardian: "监护人",
  staff: "工作人员",
  visitor: "访客",
}

function personTypeText(types: PersonType[]): string {
  return types.length ? types.map((type) => typeLabels[type]).join(" · ") : "普通人员"
}

function personTypeParam(value: string | null): PersonType | "" {
  return value && value in typeLabels ? (value as PersonType) : ""
}

export function PeoplePage({ canCreate = false }: { canCreate?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialPhone = searchParams.get("phone") ?? ""
  const initialName = searchParams.get("name") ?? ""
  const initialType = personTypeParam(searchParams.get("type"))
  const [phone, setPhone] = useState(initialPhone)
  const [name, setName] = useState(initialName)
  const [personType, setPersonType] = useState<PersonType | "">(initialType)
  const [filters, setFilters] = useState<PeopleFilters>({
    phone: initialPhone || undefined,
    name: initialName || undefined,
    type: initialType,
    page: 1,
    pageSize: 20,
  })
  const people = useQuery({
    queryKey: ["people", filters],
    queryFn: () => listPeople(filters),
  })

  function search(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const nextPhone = phone.trim()
    const nextName = name.trim()
    const nextFilters = {
      phone: nextPhone || undefined,
      name: nextName || undefined,
      type: personType,
      page: 1,
      pageSize: 20,
    } satisfies PeopleFilters
    setFilters(nextFilters)
    const nextParams = new URLSearchParams()
    if (nextPhone) nextParams.set("phone", nextPhone)
    if (nextName) nextParams.set("name", nextName)
    if (personType) nextParams.set("type", personType)
    setSearchParams(nextParams, { replace: true })
  }

  function reset(): void {
    setPhone("")
    setName("")
    setPersonType("")
    setFilters({ page: 1, pageSize: 20 })
    setSearchParams({}, { replace: true })
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
          <label htmlFor="people-name">姓名</label>
          <input
            id="people-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="输入姓名"
          />
        </div>
        <div className="peopleFilterField">
          <label htmlFor="people-type">人员类型</label>
          <select
            id="people-type"
            value={personType}
            onChange={(event) => setPersonType(event.target.value as PersonType | "")}
          >
            <option value="">全部类型</option>
            <option value="student">学员</option>
            <option value="guardian">监护人</option>
            <option value="staff">工作人员</option>
            <option value="visitor">访客</option>
          </select>
        </div>
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
            <span>{people.data ? `共 ${people.data.total} 人` : "正在统计"}</span>
          </div>
          {people.isFetching ? <span className="subtleStatus">正在更新…</span> : null}
        </div>

        {people.isError ? (
          <div className="peopleState" role="alert">
            暂时无法读取人员名单，请稍后重试。
          </div>
        ) : null}
        {people.isLoading ? <div className="peopleState">正在加载人员档案…</div> : null}
        {people.data?.items.length === 0 ? (
          <div className="peopleState">没有找到符合条件的人员。</div>
        ) : null}

        {people.data?.items.length ? (
          <div className="peopleTable" role="table" aria-label="人员名单">
            <div className="peopleTableHeader" role="row">
              <span role="columnheader">姓名 / 类型</span>
              <span role="columnheader">联系方式</span>
              <span role="columnheader">身份证</span>
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
                    <small>{personTypeText(person.person_types)}</small>
                  </span>
                </div>
                <div role="cell" data-label="联系方式">
                  <strong>{person.phone ?? "未填写手机号"}</strong>
                  <small>{person.email ?? "未填写邮箱"}</small>
                </div>
                <div role="cell" data-label="身份证">
                  <span className="maskedValue">{person.identity_number}</span>
                </div>
                <div
                  className="peopleAgeCell"
                  role="cell"
                  data-label="出生日期 / 成年状态"
                >
                  <span>{person.date_of_birth}</span>
                  <span className={`ageBadge ${person.age_status}`}>
                    {person.age_status === "adult" ? "成年" : "未成年"}
                  </span>
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
      </section>
    </div>
  )
}
