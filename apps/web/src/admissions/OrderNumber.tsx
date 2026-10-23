import { Check, Copy } from "lucide-react"
import { useState } from "react"

import "./order-number.css"

export function normalizeOrderNumber(value: string): string {
  return value.trim().toUpperCase()
}

export function OrderNumber({ orderNumber, showLabel = true }: {
  orderNumber: string
  showLabel?: boolean
}) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle")

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(orderNumber)
      setCopyStatus("copied")
    } catch {
      setCopyStatus("failed")
    }
  }

  if (!orderNumber) return <span className="orderNumber">—</span>
  return <span className="orderNumber">
    {showLabel ? <span className="orderNumberLabel">订单号</span> : null}
    <code>{orderNumber}</code>
    <button className="orderNumberCopy" type="button" aria-label={`复制订单号 ${orderNumber}`} onClick={() => void copy()}>
      {copyStatus === "copied" ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      <span>{copyStatus === "copied" ? "已复制" : "复制"}</span>
    </button>
    {copyStatus === "copied" ? <span className="orderNumberFeedback" role="status">订单号已复制</span> : null}
    {copyStatus === "failed" ? <span className="orderNumberFeedback" role="alert">复制失败，请长按或选中订单号复制</span> : null}
  </span>
}
