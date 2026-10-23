import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { Link } from "react-router-dom"

import {
  createAdultGuardianInvitation,
  listAdultGuardianInvitations,
} from "./api"
import type {
  GuardianInvitation,
  RegistrationIdentifierKind,
} from "./api"
import "./registration.css"

export function AdultGuardianInvitationsPage() {
  const [invitations, setInvitations] = useState<GuardianInvitation[]>([])
  const [loading, setLoading] = useState(true)
  const [available, setAvailable] = useState(true)
  const [identifierKind, setIdentifierKind] = useState<RegistrationIdentifierKind>("phone")
  const [identifier, setIdentifier] = useState("")
  const [relationship, setRelationship] = useState("")
  const [isPrimary, setIsPrimary] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    void listAdultGuardianInvitations()
      .then((items) => {
        if (!active) return
        setInvitations(items)
        setAvailable(true)
        setError(null)
      })
      .catch(() => {
        if (!active) return
        setInvitations([])
        setAvailable(false)
        setError("当前账号无法管理成年学员监护邀请")
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const target = identifier.trim()
    const relation = relationship.trim()
    if (!target || relation.length < 2) {
      setSuccess(null)
      setError("请填写监护人联系方式和关系")
      return
    }
    setIdentifier("")
    setSubmitting(true)
    setError(null)
    setSuccess(null)
    try {
      const created = await createAdultGuardianInvitation({
        identifier_kind: identifierKind,
        identifier: target,
        relationship: relation,
        is_primary: isPrimary,
      })
      setInvitations((current) => [...current, created])
      setRelationship("")
      setIsPrimary(false)
      setSuccess(`邀请已发送至 ${created.masked_identifier}`)
    } catch {
      setError("暂时无法发送邀请，请检查信息后重试")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="portalPage">
      <header className="portalPageHeader">
        <div>
          <p className="pageEyebrow">GUARDIAN INVITATIONS</p>
          <h1>成年学员监护邀请</h1>
          <p>成年学员可自愿邀请监护人，不影响本人账号使用。</p>
        </div>
        <Link className="secondaryButton" to="/">返回工作台</Link>
      </header>

      {loading ? <p role="status">正在读取邀请…</p> : null}
      {success ? (
        <p role="status" aria-live="polite">{success}</p>
      ) : null}
      {error ? <p className="registrationError" role="alert">{error}</p> : null}

      {available && !loading ? (
        <section className="registrationCard registrationCardWide" aria-label="监护邀请管理">
          <form className="registrationForm" onSubmit={submit}>
            <div className="registrationFieldGroup">
              <label htmlFor="adult-guardian-kind">邀请方式</label>
              <select
                id="adult-guardian-kind"
                value={identifierKind}
                onChange={(event) => {
                  setIdentifierKind(event.target.value as RegistrationIdentifierKind)
                  setIdentifier("")
                }}
              >
                <option value="phone">手机号</option>
                <option value="email">邮箱</option>
              </select>
            </div>
            <div className="registrationFieldGroup">
              <label htmlFor="adult-guardian-identifier">监护人手机号或邮箱</label>
              <input
                id="adult-guardian-identifier"
                type={identifierKind === "email" ? "email" : "tel"}
                value={identifier}
                onChange={(event) => setIdentifier(event.target.value)}
                required
              />
            </div>
            <div className="registrationFieldGroup">
              <label htmlFor="adult-guardian-relationship">与学员关系</label>
              <input
                id="adult-guardian-relationship"
                value={relationship}
                onChange={(event) => setRelationship(event.target.value)}
                minLength={2}
                maxLength={40}
                required
              />
            </div>
            <label className="registrationConsent">
              <input
                type="checkbox"
                checked={isPrimary}
                onChange={(event) => setIsPrimary(event.target.checked)}
              />
              <span>设为主要监护人</span>
            </label>
            <button
              className="primaryButton registrationSubmit"
              type="submit"
              disabled={submitting}
            >
              {submitting ? "正在发送…" : "发送监护人邀请"}
            </button>
          </form>

          {invitations.length === 0 ? <p>尚未发送邀请。</p> : (
            <ul className="registrationAdminList" aria-label="已发送邀请">
              {invitations.map((invitation) => (
                <li key={invitation.id}>
                  <article>
                    <strong>{invitation.masked_identifier}</strong>
                    <span>{invitation.relationship}</span>
                    <span>{invitation.status === "pending" ? "等待确认" : "已处理"}</span>
                  </article>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}
    </div>
  )
}
