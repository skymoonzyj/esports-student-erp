import { Printer, QrCode } from "lucide-react"
import { QRCodeSVG } from "qrcode.react"

import type { Asset } from "./api"

export function assetScanPath(assetCode: string): string {
  return `/asset-scan/${encodeURIComponent(assetCode)}`
}

export function AssetQrDialog({ asset, onClose }: { asset: Asset; onClose: () => void }) {
  const scanUrl = `${window.location.origin}${assetScanPath(asset.asset_code)}`
  return (
    <div className="assetDialogBackdrop assetQrDialogBackdrop">
      <section className="assetDialog assetQrDialog" role="dialog" aria-modal="true" aria-labelledby="asset-qr-title">
        <p className="pageEyebrow"><QrCode aria-hidden="true" /> ASSET QR LABEL</p>
        <h2 id="asset-qr-title">{asset.name}</h2>
        <div className="assetQrCode"><QRCodeSVG value={scanUrl} size={220} level="M" includeMargin /></div>
        <span className="assetCode">{asset.asset_code}</span>
        <p className="assetQrUrl">扫码地址：{scanUrl}</p>
        <div className="assetDialogActions">
          <button className="secondaryButton" type="button" onClick={onClose}>关闭</button>
          <button className="primaryButton" type="button" onClick={() => window.print()}><Printer aria-hidden="true" />打印二维码</button>
        </div>
      </section>
    </div>
  )
}

