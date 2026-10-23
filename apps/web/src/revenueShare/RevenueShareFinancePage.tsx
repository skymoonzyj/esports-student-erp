import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Banknote, Download, X } from "lucide-react"
import { useRef, useState } from "react"
import type { FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import {
  createRevenueShareSettlement,
  exportRevenueShareSummary,
  getRevenueShareFinanceSummary,
} from "./api"
import type { RevenueShareBalance, RevenueShareSettlementInput } from "./api"
import { formatCny, todayInChina } from "./format"
import "./revenue-share.css"

const financeKey = ["revenue-share", "finance"] as const

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function yuanToCents(value: string): number | null {
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(value.trim())
  if (!match) return null
  const cents = BigInt(match[1] ?? "0") * 100n + BigInt((match[2] ?? "").padEnd(2, "0") || "0")
  return cents > 0n && cents <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(cents) : null
}

function FinanceDialog({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return <div className="revenueShareDialogBackdrop" onMouseDown={onClose}><section className="revenueShareDialog" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}><header><h2>{title}</h2><button className="revenueShareIconButton" type="button" title="关闭" aria-label="关闭" onClick={onClose}><X aria-hidden="true" /></button></header>{children}</section></div>
}

type SettlementFormInput = Omit<RevenueShareSettlementInput, "beneficiary_kind" | "beneficiary_id">

function SettlementDialog({ item, pending, error, onClose, onSubmit }: { item: RevenueShareBalance; pending: boolean; error: string | null; onClose: () => void; onSubmit: (input: SettlementFormInput) => Promise<void> }) {
  // Keep the operation across uncertain failures; editing a completed retry must conflict.
  const [idempotencyKey] = useState(() => crypto.randomUUID())
  const [amount, setAmount] = useState(item.outstanding_cents > 0 ? (item.outstanding_cents / 100).toFixed(2) : "")
  const [settledOn, setSettledOn] = useState(todayInChina)
  const [reference, setReference] = useState("")
  const [note, setNote] = useState("")
  const [validation, setValidation] = useState<string | null>(null)
  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault()
    const amountCents = yuanToCents(amount)
    if (amountCents === null || amountCents > item.outstanding_cents) {
      setValidation("请输入不超过当前未结算余额的正数金额")
      return
    }
    if (!reference.trim()) {
      setValidation("请填写打款凭证号或转账流水号")
      return
    }
    setValidation(null)
    await onSubmit({ idempotency_key: idempotencyKey, amount_cents: amountCents, settled_on: settledOn, reference: reference.trim(), note: note.trim() || null })
  }
  return <FinanceDialog title="登记结算" onClose={onClose}><form className="revenueShareDialogForm" onSubmit={(event) => void submit(event)}><div className="revenueSharePayee"><span>{item.beneficiary_kind === "person" ? "个人" : "机构"}</span><strong>{item.beneficiary_name}</strong><small>未结算 {formatCny(item.outstanding_cents)}</small></div><label><span>本次结算金额（元）</span><input inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} required /></label><label><span>结算日期</span><input type="date" value={settledOn} onChange={(event) => setSettledOn(event.target.value)} required /></label><label><span>打款凭证号</span><input value={reference} onChange={(event) => setReference(event.target.value)} maxLength={120} required /></label><label><span>备注（选填）</span><textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={500} /></label>{validation ? <p className="formError">{validation}</p> : null}{error ? <p className="formError">{error}</p> : null}<div className="revenueShareDialogActions"><button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>取消</button><button className="primaryButton" type="submit" disabled={pending}>{pending ? "正在登记" : "确认已打款"}</button></div></form></FinanceDialog>
}

function ExportDialog({ pending, error, onClose, onSubmit }: { pending: boolean; error: string | null; onClose: () => void; onSubmit: (purpose: string) => Promise<void> }) {
  const [purpose, setPurpose] = useState("")
  return <FinanceDialog title="导出分成结算" onClose={onClose}><form className="revenueShareDialogForm" onSubmit={(event) => { event.preventDefault(); void onSubmit(purpose.trim()) }}><label><span>导出用途</span><textarea value={purpose} onChange={(event) => setPurpose(event.target.value)} minLength={2} maxLength={200} required /></label>{error ? <p className="formError">{error}</p> : null}<div className="revenueShareDialogActions"><button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>取消</button><button className="primaryButton" type="submit" disabled={pending || purpose.trim().length < 2}><Download aria-hidden="true" />{pending ? "正在导出" : "导出 CSV"}</button></div></form></FinanceDialog>
}

export function RevenueShareFinancePage() {
  const queryClient = useQueryClient()
  const summary = useQuery({ queryKey: financeKey, queryFn: getRevenueShareFinanceSummary })
  const [settlement, setSettlement] = useState<RevenueShareBalance | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const settlementInFlight = useRef(false)

  async function submitSettlement(input: SettlementFormInput): Promise<void> {
    if (!settlement || settlementInFlight.current) return
    settlementInFlight.current = true
    setPending(true)
    setError(null)
    try {
      await createRevenueShareSettlement({ beneficiary_kind: settlement.beneficiary_kind, beneficiary_id: settlement.beneficiary_id, ...input })
      setSettlement(null)
      await queryClient.invalidateQueries({ queryKey: financeKey })
    } catch (caught) {
      setError(errorMessage(caught, "未能确认登记结果，请保持原内容重试；系统会防止重复登记"))
    } finally {
      settlementInFlight.current = false
      setPending(false)
    }
  }

  async function submitExport(purpose: string): Promise<void> {
    setPending(true)
    setError(null)
    try {
      await exportRevenueShareSummary(purpose)
      setExportOpen(false)
    } catch (caught) {
      setError(errorMessage(caught, "分成结算无法导出"))
    } finally {
      setPending(false)
    }
  }

  return <main className="revenueSharePage"><header className="revenueShareHeader"><div><p className="pageEyebrow">FINANCE</p><h1>财务结算</h1><p>公司收款后的分成应结台账</p></div><button className="secondaryButton" type="button" onClick={() => { setError(null); setExportOpen(true) }}><Download aria-hidden="true" />导出</button></header>{summary.isLoading ? <p className="revenueShareStatus">正在汇总分成数据…</p> : null}{summary.isError ? <p className="formError">财务汇总暂时无法读取</p> : null}{summary.data ? <><section className="revenueShareStats" aria-label="分成汇总"><div><span>累计应分</span><strong>{formatCny(summary.data.earned_cents)}</strong></div><div><span>已结算</span><strong>{formatCny(summary.data.settled_cents)}</strong></div><div><span>未结算</span><strong className={summary.data.outstanding_cents < 0 ? "negativeAmount" : ""}>{formatCny(summary.data.outstanding_cents)}</strong></div><div><span>待分配付款</span><strong>{summary.data.pending_payment_count}</strong></div></section><section className="revenueShareSection"><div className="revenueShareSectionHeading"><div><p className="pageEyebrow">SETTLEMENTS</p><h2>收款对象余额</h2></div><span>{summary.data.items.length} 个对象</span></div><div className="revenueShareTableWrap"><table className="revenueShareTable"><thead><tr><th>收款对象</th><th>类型</th><th>累计应分</th><th>已结算</th><th>未结算</th><th>操作</th></tr></thead><tbody>{summary.data.items.map((item) => <tr key={`${item.beneficiary_kind}-${item.beneficiary_id}`}><td><strong>{item.beneficiary_name}</strong></td><td>{item.beneficiary_kind === "person" ? "个人" : "机构"}</td><td>{formatCny(item.earned_cents)}</td><td>{formatCny(item.settled_cents)}</td><td className={item.outstanding_cents < 0 ? "negativeAmount" : ""}>{formatCny(item.outstanding_cents)}</td><td><button className="iconTextButton" type="button" disabled={item.outstanding_cents <= 0} onClick={() => { setError(null); setSettlement(item) }}><Banknote aria-hidden="true" />登记打款</button></td></tr>)}{summary.data.items.length === 0 ? <tr><td colSpan={6}>暂无分成余额</td></tr> : null}</tbody></table></div></section></> : null}{settlement ? <SettlementDialog item={settlement} pending={pending} error={error} onClose={() => { if (!pending) setSettlement(null) }} onSubmit={submitSettlement} /> : null}{exportOpen ? <ExportDialog pending={pending} error={error} onClose={() => { if (!pending) setExportOpen(false) }} onSubmit={submitExport} /> : null}</main>
}
