import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { PasswordInput } from "./PasswordInput"
import {
  clearAccountIdentifierChallengeDraft,
  readAccountIdentifierChallengeDraft,
  writeAccountIdentifierChallengeDraft,
} from "./accountIdentifierChallengeStorage"
import {
  issueAccountIdentifierChallenge,
  listAccountIdentifiers,
  verifyAccountIdentifier,
} from "../registration/api"
import type {
  AccountIdentifiersResponse,
  RegistrationChallenge,
  RegistrationIdentifierKind,
} from "../registration/api"
import { registrationKeys } from "../registration/keys"
import "../registration/registration.css"

type AccountIdentifiersPanelProps = {
  accountId: string
}

function isPrivateFailure(error: unknown): error is ProblemDetailsError {
  return (
    error instanceof ProblemDetailsError &&
    (error.problem.status === 401 ||
      error.problem.status === 403 ||
      error.problem.status === 404 ||
      error.problem.code === "registration_action_unavailable")
  )
}

function identifierError(error: unknown): string {
  if (error instanceof ProblemDetailsError) {
    if (error.problem.status === 429) return "验证码请求过于频繁，请稍后重试"
    if (error.problem.code === "verification_invalid_or_expired") {
      return "验证码无效或已过期"
    }
    if (error.problem.code === "invalid_credentials") return "当前密码错误"
  }
  return "登录方式操作失败，请检查后重试"
}

function isExpiredVerification(error: unknown): boolean {
  return (
    error instanceof ProblemDetailsError &&
    error.problem.code === "verification_invalid_or_expired"
  )
}

export function AccountIdentifiersPanel({ accountId }: AccountIdentifiersPanelProps) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => registrationKeys.accountIdentifiers(accountId), [accountId])
  const [initialDraft] = useState(() => readAccountIdentifierChallengeDraft(accountId))
  const previousAccountIdRef = useRef(accountId)
  const [accessUnavailable, setAccessUnavailable] = useState(false)
  const [identifierKind, setIdentifierKind] = useState<RegistrationIdentifierKind | null>(
    initialDraft?.identifierKind ?? null,
  )
  const [identifier, setIdentifier] = useState(initialDraft?.identifier ?? "")
  const [challenge, setChallenge] = useState<RegistrationChallenge | null>(
    initialDraft?.challenge ?? null,
  )
  const [code, setCode] = useState("")
  const [currentPassword, setCurrentPassword] = useState("")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const identifiers = useQuery({
    queryKey,
    queryFn: listAccountIdentifiers,
    retry: false,
    enabled: !accessUnavailable,
  })

  const closeForm = useCallback(() => {
    clearAccountIdentifierChallengeDraft(accountId)
    setIdentifierKind(null)
    setIdentifier("")
    setChallenge(null)
    setCode("")
    setCurrentPassword("")
  }, [accountId])

  const purgePrivateState = useCallback(() => {
    setAccessUnavailable(true)
    closeForm()
    setError("登录方式暂时不可用，请重新登录后再试")
    const cancellation = queryClient.cancelQueries({ queryKey, exact: true })
    queryClient.removeQueries({ queryKey, exact: true })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey, exact: true }))
  }, [closeForm, queryClient, queryKey])

  useEffect(() => {
    if (isPrivateFailure(identifiers.error)) purgePrivateState()
  }, [identifiers.error, purgePrivateState])

  useEffect(() => {
    if (previousAccountIdRef.current === accountId) return
    previousAccountIdRef.current = accountId
    const restored = readAccountIdentifierChallengeDraft(accountId)
    setAccessUnavailable(false)
    setIdentifierKind(restored?.identifierKind ?? null)
    setIdentifier(restored?.identifier ?? "")
    setChallenge(restored?.challenge ?? null)
    setCode("")
    setCurrentPassword("")
    setPending(false)
    setError(null)
  }, [accountId])

  function openForm(kind: RegistrationIdentifierKind): void {
    clearAccountIdentifierChallengeDraft(accountId)
    setIdentifierKind(kind)
    setIdentifier("")
    setChallenge(null)
    setCode("")
    setCurrentPassword("")
    setError(null)
  }

  async function issueChallenge(): Promise<void> {
    if (!identifierKind || !identifier.trim() || !currentPassword) return
    const normalizedIdentifier = identifier.trim()
    setPending(true)
    setError(null)
    try {
      const result = await issueAccountIdentifierChallenge({
        identifier_kind: identifierKind,
        identifier: normalizedIdentifier,
        current_password: currentPassword,
      })
      writeAccountIdentifierChallengeDraft(accountId, {
        identifierKind,
        identifier: normalizedIdentifier,
        challenge: result,
      })
      setIdentifier(normalizedIdentifier)
      setChallenge(result)
      setCode("")
    } catch (caught) {
      if (isPrivateFailure(caught)) purgePrivateState()
      else setError(identifierError(caught))
    } finally {
      setPending(false)
    }
  }

  async function verify(): Promise<void> {
    if (!identifierKind || !challenge || !/^\d{6}$/.test(code) || !currentPassword) {
      setError("请输入 6 位数字验证码和当前密码")
      return
    }
    setPending(true)
    setError(null)
    try {
      const result = await verifyAccountIdentifier({
        challenge_id: challenge.challenge_id,
        code,
        identifier_kind: identifierKind,
        identifier: identifier.trim(),
        current_password: currentPassword,
      })
      queryClient.setQueryData<AccountIdentifiersResponse>(queryKey, result)
      closeForm()
    } catch (caught) {
      if (isPrivateFailure(caught)) purgePrivateState()
      else {
        if (isExpiredVerification(caught)) {
          clearAccountIdentifierChallengeDraft(accountId)
          setChallenge(null)
          setCode("")
        }
        setError(identifierError(caught))
      }
    } finally {
      setPending(false)
    }
  }

  const kinds = new Set(identifiers.data?.items.map((item) => item.kind) ?? [])

  return (
    <section className="accountIdentifiersPanel" aria-labelledby="account-identifiers-title">
      <div className="accountIdentifiersHeader">
        <div>
          <p className="registrationEyebrow">ACCOUNT SECURITY</p>
          <h2 id="account-identifiers-title">登录方式</h2>
          <p>使用已验证的手机号或邮箱登录，不显示完整联系方式。</p>
        </div>
        {!accessUnavailable ? (
          <div className="accountIdentifierActions">
            {!kinds.has("phone") ? <button type="button" onClick={() => openForm("phone")}>添加手机号</button> : null}
            {!kinds.has("email") ? <button type="button" onClick={() => openForm("email")}>添加邮箱</button> : null}
          </div>
        ) : null}
      </div>

      {accessUnavailable ? <p className="registrationError" role="alert">{error}</p> : null}
      {!accessUnavailable && identifiers.isPending ? <p role="status">正在读取登录方式…</p> : null}
      {!accessUnavailable && identifiers.error && !isPrivateFailure(identifiers.error) ? (
        <p className="registrationError" role="alert">登录方式读取失败，请稍后重试</p>
      ) : null}
      {!accessUnavailable && identifiers.data ? (
        <ul className="accountIdentifierList">
          {identifiers.data.items.map((item) => (
            <li key={item.kind}>
              <span>{item.kind === "phone" ? "手机号" : "邮箱"}</span>
              <strong>{item.masked_identifier} · 已验证</strong>
            </li>
          ))}
        </ul>
      ) : null}

      {!accessUnavailable && identifierKind ? (
        <div className="accountIdentifierForm" aria-label={identifierKind === "phone" ? "添加手机号" : "添加邮箱"}>
          {!challenge ? (
            <div className="registrationFieldGroup">
              <label htmlFor="account-new-identifier">
                {identifierKind === "phone" ? "新手机号" : "新邮箱"}
              </label>
              <input
                id="account-new-identifier"
                type={identifierKind === "phone" ? "tel" : "email"}
                autoComplete={identifierKind === "phone" ? "tel" : "email"}
                value={identifier}
                onChange={(event) => setIdentifier(event.target.value)}
              />
            </div>
          ) : null}
          <div className="registrationFieldGroup">
            <label htmlFor="account-identifier-current-password">当前密码</label>
            <PasswordInput
              id="account-identifier-current-password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
          </div>
          {challenge ? (
            <>
              <p className="registrationMaskedValue accountIdentifierMasked">{challenge.masked_destination}</p>
              <div className="registrationFieldGroup">
                <label htmlFor="account-identifier-code">验证码</label>
                <input
                  id="account-identifier-code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                />
              </div>
            </>
          ) : null}
          {error ? <p className="registrationError" role="alert">{error}</p> : null}
          <div className="accountIdentifierFormActions">
            <button type="button" onClick={closeForm} disabled={pending}>取消</button>
            {challenge ? (
              <button className="primaryButton" type="button" onClick={() => void verify()} disabled={pending || !currentPassword}>
                {pending ? "正在绑定…" : "确认绑定"}
              </button>
            ) : (
              <button className="primaryButton" type="button" onClick={() => void issueChallenge()} disabled={pending || !identifier.trim() || !currentPassword}>
                {pending ? "正在发送…" : "发送验证码"}
              </button>
            )}
          </div>
        </div>
      ) : null}
    </section>
  )
}
