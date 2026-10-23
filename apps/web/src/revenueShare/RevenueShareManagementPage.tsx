import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Copy, Plus, QrCode, RefreshCw, ShieldOff } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import {
  createReferralCode,
  createRevenueShareOrganization,
  createRevenueShareRule,
  disableRevenueShareRule,
  getRevenueShareConfiguration,
  reconcileRevenueShare,
} from "./api"
import type {
  BeneficiaryKind,
  CalculationType,
  ReferralCode,
  RevenueShareRole,
} from "./api"
import { displayDate, formatCny, revenueShareRoleLabels, todayInChina } from "./format"
import { ReferralQrDialog } from "./ReferralQrDialog"
import { OrganizationManagersPanel } from "./OrganizationManagersPanel"
import "./revenue-share.css"

const configurationKey = ["revenue-share", "configuration"] as const

type ReconcileProgress = {
  cursor: string | null
  processed: number
  allocated: number
  pending: number
  status: "idle" | "running" | "stopping" | "paused" | "failed" | "complete"
}

function newReconcileProgress(): ReconcileProgress {
  return { cursor: null, processed: 0, allocated: 0, pending: 0, status: "idle" }
}

function message(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

export function RevenueShareManagementPage() {
  const auth = useOptionalAuth()
  const canManageManagers = auth?.user?.is_super_admin === true && auth.user.permissions.includes("revenue_share.manage")
  const queryClient = useQueryClient()
  const configuration = useQuery({
    queryKey: configurationKey,
    queryFn: getRevenueShareConfiguration,
  })
  const [organizationName, setOrganizationName] = useState("")
  const [sourceKind, setSourceKind] = useState<BeneficiaryKind>("person")
  const [sourceId, setSourceId] = useState("")
  const [codeLabel, setCodeLabel] = useState("")
  const [code, setCode] = useState("")
  const [projectId, setProjectId] = useState("")
  const [roleKind, setRoleKind] = useState<RevenueShareRole>("inviter")
  const [recipientKind, setRecipientKind] = useState<BeneficiaryKind>("person")
  const [recipientId, setRecipientId] = useState("")
  const [ruleLabel, setRuleLabel] = useState("")
  const [calculationType, setCalculationType] = useState<CalculationType>("percentage")
  const [value, setValue] = useState("")
  const [effectiveFrom, setEffectiveFrom] = useState(todayInChina)
  const [effectiveTo, setEffectiveTo] = useState("")
  const [pendingAction, setPendingAction] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [qrCode, setQrCode] = useState<ReferralCode | null>(null)
  const [reconcileProgress, setReconcileProgress] = useState(newReconcileProgress)
  const progressRef = useRef(reconcileProgress)
  const reconcileBusy = useRef(false)
  const stopRequested = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      stopRequested.current = true
    }
  }, [])

  function updateProgress(progress: ReconcileProgress): void {
    progressRef.current = progress
    if (mounted.current) setReconcileProgress(progress)
  }

  async function refresh(): Promise<void> {
    await queryClient.invalidateQueries({ queryKey: configurationKey })
  }

  async function addOrganization(event: FormEvent): Promise<void> {
    event.preventDefault()
    setPendingAction("organization")
    setError(null)
    setNotice(null)
    try {
      await createRevenueShareOrganization(organizationName.trim())
      setOrganizationName("")
      await refresh()
      setNotice("邀请机构已添加")
    } catch (caught) {
      setError(message(caught, "邀请机构无法保存"))
    } finally {
      setPendingAction(null)
    }
  }

  async function addCode(event: FormEvent): Promise<void> {
    event.preventDefault()
    if (!sourceId) {
      setError("请选择邀请人或邀请机构")
      return
    }
    setPendingAction("code")
    setError(null)
    setNotice(null)
    try {
      const created = await createReferralCode({
        ...(code.trim() ? { code: code.trim().toUpperCase() } : {}),
        label: codeLabel.trim(),
        source_kind: sourceKind,
        referrer_person_id: sourceKind === "person" ? sourceId : null,
        referrer_organization_id: sourceKind === "organization" ? sourceId : null,
      })
      setCode("")
      setCodeLabel("")
      setSourceId("")
      await refresh()
      setNotice("邀请码已生成")
      setQrCode(created)
    } catch (caught) {
      setError(message(caught, "邀请码无法生成"))
    } finally {
      setPendingAction(null)
    }
  }

  async function addRule(event: FormEvent): Promise<void> {
    event.preventDefault()
    const explicitRecipient = ["platform_developer", "additional"].includes(roleKind)
    if (explicitRecipient && !recipientId) {
      setError("请选择分成收款人")
      return
    }
    setPendingAction("rule")
    setError(null)
    setNotice(null)
    try {
      await createRevenueShareRule({
        project_id: projectId || null,
        role_kind: roleKind,
        recipient_person_id:
          explicitRecipient && recipientKind === "person" ? recipientId : null,
        recipient_organization_id:
          explicitRecipient && recipientKind === "organization" ? recipientId : null,
        label: ruleLabel.trim() || null,
        calculation_type: calculationType,
        percentage: calculationType === "percentage" ? value.trim() : null,
        fixed_amount: calculationType === "fixed" ? value.trim() : null,
        effective_from: effectiveFrom,
        effective_to: effectiveTo || null,
      })
      setRuleLabel("")
      setValue("")
      setRecipientId("")
      await refresh()
      setNotice("分成规则已生效")
    } catch (caught) {
      setError(message(caught, "分成规则无法保存"))
    } finally {
      setPendingAction(null)
    }
  }

  async function disableRule(ruleId: string): Promise<void> {
    if (!window.confirm("确认停用这条规则？历史付款快照不会改变。")) return
    setPendingAction(ruleId)
    setError(null)
    setNotice(null)
    try {
      await disableRevenueShareRule(ruleId)
      await refresh()
      setNotice("规则已停用")
    } catch (caught) {
      setError(message(caught, "规则无法停用"))
    } finally {
      setPendingAction(null)
    }
  }

  async function reconcile(): Promise<void> {
    if (reconcileBusy.current || pendingAction !== null) return
    reconcileBusy.current = true
    stopRequested.current = false
    const previous = progressRef.current.status === "complete"
      ? newReconcileProgress() : progressRef.current
    updateProgress({ ...previous, status: "running" })
    setPendingAction("reconcile")
    setError(null)
    setNotice(null)
    try {
      while (mounted.current && !stopRequested.current) {
        const current = progressRef.current
        const result = await reconcileRevenueShare({ batch_size: 50, cursor: current.cursor })
        // An in-flight batch remains committed even when the user leaves this page.
        void queryClient.invalidateQueries({ queryKey: ["revenue-share", "finance"], refetchType: "none" })
        void queryClient.invalidateQueries({ queryKey: ["revenue-share", "me"], refetchType: "none" })
        if (!mounted.current) return
        if (result.has_more && (!result.next_cursor || result.next_cursor === current.cursor)) {
          throw new Error("invalid_reconcile_cursor")
        }
        updateProgress({
          cursor: result.next_cursor,
          processed: current.processed + result.processed,
          allocated: current.allocated + result.allocated,
          pending: current.pending + result.pending,
          status: result.has_more ? (stopRequested.current ? "paused" : "running") : "complete",
        })
        if (!result.has_more) break
      }
    } catch (caught) {
      if (mounted.current) {
        updateProgress({ ...progressRef.current, status: "failed" })
        setError(message(caught, "本批处理未能确认完成，已完成的进度已保留，请点击重试继续"))
      }
    } finally {
      reconcileBusy.current = false
      if (mounted.current) setPendingAction(null)
    }
  }

  function stopReconcile(): void {
    stopRequested.current = true
    updateProgress({ ...progressRef.current, status: "stopping" })
  }

  async function copyRegistrationLink(item: ReferralCode): Promise<void> {
    setError(null)
    try {
      await navigator.clipboard.writeText(
        new URL(item.registration_url, window.location.origin).toString(),
      )
      setNotice("注册链接已复制")
    } catch {
      setError("注册链接复制失败，请打开二维码后长按链接复制")
    }
  }

  const data = configuration.data
  const explicitRecipient = ["platform_developer", "additional"].includes(roleKind)
  const referralSources = sourceKind === "person" ? data?.people : data?.organizations
  const recipientSources = recipientKind === "person" ? data?.people : data?.organizations

  return (
    <main className="revenueSharePage">
      <header className="revenueShareHeader">
        <div>
          <p className="pageEyebrow">REVENUE SHARE</p>
          <h1>分成管理</h1>
          <p>邀请归属与付款分成规则</p>
        </div>
        <button className="secondaryButton" type="button" onClick={() => void reconcile()} disabled={pendingAction !== null}>
          <RefreshCw aria-hidden="true" />
          {pendingAction === "reconcile" ? "正在增量重算" : reconcileProgress.status === "failed" ? "重试增量重算" : reconcileProgress.status === "paused" ? "继续增量重算" : "处理待重算付款"}
        </button>
      </header>

      {configuration.isLoading ? <p className="revenueShareStatus">正在读取分成配置…</p> : null}
      {configuration.isError ? <p className="formError">分成配置暂时无法读取</p> : null}
      {error ? <p className="formError" role="alert">{error}</p> : null}
      {notice ? <p className="revenueShareNotice" role="status">{notice}</p> : null}
      {reconcileProgress.status !== "idle" ? (
        <section className="revenueShareReconcileProgress" aria-label="增量重算进度">
          <div role="status" aria-live="polite">
            <strong>{reconcileProgress.status === "complete" ? "本轮处理完成" : reconcileProgress.status === "paused" ? "已暂停后续批次" : reconcileProgress.status === "stopping" ? "当前批次完成后停止" : reconcileProgress.status === "failed" ? "处理已中断，可继续重试" : "正在处理待重算付款"}</strong>
            <p>累计已处理 {reconcileProgress.processed} 笔，成功分配 {reconcileProgress.allocated} 笔，仍待分配 {reconcileProgress.pending} 笔</p>
            <small>仅处理待重算付款，已记账的历史分成不会因规则调整被改写。</small>
          </div>
          {pendingAction === "reconcile" ? <button className="secondaryButton" type="button" onClick={stopReconcile} disabled={reconcileProgress.status === "stopping"}>{reconcileProgress.status === "stopping" ? "正在停止" : "停止后续批次"}</button> : null}
        </section>
      ) : null}

      {data ? (
        <>
          <section className="revenueShareSection" aria-labelledby="referral-title">
            <div className="revenueShareSectionHeading">
              <div><p className="pageEyebrow">REFERRALS</p><h2 id="referral-title">邀请归属</h2></div>
              <span>{data.referral_codes.length} 个邀请码</span>
            </div>
            <div className="revenueShareFormColumns">
              <form className="revenueShareForm" onSubmit={(event) => void addOrganization(event)}>
                <h3>新增邀请机构</h3>
                <label><span>机构名称</span><input value={organizationName} onChange={(event) => setOrganizationName(event.target.value)} maxLength={120} required /></label>
                <button className="secondaryButton" type="submit" disabled={pendingAction !== null}><Plus aria-hidden="true" />添加机构</button>
              </form>
              <form className="revenueShareForm" onSubmit={(event) => void addCode(event)}>
                <h3>生成邀请码</h3>
                <div className="revenueShareSegmented">
                  <label><input type="radio" name="source-kind" checked={sourceKind === "person"} onChange={() => { setSourceKind("person"); setSourceId("") }} />邀请人</label>
                  <label><input type="radio" name="source-kind" checked={sourceKind === "organization"} onChange={() => { setSourceKind("organization"); setSourceId("") }} />邀请机构</label>
                </div>
                <label><span>{sourceKind === "person" ? "邀请人" : "邀请机构"}</span><select value={sourceId} onChange={(event) => setSourceId(event.target.value)} required><option value="">请选择</option>{referralSources?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
                <label><span>显示名称</span><input value={codeLabel} onChange={(event) => setCodeLabel(event.target.value)} maxLength={120} required /></label>
                <label><span>指定代码（选填）</span><input value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} maxLength={24} placeholder="留空自动生成" /></label>
                <button className="primaryButton" type="submit" disabled={pendingAction !== null}><Plus aria-hidden="true" />生成邀请码</button>
              </form>
            </div>
            <OrganizationManagersPanel accountId={auth?.user?.account_id ?? ""} organizations={data.organizations} canManageManagers={canManageManagers} />
            <div className="revenueShareTableWrap">
              <table className="revenueShareTable"><thead><tr><th>邀请码</th><th>归属</th><th>名称</th><th>状态</th><th>注册链接</th></tr></thead><tbody>{data.referral_codes.map((item) => <tr key={item.id}><td><code>{item.code}</code></td><td>{item.source_name}</td><td>{item.label}</td><td>{item.is_active ? "使用中" : "已停用"}</td><td><div className="revenueShareTableActions"><button className="iconTextButton" type="button" disabled={!item.is_active} onClick={() => setQrCode(item)}><QrCode aria-hidden="true" />二维码</button><button className="iconTextButton" type="button" disabled={!item.is_active} onClick={() => void copyRegistrationLink(item)}><Copy aria-hidden="true" />复制</button></div></td></tr>)}{data.referral_codes.length === 0 ? <tr><td colSpan={5}>暂无邀请码</td></tr> : null}</tbody></table>
            </div>
          </section>

          <section className="revenueShareSection" aria-labelledby="rules-title">
            <div className="revenueShareSectionHeading"><div><p className="pageEyebrow">RULES</p><h2 id="rules-title">分成规则</h2></div><span>{data.rules.filter((item) => item.is_active).length} 条生效中</span></div>
            <form className="revenueShareRuleForm" onSubmit={(event) => void addRule(event)}>
              <label><span>适用项目</span><select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">全部项目</option>{data.projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
              <label><span>分成角色</span><select value={roleKind} onChange={(event) => { setRoleKind(event.target.value as RevenueShareRole); setRecipientId("") }}>{Object.entries(revenueShareRoleLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
              {explicitRecipient ? <><label><span>收款对象类型</span><select value={recipientKind} onChange={(event) => { setRecipientKind(event.target.value as BeneficiaryKind); setRecipientId("") }}><option value="person">个人</option><option value="organization">机构</option></select></label><label><span>收款对象</span><select value={recipientId} onChange={(event) => setRecipientId(event.target.value)} required><option value="">请选择</option>{recipientSources?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></> : null}
              <label><span>规则备注（选填）</span><input value={ruleLabel} onChange={(event) => setRuleLabel(event.target.value)} maxLength={120} /></label>
              <label><span>计算方式</span><select value={calculationType} onChange={(event) => { setCalculationType(event.target.value as CalculationType); setValue("") }}><option value="percentage">按付款比例</option><option value="fixed">按整期固定金额</option></select></label>
              <label><span>{calculationType === "percentage" ? "比例（%）" : "固定金额（元）"}</span><input inputMode="decimal" value={value} onChange={(event) => setValue(event.target.value)} placeholder={calculationType === "percentage" ? "例如 10" : "例如 1500"} required /></label>
              <label><span>生效日期</span><input type="date" value={effectiveFrom} onChange={(event) => setEffectiveFrom(event.target.value)} required /></label>
              <label><span>结束日期（选填）</span><input type="date" value={effectiveTo} onChange={(event) => setEffectiveTo(event.target.value)} min={effectiveFrom} /></label>
              <button className="primaryButton" type="submit" disabled={pendingAction !== null}><Plus aria-hidden="true" />新增规则</button>
            </form>
            <div className="revenueShareTableWrap">
              <table className="revenueShareTable revenueShareRulesTable"><thead><tr><th>项目</th><th>分成角色</th><th>收款对象</th><th>规则</th><th>有效期</th><th>状态</th><th>操作</th></tr></thead><tbody>{data.rules.map((rule) => <tr key={rule.id}><td>{rule.project_name ?? "全部项目"}</td><td>{revenueShareRoleLabels[rule.role_kind]}</td><td>{rule.recipient_name ?? "按项目自动匹配"}</td><td>{rule.calculation_type === "percentage" ? `${(rule.percentage_basis_points ?? 0) / 100}%` : formatCny(rule.fixed_amount_cents ?? 0)}</td><td>{displayDate(rule.effective_from)} 至 {rule.effective_to ? displayDate(rule.effective_to) : "长期"}</td><td>{rule.is_active ? "生效中" : "已停用"}</td><td>{rule.is_active ? <button className="dangerTextButton" type="button" disabled={pendingAction !== null} onClick={() => void disableRule(rule.id)}><ShieldOff aria-hidden="true" />停用</button> : "-"}</td></tr>)}{data.rules.length === 0 ? <tr><td colSpan={7}>暂无分成规则</td></tr> : null}</tbody></table>
            </div>
          </section>
        </>
      ) : null}
      {qrCode ? <ReferralQrDialog referralCode={qrCode} onClose={() => setQrCode(null)} /> : null}
    </main>
  )
}
