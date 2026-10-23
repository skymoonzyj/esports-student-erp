import { Copy, Download, Printer, X } from "lucide-react"
import { QRCodeSVG } from "qrcode.react"
import { useEffect, useRef, useState } from "react"

import type { ReferralCode } from "./api"

function registrationUrl(referralCode: ReferralCode): string {
  return new URL(referralCode.registration_url, window.location.origin).toString()
}

export function ReferralQrDialog({
  referralCode,
  onClose,
}: {
  referralCode: ReferralCode
  onClose: () => void
}) {
  const svgRef = useRef<SVGSVGElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const [status, setStatus] = useState<string | null>(null)
  const url = registrationUrl(referralCode)

  useEffect(() => {
    closeButtonRef.current?.focus()
    function closeOnEscape(event: KeyboardEvent): void {
      if (event.key === "Escape") onClose()
    }
    window.addEventListener("keydown", closeOnEscape)
    return () => window.removeEventListener("keydown", closeOnEscape)
  }, [onClose])

  async function copyLink(): Promise<void> {
    try {
      await navigator.clipboard.writeText(url)
      setStatus("注册链接已复制")
    } catch {
      setStatus("复制失败，请长按下方链接复制")
    }
  }

  function downloadQrCode(): void {
    const svg = svgRef.current
    if (!svg) return
    const clone = svg.cloneNode(true) as SVGSVGElement
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg")
    const source = new XMLSerializer().serializeToString(clone)
    const objectUrl = URL.createObjectURL(
      new Blob([source], { type: "image/svg+xml;charset=utf-8" }),
    )
    const anchor = document.createElement("a")
    anchor.href = objectUrl
    anchor.download = `邀请注册-${referralCode.code}.svg`
    anchor.click()
    URL.revokeObjectURL(objectUrl)
    setStatus("二维码已下载")
  }

  return (
    <div className="revenueShareDialogBackdrop" onMouseDown={onClose}>
      <section
        className="revenueShareDialog revenueShareQrDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="referral-qr-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <p className="pageEyebrow">REGISTRATION QR</p>
            <h2 id="referral-qr-title">邀请注册二维码</h2>
          </div>
          <button
            ref={closeButtonRef}
            className="revenueShareIconButton revenueShareQrNoPrint"
            type="button"
            title="关闭"
            aria-label="关闭"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="revenueShareQrCode">
          <QRCodeSVG
            ref={svgRef}
            value={url}
            size={240}
            level="M"
            marginSize={2}
            role="img"
            aria-label={`邀请注册二维码：${url}`}
          />
        </div>

        <dl className="revenueShareQrDetails">
          <div><dt>邀请归属</dt><dd>{referralCode.source_name}</dd></div>
          <div><dt>显示名称</dt><dd>{referralCode.label}</dd></div>
          <div><dt>邀请码</dt><dd><code>{referralCode.code}</code></dd></div>
        </dl>

        <p className="revenueShareQrHint">
          扫码后直接进入学员注册，邀请码会自动绑定。付款确认后，系统按生效的邀请人分成规则入账。
        </p>
        <p className="revenueShareQrUrl">{url}</p>
        <div className="revenueShareDialogActions revenueShareQrNoPrint">
          <button className="secondaryButton" type="button" onClick={() => void copyLink()}>
            <Copy aria-hidden="true" />复制链接
          </button>
          <button className="secondaryButton" type="button" onClick={downloadQrCode}>
            <Download aria-hidden="true" />下载二维码
          </button>
          <button className="primaryButton" type="button" onClick={() => window.print()}>
            <Printer aria-hidden="true" />打印
          </button>
        </div>
        <p className="revenueShareQrStatus revenueShareQrNoPrint" aria-live="polite">
          {status}
        </p>
      </section>
    </div>
  )
}
