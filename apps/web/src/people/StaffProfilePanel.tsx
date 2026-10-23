import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import type { FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import { getStaffPersonProfile, updatePerson } from "./api"
import type { StaffPersonProfileUpdate } from "./api"

function isRevoked(error: unknown): boolean {
  return error instanceof ProblemDetailsError && [401, 403, 404].includes(error.problem.status)
}

export function StaffProfilePanel({ personId, accountId }: { personId: string; accountId: string }) {
  const client = useQueryClient()
  const queryKey = ["staff-person-profile", personId, accountId]
  const profile = useQuery({
    queryKey,
    queryFn: ({ signal }) => getStaffPersonProfile(personId, signal),
    staleTime: 0, gcTime: 0, retry: false,
  })
  const [editing, setEditing] = useState(false)
  const [nickname, setNickname] = useState("")
  const [notes, setNotes] = useState("")
  const [original, setOriginal] = useState<StaffPersonProfileUpdate>({})
  const [pending, setPending] = useState(false)
  const [revoked, setRevoked] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!profile.data || pending) return
    const input: StaffPersonProfileUpdate = {}
    const nextNickname = nickname.trim() || null
    const nextNotes = notes.trim() || null
    if (nextNickname !== original.nickname) input.nickname = nextNickname
    if (nextNotes !== original.profile_notes) input.profile_notes = nextNotes
    setPending(true)
    setError(null)
    try {
      if (Object.keys(input).length) await updatePerson(personId, input)
      setEditing(false)
      await Promise.all([
        client.invalidateQueries({ queryKey }),
        client.invalidateQueries({ queryKey: ["person", personId] }),
        client.invalidateQueries({ queryKey: ["people"] }),
        client.invalidateQueries({ queryKey: ["self-profile", personId] }),
      ])
    } catch (failure) {
      if (isRevoked(failure)) {
        setRevoked(true)
        setEditing(false)
        setNickname("")
        setNotes("")
        client.removeQueries({ queryKey })
      } else {
        setError(failure instanceof ProblemDetailsError ? failure.problem.detail : "昵称与内部备注暂时无法保存")
      }
    } finally {
      setPending(false)
    }
  }

  if (revoked || isRevoked(profile.error)) return <p role="alert">无权查看内部备注</p>
  if (profile.isLoading) return <p>正在读取内部备注…</p>
  if (!profile.data || profile.isError) return <p role="alert">内部备注暂时无法读取</p>
  const data = profile.data
  return <section className="detailCard staffProfileCard" aria-labelledby="staff-profile-title">
    <div className="detailCardHeader"><h2 id="staff-profile-title">内部备注</h2><span className="privacyBadge">仅授权后台人员可见</span></div>
    {editing ? <form onSubmit={save} className="staffProfileForm">
      <label>昵称<input value={nickname} maxLength={40} onChange={event => setNickname(event.target.value)} disabled={pending} /></label>
      <label>内部备注<textarea value={notes} maxLength={2000} rows={5} onChange={event => setNotes(event.target.value)} disabled={pending} /></label>
      <p className="formHint">最多 2000 字；请勿填写密码或不必要的敏感资料。</p>
      {error ? <p role="alert">{error}</p> : null}
      <div className="personDetailActions"><button className="primaryButton" type="submit" disabled={pending}>{pending ? "保存中…" : "保存昵称与备注"}</button><button type="button" className="secondaryButton" disabled={pending} onClick={() => { setEditing(false); setError(null) }}>取消</button></div>
    </form> : <>
      <p className="staffProfileNotes">{data.profile_notes ?? "暂无内部备注"}</p>
      <button type="button" className="secondaryButton" onClick={() => { setNickname(data.nickname ?? ""); setNotes(data.profile_notes ?? ""); setOriginal({ nickname: data.nickname, profile_notes: data.profile_notes }); setError(null); setEditing(true) }}>编辑昵称与内部备注</button>
    </>}
  </section>
}
