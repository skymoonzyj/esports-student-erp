import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { QrCode } from "lucide-react"
import { useState } from "react"

import { useAuth } from "../auth/AuthProvider"
import { ensureMyReferralCode, getMyRevenueShare } from "./api"
import type { ReferralCode, RevenueShareSelf } from "./api"
import { displayDate, formatCny, revenueShareRoleLabels } from "./format"
import { ReferralQrDialog } from "./ReferralQrDialog"
import "./revenue-share.css"

const entryKindLabels = {
  earning: "分成入账",
  refund_adjustment: "退款冲减",
  manual_adjustment: "人工调整",
} as const

const myRevenueShareKey = ["revenue-share", "me"] as const

export function MyRevenueSharePage() {
  const { user } = useAuth()
  return <MyRevenueShareContent key={user?.account_id ?? "anonymous"} />
}

function MyRevenueShareContent() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [pageSize, setPageSize] = useState(20)
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const cursor = cursors[cursors.length - 1] ?? null
  const accountKey = [...myRevenueShareKey, user?.account_id] as const
  const result = useQuery({
    queryKey: [...accountKey, pageSize, cursor],
    queryFn: () => getMyRevenueShare({ page_size: pageSize, cursor }),
    enabled: Boolean(user?.account_id),
    placeholderData: keepPreviousData,
  })
  const [qrCode, setQrCode] = useState<ReferralCode | null>(null)
  const referralCodes = result.data?.referral_codes ?? []
  const effectivePermissions = new Set([
    ...(user?.permissions ?? []),
    ...(user?.scoped_permissions ?? []),
  ])
  const canCreateReferralCode = effectivePermissions.has(
    "revenue_share.create_self_referral",
  )
  const ensureReferral = useMutation({
    mutationFn: ensureMyReferralCode,
    onSuccess: (referralCode) => {
      queryClient.setQueriesData<RevenueShareSelf>({ queryKey: accountKey }, (current) => {
        if (!current) return current
        return {
          ...current,
          referral_codes: [
            referralCode,
            ...current.referral_codes.filter((item) => item.id !== referralCode.id),
          ],
        }
      })
      void queryClient.invalidateQueries({ queryKey: accountKey })
      setQrCode(referralCode)
    },
  })

  return (
    <main className="revenueSharePage">
      <header className="revenueShareHeader">
        <div>
          <p className="pageEyebrow">MY REVENUE SHARE</p>
          <h1>我的分成</h1>
          <p>个人邀请二维码、分成与结算记录</p>
        </div>
      </header>
      {result.isLoading ? <p className="revenueShareStatus">正在读取分成记录…</p> : null}
      {result.isError ? <p className="formError" role="alert">分成记录暂时无法读取，请重试当前页。<button className="iconTextButton" type="button" onClick={() => void result.refetch()}>重试</button></p> : null}
      {ensureReferral.isError ? (
        <p className="formError" role="alert">邀请二维码生成失败，请稍后重试</p>
      ) : null}
      {result.data ? (
        <>
          <section className="revenueShareSection" aria-labelledby="my-referral-title">
            <div className="revenueShareSectionHeading">
              <div>
                <p className="pageEyebrow">MY INVITATIONS</p>
                <h2 id="my-referral-title">我的邀请二维码</h2>
              </div>
              <div className="revenueShareSectionActions">
                <span>{referralCodes.length} 个</span>
                {canCreateReferralCode && referralCodes.length === 0 ? (
                  <button
                    className="primaryButton"
                    type="button"
                    onClick={() => ensureReferral.mutate()}
                    disabled={ensureReferral.isPending}
                  >
                    <QrCode aria-hidden="true" />
                    {ensureReferral.isPending ? "正在生成" : "生成我的邀请二维码"}
                  </button>
                ) : null}
              </div>
            </div>
            {referralCodes.length ? (
              <div className="revenueShareReferralGrid">
                {referralCodes.map((item) => (
                  <article className="revenueShareReferralItem" key={item.id}>
                    <div>
                      <strong>{item.label}</strong>
                      <span>邀请码 <code>{item.code}</code></span>
                    </div>
                    <button
                      className="iconTextButton"
                      type="button"
                      onClick={() => setQrCode(item)}
                    >
                      <QrCode aria-hidden="true" />查看二维码
                    </button>
                  </article>
                ))}
              </div>
            ) : (
              <p className="revenueShareEmpty">
                {canCreateReferralCode
                  ? "尚未生成邀请码，点击上方按钮即可创建专属注册链接和二维码。"
                  : "管理员尚未为你生成邀请码。"}
              </p>
            )}
          </section>
          <section className="revenueShareStats revenueShareStatsThree" aria-label="我的分成汇总">
            <div><span>累计分成</span><strong>{formatCny(result.data.earned_cents)}</strong></div>
            <div><span>已结算</span><strong>{formatCny(result.data.settled_cents)}</strong></div>
            <div><span>未结算</span><strong className={result.data.outstanding_cents < 0 ? "negativeAmount" : ""}>{formatCny(result.data.outstanding_cents)}</strong></div>
          </section>
          <section className="revenueShareSection">
            <div className="revenueShareSectionHeading">
              <div><p className="pageEyebrow">LEDGER</p><h2>分成流水</h2></div>
              <span>共 {result.data.total} 条，本页 {result.data.items.length} 条</span>
            </div>
            {result.isFetching ? <p className="revenueShareStatus" role="status">正在读取当前页…</p> : null}
            <div className="revenueShareTableWrap" aria-busy={result.isFetching}>
              <table className="revenueShareTable">
                <thead><tr><th>日期</th><th>类型</th><th>分成角色</th><th>金额</th><th>付款编号</th></tr></thead>
                <tbody>
                  {result.data.items.map((item) => (
                    <tr key={item.id}>
                      <td>{displayDate(item.occurred_at)}</td>
                      <td>{entryKindLabels[item.entry_kind]}</td>
                      <td>{revenueShareRoleLabels[item.role_kind]}</td>
                      <td className={item.amount_cents < 0 ? "negativeAmount" : "positiveAmount"}>{item.amount_cents > 0 ? "+" : ""}{formatCny(item.amount_cents)}</td>
                      <td><code>{item.payment_id}</code></td>
                    </tr>
                  ))}
                  {result.data.items.length === 0 ? <tr><td colSpan={5}>暂无分成流水</td></tr> : null}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}
      <nav className="revenueSharePagination" aria-label="分成流水分页">
        <label>每页条数<select value={pageSize} disabled={result.isFetching} onChange={(event) => {
          setPageSize(Number(event.target.value))
          setCursors([null])
        }}><option value={20}>20 条</option><option value={50}>50 条</option><option value={100}>100 条</option></select></label>
        <span>第 {cursors.length} 页</span>
        <div>
          <button className="secondaryButton" type="button" disabled={cursors.length <= 1 || result.isFetching} onClick={() => setCursors((current) => current.slice(0, -1))}>上一页</button>
          <button className="secondaryButton" type="button" disabled={!result.data?.has_more || !result.data.next_cursor || result.isFetching || result.isPlaceholderData} onClick={() => {
            const next = result.data?.next_cursor
            if (next) setCursors((current) => [...current, next])
          }}>下一页</button>
        </div>
      </nav>
      {qrCode ? <ReferralQrDialog referralCode={qrCode} onClose={() => setQrCode(null)} /> : null}
    </main>
  )
}
