import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Save } from "lucide-react"
import { useEffect, useState } from "react"
import type { FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { MyTeamPanel } from "../teams/MyTeamPanel"
import {
  getSelfProfile,
  selfProfileKeys,
  updateSelfProfile,
} from "./api"
import type { SelfProfile } from "./api"
import { currentAge, personTypeText } from "./presentation"
import "./self-profile.css"

const NICKNAME_LIMIT = 40

function mutationMessage(error: unknown): string {
  return error instanceof ProblemDetailsError
    ? error.problem.detail
    : "昵称暂时无法保存，请稍后重试。"
}

export function SelfProfilePanel({ personId, accountId }: { personId: string; accountId?: string }) {
  const auth = useOptionalAuth()
  const teamAccountId = accountId ?? auth?.user?.account_id
  const queryClient = useQueryClient()
  const [nickname, setNickname] = useState("")
  const profile = useQuery({
    queryKey: selfProfileKeys.detail(personId),
    queryFn: getSelfProfile,
  })
  const mutation = useMutation<SelfProfile, unknown, string | null>({
    mutationFn: updateSelfProfile,
    onSuccess: (updated) => {
      queryClient.setQueryData(selfProfileKeys.detail(personId), updated)
      setNickname(updated.nickname ?? "")
      void queryClient.invalidateQueries({ queryKey: ["people"] })
      void queryClient.invalidateQueries({ queryKey: ["person", personId] })
    },
  })

  useEffect(() => {
    setNickname(profile.data?.nickname ?? "")
  }, [profile.data?.nickname, profile.data?.person_id])

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (mutation.isPending) return
    const normalized = nickname.trim()
    mutation.mutate(normalized || null)
  }

  if (profile.isLoading) {
    return <section className="selfProfilePanel selfProfileState">正在读取个人信息…</section>
  }
  if (profile.isError || !profile.data) {
    return (
      <section className="selfProfilePanel selfProfileState" role="alert">
        个人信息暂时无法读取，请刷新页面重试。
      </section>
    )
  }

  const detail = profile.data
  const normalizedNickname = nickname.trim() || null
  const hasChanges = normalizedNickname !== detail.nickname

  return (
    <section className="selfProfilePanel" aria-labelledby="self-profile-title">
      <header className="selfProfileHeader">
        <div>
          <p className="pageEyebrow">BASIC PROFILE</p>
          <h2 id="self-profile-title">基本信息</h2>
          <p>实名资料仅供查看，昵称可随时修改。</p>
        </div>
        <span className="selfProfilePrivacy">敏感信息已脱敏</span>
      </header>

      <div className="selfProfileContent">
        <dl className="selfProfileFacts">
          <div>
            <dt>真实姓名</dt>
            <dd>{detail.full_name}</dd>
          </div>
          <div>
            <dt>昵称</dt>
            <dd>{detail.nickname ?? "未设置"}</dd>
          </div>
          <div>
            <dt>人员身份</dt>
            <dd>{personTypeText(detail.person_types, detail.staff_type)}</dd>
          </div>
          <div>
            <dt>性别</dt>
            <dd>{detail.sex === "female" ? "女" : "男"}</dd>
          </div>
          <div>
            <dt>出生日期</dt>
            <dd>{detail.date_of_birth}</dd>
          </div>
          <div>
            <dt>年龄</dt>
            <dd>
              {currentAge(detail.date_of_birth)} 岁 · {detail.age_status === "adult" ? "成年" : "未成年"}
            </dd>
          </div>
          <div>
            <dt>证件号码</dt>
            <dd className="selfProfileMasked">{detail.masked_identity}</dd>
          </div>
          <div>
            <dt>手机号</dt>
            <dd>{detail.masked_phone ?? "未填写"}</dd>
          </div>
          <div>
            <dt>邮箱</dt>
            <dd>{detail.masked_email ?? "未填写"}</dd>
          </div>
        </dl>

        <form className="selfProfileNicknameForm" onSubmit={submit}>
          <div>
            <label htmlFor="self-profile-nickname">设置昵称</label>
            <p id="self-profile-nickname-help">昵称会显示在个人资料和人员中心，留空保存可清除。</p>
          </div>
          <input
            id="self-profile-nickname"
            value={nickname}
            maxLength={NICKNAME_LIMIT}
            aria-describedby="self-profile-nickname-help self-profile-nickname-count"
            autoComplete="nickname"
            onChange={(event) => {
              mutation.reset()
              setNickname(event.target.value)
            }}
            placeholder="输入昵称"
          />
          <span id="self-profile-nickname-count" className="selfProfileCounter">
            {nickname.length}/{NICKNAME_LIMIT}
          </span>
          <button
            className="primaryButton selfProfileSaveButton"
            type="submit"
            disabled={!hasChanges || mutation.isPending}
          >
            <Save size={16} aria-hidden="true" />
            {mutation.isPending ? "正在保存" : "保存昵称"}
          </button>
          {mutation.isSuccess ? (
            <p className="selfProfileSuccess" role="status">昵称已保存。</p>
          ) : null}
          {mutation.isError ? (
            <p className="selfProfileError" role="alert">{mutationMessage(mutation.error)}</p>
          ) : null}
        </form>
      </div>
      {teamAccountId ? <MyTeamPanel accountId={teamAccountId} /> : null}
    </section>
  )
}
