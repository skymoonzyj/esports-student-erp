import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import type { FormEvent } from "react"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import {
  createAccount,
  deleteAccount,
  disableAccount,
  enableAccount,
  listAccountCandidates,
  listAccounts,
  resetAccountPassword,
} from "./api"
import type { Account, AccountStatus } from "./api"
import "./settings.css"

type TemporaryPasswordResult = {
  accountName: string
  password: string
}

const GUARDIAN_ACCOUNT_MESSAGE = "请先绑定已成年、已实名且账号启用的监护人"

function hasGuardianError(error: ProblemDetailsError): boolean {
  return error.problem.errors?.some((item) => item.field === "guardian") === true
}

function verifiedIdentifiers(account: Account): string[] {
  const values: string[] = []
  if (account.masked_phone) values.push(account.masked_phone)
  if (account.masked_email) values.push(account.masked_email)
  return values
}

function identifierDetails(account: Account): string {
  const verifiedKinds = account.verified_identifier_kinds.map((kind) =>
    kind === "phone" ? "手机号" : "邮箱",
  )
  return `均可登录：${verifiedKinds.join("、")}`
}

export function OneTimePasswordDialog({
  result,
  onClose,
}: {
  result: TemporaryPasswordResult
  onClose: () => void
}) {
  return (
    <div className="dialogBackdrop">
      <section
        className="settingsDialog oneTimePasswordDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="temporary-password-title"
      >
        <p className="settingsEyebrow">ONE-TIME SECRET</p>
        <h2 id="temporary-password-title">请立即安全交付临时密码</h2>
        <p>
          {result.accountName} 的临时密码只显示这一次。关闭后系统和前端都不能再次查看。
        </p>
        <output className="temporaryPasswordValue">{result.password}</output>
        <button className="primaryButton" type="button" onClick={onClose}>
          我已保存并关闭
        </button>
      </section>
    </div>
  )
}

export function AccountDeletionDialog({
  account,
  pending,
  onClose,
  onConfirm,
}: {
  account: Account
  pending: boolean
  onClose: () => void
  onConfirm: () => void | Promise<void>
}) {
  const [step, setStep] = useState<1 | 2>(1)
  const finalConfirmation = step === 2

  return (
    <div className="dialogBackdrop">
      <section
        className="settingsDialog accountDeletionDialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="account-deletion-title"
        aria-describedby="account-deletion-description"
      >
        <p className="settingsEyebrow">ACCOUNT DELETION</p>
        <h2 id="account-deletion-title">
          {finalConfirmation ? "再次确认删除账号" : "确认删除账号"}
        </h2>
        <p id="account-deletion-description">
          删除后会立即撤销登录、从账号管理列表移除，人员档案和已有业务历史会保留。
          {finalConfirmation ? "此操作不可通过页面恢复，请确认继续。" : "请先确认你要删除的是以下账号。"}
        </p>
        <div className="accountDeletionTarget">
          <strong>{account.full_name}</strong>
          <small>{account.masked_primary_identifier}</small>
        </div>
        <div className="dialogActions">
          <button
            className="secondaryButton"
            type="button"
            onClick={finalConfirmation ? () => setStep(1) : onClose}
            disabled={pending}
          >
            {finalConfirmation ? "返回上一步" : "取消"}
          </button>
          {finalConfirmation ? (
            <button
              className="dangerButton"
              type="button"
              onClick={() => void onConfirm()}
              disabled={pending}
            >
              {pending ? "正在删除…" : "确认删除"}
            </button>
          ) : (
            <button
              className="dangerButton"
              type="button"
              onClick={() => setStep(2)}
            >
              继续删除
            </button>
          )}
        </div>
      </section>
    </div>
  )
}

export function AccountOpeningDialog({
  fixedPersonId,
  fixedPersonName,
  onClose,
  onCreated,
}: {
  fixedPersonId?: string
  fixedPersonName?: string
  onClose: () => void
  onCreated?: () => void | Promise<void>
}) {
  const [name, setName] = useState("")
  const [searchedName, setSearchedName] = useState("")
  const [personId, setPersonId] = useState(fixedPersonId ?? "")
  const [personName, setPersonName] = useState(fixedPersonName ?? "")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [guardianError, setGuardianError] = useState(false)
  const [result, setResult] = useState<TemporaryPasswordResult | null>(null)
  const candidates = useQuery({
    queryKey: ["account-candidates", searchedName],
    queryFn: () => listAccountCandidates(searchedName),
    enabled: !fixedPersonId && searchedName.length > 0,
  })

  function search(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setSearchedName(name.trim())
    setPersonId("")
    setPersonName("")
    setGuardianError(false)
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!personId || pending) {
      setError("请选择一名待开户人员")
      return
    }
    setPending(true)
    setError(null)
    setGuardianError(false)
    try {
      const created = await createAccount(personId)
      setResult({
        accountName: created.account.full_name || personName,
        password: created.temporary_password,
      })
      await onCreated?.()
    } catch (caught) {
      if (caught instanceof ProblemDetailsError && hasGuardianError(caught)) {
        setError(GUARDIAN_ACCOUNT_MESSAGE)
        setGuardianError(true)
      } else {
        setError(
          caught instanceof ProblemDetailsError ? caught.problem.detail : "暂时无法开通账号",
        )
      }
    } finally {
      setPending(false)
    }
  }

  if (result) {
    return (
      <OneTimePasswordDialog
        result={result}
        onClose={() => {
          setResult(null)
          onClose()
        }}
      />
    )
  }

  return (
    <div className="dialogBackdrop">
      <section
        className="settingsDialog accountOpeningDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="account-opening-title"
      >
        <p className="settingsEyebrow">ACCOUNT OPENING</p>
        <h2 id="account-opening-title">开通登录账号</h2>
        <p>系统会使用人员档案中已核验的手机号和邮箱，不在页面暴露完整手机号。</p>

        {fixedPersonId ? (
          <div className="fixedAccountPerson">
            <span>开户人员</span>
            <strong>{fixedPersonName}</strong>
          </div>
        ) : (
          <>
            <form className="settingsSearch" onSubmit={search}>
              <label htmlFor="account-person-search">查找人员</label>
              <div>
                <input
                  id="account-person-search"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  minLength={2}
                  required
                />
                <button className="secondaryButton" type="submit">
                  查找
                </button>
              </div>
            </form>
            {candidates.data ? (
              <div className="settingsCandidates">
                {candidates.data.map((candidate) => (
                  <label key={candidate.id}>
                    <input
                      type="radio"
                      name="account-person"
                      value={candidate.id}
                      checked={personId === candidate.id}
                      onChange={() => {
                        setPersonId(candidate.id)
                        setPersonName(candidate.full_name)
                        setGuardianError(false)
                      }}
                      aria-label={`${candidate.full_name} ${candidate.phone ?? "未填写手机号"} · 档案 ${candidate.id}`}
                    />
                    <span>
                      <strong>{candidate.full_name}</strong>
                      <small>
                        {candidate.phone ?? "未填写手机号"} · 档案 {candidate.id}
                      </small>
                    </span>
                  </label>
                ))}
                {candidates.data.length === 0 ? <p>没有找到待开户人员。</p> : null}
              </div>
            ) : null}
          </>
        )}

        <form onSubmit={(event) => void submit(event)}>
          {error ? (
            <div className="formSubmitError accountGuardianError" role="alert">
              <span>{error}</span>
              {guardianError && personId ? (
                <Link to={`/people/${personId}`}>返回人员档案</Link>
              ) : null}
            </div>
          ) : null}
          <div className="dialogActions">
            <button className="secondaryButton" type="button" onClick={onClose}>
              取消
            </button>
            <button className="primaryButton" type="submit" disabled={pending}>
              {pending ? "正在开通…" : "确认开通"}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

export function AccountsPage({ canManage = false }: { canManage?: boolean }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<AccountStatus | "">("")
  const [opening, setOpening] = useState(false)
  const [busyAccountId, setBusyAccountId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [errorPersonId, setErrorPersonId] = useState<string | null>(null)
  const [temporaryResult, setTemporaryResult] = useState<TemporaryPasswordResult | null>(null)
  const [deletingAccount, setDeletingAccount] = useState<Account | null>(null)
  const accounts = useQuery({
    queryKey: ["accounts", { status }],
    queryFn: () => listAccounts(status, 1, 20),
  })

  async function refresh(): Promise<void> {
    await queryClient.invalidateQueries({ queryKey: ["accounts"] })
  }

  async function changeStatus(account: Account): Promise<void> {
    setBusyAccountId(account.id)
    setError(null)
    setErrorPersonId(null)
    try {
      if (account.status === "active") await disableAccount(account.id)
      else await enableAccount(account.id)
      await refresh()
    } catch (caught) {
      if (caught instanceof ProblemDetailsError && hasGuardianError(caught)) {
        setError(GUARDIAN_ACCOUNT_MESSAGE)
        setErrorPersonId(account.person_id)
      } else {
        setError(
          caught instanceof ProblemDetailsError ? caught.problem.detail : "账号状态暂时无法修改",
        )
      }
    } finally {
      setBusyAccountId(null)
    }
  }

  async function resetPassword(account: Account): Promise<void> {
    setBusyAccountId(account.id)
    setError(null)
    setErrorPersonId(null)
    try {
      const reset = await resetAccountPassword(account.id)
      setTemporaryResult({ accountName: account.full_name, password: reset.temporary_password })
    } catch (caught) {
      setError(
        caught instanceof ProblemDetailsError ? caught.problem.detail : "暂时无法重置密码",
      )
    } finally {
      setBusyAccountId(null)
    }
  }

  async function removeAccount(account: Account): Promise<void> {
    setBusyAccountId(account.id)
    setError(null)
    setErrorPersonId(null)
    try {
      await deleteAccount(account.id)
      setDeletingAccount(null)
      await refresh()
    } catch (caught) {
      setDeletingAccount(null)
      setError(
        caught instanceof ProblemDetailsError ? caught.problem.detail : "账号删除暂时无法完成",
      )
    } finally {
      setBusyAccountId(null)
    }
  }

  return (
    <div className="settingsPage accountsPage">
      <div className="settingsHeading">
        <div>
          <p className="pageEyebrow">ACCOUNT LIFECYCLE</p>
          <h1>{canManage ? "账号管理" : "账号查看"}</h1>
          <p>
            {canManage
              ? "开通、停用、启用和重置登录账号；临时密码只显示一次。"
              : "查看人员账号、可用登录方式和当前状态。"}
          </p>
        </div>
        {canManage ? (
          <button className="primaryButton" type="button" onClick={() => setOpening(true)}>
            开通账号
          </button>
        ) : null}
      </div>

      <div className="settingsToolbar">
        <label htmlFor="account-status-filter">账号状态</label>
        <select
          id="account-status-filter"
          value={status}
          onChange={(event) => setStatus(event.target.value as AccountStatus | "")}
        >
          <option value="">全部状态</option>
          <option value="active">启用</option>
          <option value="disabled">停用</option>
        </select>
        {accounts.data ? (
          <span className="accountCount" role="status">
            用户数量：<strong>{accounts.data.total}</strong>
          </span>
        ) : null}
      </div>

      {error ? (
        <div className="formSubmitError accountGuardianError" role="alert">
          <span>{error}</span>
          {errorPersonId ? (
            <Link to={`/people/${errorPersonId}`}>返回人员档案</Link>
          ) : null}
        </div>
      ) : null}
      {accounts.isLoading ? <div className="settingsState">正在读取账号…</div> : null}
      {accounts.isError ? <div className="settingsState">暂时无法读取账号列表。</div> : null}
      {accounts.data ? (
        <section className="settingsTable" role="table" aria-label="账号列表">
          <div className="settingsTableHeader" role="row">
            <span role="columnheader">人员</span>
            <span role="columnheader">昵称</span>
            <span role="columnheader">登录信息</span>
            <span role="columnheader">状态</span>
            <span role="columnheader">操作</span>
          </div>
          {accounts.data.items.map((account) => (
            <div className="settingsTableRow" role="row" key={account.id}>
              <div role="cell" data-label="人员">
                <strong>{account.full_name}</strong>
                <small>{account.person_id}</small>
              </div>
              <div className="accountNicknameCell" role="cell" data-label="昵称">
                <strong className={account.nickname ? undefined : "isEmpty"}>
                  {account.nickname ?? "未设置"}
                </strong>
              </div>
              <div role="cell" data-label="登录信息">
                <strong>{verifiedIdentifiers(account).join("、")}</strong>
                <small>{identifierDetails(account)}</small>
              </div>
              <div role="cell" data-label="状态">
                <span className={`statusPill ${account.status}`}>
                  {account.status === "active" ? "已启用" : "已停用"}
                </span>
              </div>
              <div className="settingsRowActions" role="cell" data-label="操作">
                {canManage ? (
                  <>
                    <button
                      type="button"
                      onClick={() => void changeStatus(account)}
                      disabled={busyAccountId === account.id}
                    >
                      {account.status === "active" ? "停用" : "启用"}
                    </button>
                    <button
                      type="button"
                      onClick={() => void resetPassword(account)}
                      disabled={busyAccountId === account.id}
                    >
                      重置密码
                    </button>
                    <button
                      className="dangerButton"
                      type="button"
                      onClick={() => {
                        setError(null)
                        setErrorPersonId(null)
                        setDeletingAccount(account)
                      }}
                      disabled={busyAccountId === account.id}
                    >
                      删除账号
                    </button>
                  </>
                ) : (
                  <span className="statusPill disabled">只读</span>
                )}
              </div>
            </div>
          ))}
        </section>
      ) : null}

      {canManage && opening ? (
        <AccountOpeningDialog
          onClose={() => setOpening(false)}
          onCreated={refresh}
        />
      ) : null}
      {temporaryResult ? (
        <OneTimePasswordDialog
          result={temporaryResult}
          onClose={() => setTemporaryResult(null)}
        />
      ) : null}
      {canManage && deletingAccount ? (
        <AccountDeletionDialog
          account={deletingAccount}
          pending={busyAccountId === deletingAccount.id}
          onClose={() => setDeletingAccount(null)}
          onConfirm={() => removeAccount(deletingAccount)}
        />
      ) : null}
    </div>
  )
}
