import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import type { FormEvent } from "react"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import {
  addGuardian,
  endGuardianRelationship,
  getReadiness,
  listGuardians,
  listPeople,
} from "./api"
import type { GuardianIssueCode, Readiness } from "./api"
import "./people.css"

function shanghaiDate(): string {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date())
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${value.year}-${value.month}-${value.day}`
}

const readinessLabels: Record<string, string> = {
  full_name: "姓名",
  identity_number: "身份证",
  sex: "性别",
  date_of_birth: "出生日期",
  phone: "手机号",
  email: "邮箱",
  guardian: "有效实名监护人",
}

const issueLabels: Record<GuardianIssueCode, string> = {
  guardian_underage: "监护人未满 18 周岁",
  guardian_profile_incomplete: "监护人实名资料不完整",
  guardian_account_missing: "尚未开通独立账号",
  guardian_account_disabled: "监护人账号未启用",
  guardian_role_missing: "缺少监护人角色",
  relationship_not_started: "监护关系尚未生效",
  relationship_expired: "监护关系已过期",
  relationship_ended: "监护关系已结束",
  relationship_verification_required: "监护关系待验证",
}

const relationshipStatusLabels = {
  scheduled: "待生效",
  active: "生效中",
  expired: "已过期",
  ended: "已结束",
} as const

function readinessTitle(readiness: Readiness): string {
  if (readiness.guardian_status === "ready") return "未成年 · 监护人已绑定"
  if (readiness.guardian_status === "pending") return "未成年 · 待绑定监护人"
  return "成年 · 监护关系非强制"
}

export function GuardianPanel({
  studentId,
  canManage = false,
}: {
  studentId: string
  canManage?: boolean
}) {
  const queryClient = useQueryClient()
  const referenceDate = shanghaiDate()
  const [binding, setBinding] = useState(false)
  const [name, setName] = useState("")
  const [searchedName, setSearchedName] = useState("")
  const [guardianId, setGuardianId] = useState("")
  const [relationship, setRelationship] = useState("父亲")
  const [isPrimary, setIsPrimary] = useState(false)
  const [validFrom, setValidFrom] = useState(referenceDate)
  const [validUntil, setValidUntil] = useState("")
  const [endingRelationship, setEndingRelationship] = useState<{
    id: string
    name: string
  } | null>(null)
  const [endReason, setEndReason] = useState("")
  const [error, setError] = useState<string | null>(null)

  const guardians = useQuery({
    queryKey: ["guardians", studentId, { includeInactive: canManage }],
    queryFn: () => listGuardians(studentId, canManage),
  })
  const readiness = useQuery({
    queryKey: ["readiness", studentId, referenceDate],
    queryFn: () => getReadiness(studentId, referenceDate),
  })
  const candidates = useQuery({
    queryKey: ["guardian-candidates", searchedName],
    queryFn: () => listPeople({ name: searchedName, page: 1, pageSize: 10 }),
    enabled: searchedName.length > 0,
  })

  async function refreshGuardianState(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["guardians", studentId] }),
      queryClient.invalidateQueries({ queryKey: ["readiness", studentId] }),
      queryClient.invalidateQueries({ queryKey: ["people"] }),
      queryClient.invalidateQueries({ queryKey: ["person", studentId] }),
    ])
  }

  const bindGuardian = useMutation({
    mutationFn: () =>
      addGuardian(studentId, {
        guardian_id: guardianId,
        relationship,
        is_primary: isPrimary,
        valid_from: validFrom,
        valid_until: validUntil || null,
      }),
    onSuccess: async () => {
      setBinding(false)
      setName("")
      setSearchedName("")
      setGuardianId("")
      setError(null)
      await refreshGuardianState()
    },
    onError: (caught) => {
      setError(
        caught instanceof ProblemDetailsError ? caught.problem.detail : "暂时无法绑定监护人",
      )
    },
  })
  const endRelationship = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      endGuardianRelationship(studentId, id, reason),
    onSuccess: async () => {
      setEndingRelationship(null)
      setEndReason("")
      setError(null)
      await refreshGuardianState()
    },
    onError: (caught) => {
      setError(
        caught instanceof ProblemDetailsError ? caught.problem.detail : "暂时无法结束监护关系",
      )
    },
  })

  function search(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setSearchedName(name.trim())
    setGuardianId("")
  }

  function submitBinding(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!guardianId) {
      setError("请选择一名已有人员")
      return
    }
    setError(null)
    bindGuardian.mutate()
  }

  return (
    <section className="guardianPanel" aria-labelledby="guardian-panel-title">
      <div className="guardianPanelHeader">
        <div>
          <p className="detailCardEyebrow">GUARDIANS</p>
          <h2 id="guardian-panel-title">监护人关系</h2>
        </div>
        {canManage ? (
          <button className="secondaryButton" type="button" onClick={() => setBinding(true)}>
            绑定监护人
          </button>
        ) : null}
      </div>

      {readiness.data ? (
        <div
          className={`readinessBanner ${
            readiness.data.guardian_status === "pending"
              ? "missing"
              : readiness.data.guardian_status === "ready"
                ? "ready"
                : "neutral"
          }`}
        >
          <strong>{readinessTitle(readiness.data)}</strong>
          <span>
            {readiness.data.guardian_status === "ready"
              ? "当前资料已满足账号与后续业务校验基础。"
              : readiness.data.guardian_status === "pending"
                ? `待补充：${readiness.data.missing.map((item) => readinessLabels[item] ?? item).join("、")}`
                : "成年学员仍可保留或新建监护关系。"}
          </span>
        </div>
      ) : null}

      {readiness.data?.guardian_issues?.length ? (
        <ul className="guardianIssueList" aria-label="监护就绪诊断">
          {readiness.data.guardian_issues.flatMap((issue) =>
            issue.reason_codes.map((reason) => (
              <li className="guardianIssue" key={`${issue.relationship_id}-${reason}`}>
                <span>{issueLabels[reason]}</span>
                <div className="guardianRepairLinks">
                  {reason === "guardian_underage" || reason === "guardian_profile_incomplete" ? (
                    <Link to={`/people/${issue.guardian_person_id}`}>完善监护人资料</Link>
                  ) : null}
                  {reason === "guardian_account_missing" || reason === "guardian_account_disabled" ? (
                    <Link to="/settings/accounts">前往账号管理</Link>
                  ) : null}
                  {reason === "guardian_role_missing" ? (
                    <Link to="/settings/permissions">前往权限管理</Link>
                  ) : null}
                </div>
              </li>
            )),
          )}
        </ul>
      ) : null}

      {guardians.isLoading ? <div className="peopleState">正在读取监护人关系…</div> : null}
      {guardians.isError ? (
        <div className="peopleState" role="alert">
          当前账号无权查看监护人资料。
        </div>
      ) : null}
      {guardians.data?.length === 0 ? (
        <div className="guardianEmpty">
          {canManage ? "暂无监护人关系记录。" : "暂未绑定有效监护人。"}
        </div>
      ) : null}
      {guardians.data?.length ? (
        <div className="guardianList">
          {guardians.data.map((guardian) => (
            <article key={guardian.relationship_id}>
              <span className="peopleInitial" aria-hidden="true">
                {guardian.full_name.slice(0, 1)}
              </span>
              <div>
                <div className="guardianNameLine">
                  <strong>{guardian.full_name}</strong>
                  {guardian.is_primary ? <span>主要监护人</span> : null}
                  <span className={`guardianHistoryStatus ${guardian.status}`}>
                    {relationshipStatusLabels[guardian.status]}
                  </span>
                </div>
                <small>
                  {guardian.relationship} · {guardian.phone ?? "未填写手机号"} · 生效于 {guardian.valid_from}
                  {guardian.valid_until ? ` · 有效至 ${guardian.valid_until}` : ""}
                </small>
                {guardian.end_reason ? (
                  <small className="guardianEndReason">结束原因：{guardian.end_reason}</small>
                ) : null}
              </div>
              {canManage && guardian.status !== "ended" ? (
                <button
                  className="guardianRemoveButton"
                  type="button"
                  onClick={() => {
                    setError(null)
                    setEndReason("")
                    setEndingRelationship({
                      id: guardian.relationship_id,
                      name: guardian.full_name,
                    })
                  }}
                >
                  结束监护关系
                </button>
              ) : null}
            </article>
          ))}
        </div>
      ) : null}

      {error && !binding && !endingRelationship ? (
        <p className="formSubmitError" role="alert">
          {error}
        </p>
      ) : null}

      {binding ? (
        <div className="dialogBackdrop">
          <section
            className="guardianDialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="guardian-dialog-title"
          >
            <p className="detailCardEyebrow">LINK GUARDIAN</p>
            <h2 id="guardian-dialog-title">绑定已有监护人</h2>
            <p>先按姓名查找已建档人员，再设置与学员的关系和有效期。</p>

            <form className="guardianSearch" onSubmit={search}>
              <label htmlFor="guardian-search">查找已有监护人</label>
              <div>
                <input
                  id="guardian-search"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  minLength={2}
                  required
                />
                <button className="secondaryButton" type="submit">
                  搜索
                </button>
              </div>
            </form>

            {candidates.data ? (
              <div className="guardianCandidates">
                {candidates.data.items.length ? (
                  candidates.data.items.map((candidate) => (
                    <label key={candidate.id}>
                      <input
                        type="radio"
                        name="guardian-candidate"
                        value={candidate.id}
                        checked={guardianId === candidate.id}
                        onChange={(event) => setGuardianId(event.target.value)}
                        aria-label={`${candidate.full_name} ${candidate.phone ?? "未填写手机号"}`}
                      />
                      <span>
                        <strong>{candidate.full_name}</strong>
                        <small>{candidate.phone ?? "未填写手机号"}</small>
                      </span>
                    </label>
                  ))
                ) : (
                  <p>没有找到匹配人员，请先在人员中心建档。</p>
                )}
              </div>
            ) : null}

            <form className="guardianBindingForm" onSubmit={submitBinding}>
              <div className="formField">
                <label htmlFor="guardian-relationship">关系</label>
                <input
                  id="guardian-relationship"
                  value={relationship}
                  onChange={(event) => setRelationship(event.target.value)}
                  minLength={2}
                  required
                />
              </div>
              <label className="guardianPrimaryCheck">
                <input
                  type="checkbox"
                  checked={isPrimary}
                  onChange={(event) => setIsPrimary(event.target.checked)}
                />
                设为主要监护人
              </label>
              <div className="guardianDateGrid">
                <div className="formField">
                  <label htmlFor="guardian-valid-from">生效日期</label>
                  <input
                    id="guardian-valid-from"
                    type="date"
                    value={validFrom}
                    onChange={(event) => setValidFrom(event.target.value)}
                    required
                  />
                </div>
                <div className="formField">
                  <label htmlFor="guardian-valid-until">失效日期（选填）</label>
                  <input
                    id="guardian-valid-until"
                    type="date"
                    value={validUntil}
                    onChange={(event) => setValidUntil(event.target.value)}
                  />
                </div>
              </div>
              {error ? (
                <p className="formError" role="alert">
                  {error}
                </p>
              ) : null}
              <div className="dialogActions">
                <button className="secondaryButton" type="button" onClick={() => setBinding(false)}>
                  取消
                </button>
                <button className="primaryButton" type="submit" disabled={bindGuardian.isPending}>
                  {bindGuardian.isPending ? "正在绑定…" : "确认绑定"}
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}

      {endingRelationship ? (
        <div className="dialogBackdrop">
          <section
            className="guardianDialog guardianEndDialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="guardian-end-title"
          >
            <p className="detailCardEyebrow">END RELATIONSHIP</p>
            <h2 id="guardian-end-title">结束监护关系</h2>
            <p>结束后将保留 {endingRelationship.name} 的关系历史。</p>
            <form
              className="guardianEndForm"
              onSubmit={(event) => {
                event.preventDefault()
                const normalizedReason = endReason.trim()
                if (normalizedReason.length < 2) {
                  setError("结束原因至少需要 2 个字符")
                  return
                }
                setError(null)
                endRelationship.mutate({
                  id: endingRelationship.id,
                  reason: normalizedReason,
                })
              }}
            >
              <label htmlFor="guardian-end-reason">结束原因</label>
              <textarea
                id="guardian-end-reason"
                value={endReason}
                onChange={(event) => setEndReason(event.target.value)}
                minLength={2}
                maxLength={200}
                required
              />
              {error ? (
                <p className="formError" role="alert">
                  {error}
                </p>
              ) : null}
              <div className="guardianEndActions">
                <button
                  className="secondaryButton"
                  type="button"
                  onClick={() => {
                    setEndingRelationship(null)
                    setEndReason("")
                    setError(null)
                  }}
                >
                  取消
                </button>
                <button
                  className="primaryButton"
                  type="submit"
                  disabled={endRelationship.isPending}
                >
                  {endRelationship.isPending ? "正在结束…" : "确认结束"}
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}
    </section>
  )
}
