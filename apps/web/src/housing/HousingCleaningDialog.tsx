import type { RefObject } from "react"

import { HousingDialogShell } from "./HousingDialogShell"

export function HousingCleaningDialog({ roomNumber, roomVersion, pending, error, focusFallbackRef, onClose, onSubmit }: {
  roomNumber: string
  roomVersion: number
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (input: { version: number }) => void
}) {
  return <HousingDialogShell title={`完成保洁 ${roomNumber}`} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}>
    <form onSubmit={(event) => { event.preventDefault(); if (!pending) onSubmit({ version: roomVersion }) }}>
      <fieldset disabled={pending}>
        <p>确认房间 {roomNumber} 已完成保洁？保洁完成后仍需满足其他入住条件。</p>
        <footer><button data-autofocus type="button" onClick={onClose}>取消</button><button type="submit">{pending ? "正在保存" : "确认完成保洁"}</button></footer>
      </fieldset>
    </form>
  </HousingDialogShell>
}
