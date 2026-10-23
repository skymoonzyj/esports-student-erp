import { useEffect, useRef, useState } from "react"
import { useAuth } from "../auth/AuthProvider"
import { AssistedPersonForm } from "./AssistedPersonForm"
import { assistedPeopleApi } from "./api"
import type { AssistedContext } from "./api"
import { useAssistedOperation, useAssistedPeopleContext } from "./useAssistedPeopleContext"

export function AssistedDirectCreatePage() {
  const { user, status } = useAuth()
  if (!user || status !== "authenticated") return <p>请先登录。</p>
  return <DirectAccount key={user.account_id} accountId={user.account_id} />
}

function DirectAccount({ accountId }: { accountId: string }) {
  const query = useAssistedPeopleContext(accountId)
  if (query.isPending) return <p role="status">正在核实管理建档权限…</p>
  if (!query.context?.can_direct_create) return <p role="alert">无管理建档权限。</p>
  return <DirectForm accountId={accountId} context={query.context} />
}

function DirectForm({ accountId, context }: { accountId: string; context: AssistedContext }) {
  const [confirmed, setConfirmed] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [formEpoch, setFormEpoch] = useState(0)
  const [confirmationError, setConfirmationError] = useState(false)
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const operation = useAssistedOperation(accountId)
  useEffect(() => { if (operation.denied) attempt.current = null }, [operation.denied])
  return <main className="peoplePage"><h1>管理建档</h1>
    <p>这是显式管理建档：成功后直接形成已通过档案，不是普通待审核申请。不得把本操作当作监护验证、同意或家庭查看授权。</p>
    {operation.message ? <p role="status">{operation.message}</p> : null}
    {completed ? <><p>管理建档成功。</p><button onClick={() => { attempt.current = null; operation.clearMessage(); setCompleted(false); setConfirmed(false); setFormEpoch((value) => value + 1) }}>开始新的管理建档</button></>
      : !operation.denied ? <>
        <label><input type="checkbox" checked={confirmed} disabled={operation.pending} onChange={(event) => { setConfirmed(event.target.checked); setConfirmationError(false) }} />我已核对资料，确认直接创建已通过档案</label>
        {confirmationError ? <p role="alert">请先确认管理建档操作。</p> : null}
        <AssistedPersonForm key={formEpoch} submitting={operation.pending} coachOptions={context.eligible_coaches} teamOptions={context.eligible_teams}
          onSubmit={async (input) => {
            if (!confirmed) { setConfirmationError(true); throw new Error("confirmation_required") }
            const fingerprint = JSON.stringify(input)
            if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, key: crypto.randomUUID() }
            const key = attempt.current.key
            if (await operation.run((signal) => assistedPeopleApi.directCreate(input, key, signal))) {
              attempt.current = null; setCompleted(true)
            } else throw new Error("request_failed")
          }} />
      </> : null}
  </main>
}
