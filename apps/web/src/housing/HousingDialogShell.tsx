import { X } from "lucide-react"
import { useEffect, useId, useRef } from "react"
import type { MouseEvent, ReactNode, RefObject } from "react"

let dialogLocks = 0
let previousOverflow = ""

function lockBody(): () => void {
  if (dialogLocks === 0) {
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"
  }
  dialogLocks += 1
  return () => {
    dialogLocks = Math.max(0, dialogLocks - 1)
    if (dialogLocks === 0) document.body.style.overflow = previousOverflow
  }
}

export function HousingDialogShell({ title, pending, error, focusFallbackRef, onClose, children }: {
  title: string
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  children: ReactNode
}) {
  const titleId = useId()
  const dialogRef = useRef<HTMLElement>(null)
  const focusCapturedRef = useRef(false)
  const openerRef = useRef<HTMLElement | null>(null)
  const ownerRef = useRef<HTMLElement | null>(null)
  const focusFallbackRefRef = useRef(focusFallbackRef)
  const pendingRef = useRef(pending)
  const onCloseRef = useRef(onClose)
  focusFallbackRefRef.current = focusFallbackRef
  pendingRef.current = pending
  onCloseRef.current = onClose

  useEffect(() => {
    const unlock = lockBody()
    const dialog = dialogRef.current
    if (!focusCapturedRef.current) {
      const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
      openerRef.current = opener
      ownerRef.current = opener?.closest<HTMLElement>("[data-housing-dialog-owner]") ?? null
      focusCapturedRef.current = true
    }
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>(
      'input, select, textarea, button, [href], [tabindex]:not([tabindex="-1"])',
    ) ?? []).filter((element) => (
      !element.matches(":disabled") &&
      element.getAttribute("aria-disabled") !== "true" &&
      !element.hidden &&
      !element.closest('[hidden], [aria-hidden="true"]')
    ))
    const autofocus = dialog?.querySelector<HTMLElement>("[data-autofocus]")
    const initial = autofocus && focusable().includes(autofocus) ? autofocus : focusable()[0]
    if (initial) initial.focus(); else dialog?.focus()
    function handleKey(event: KeyboardEvent): void {
      if (event.key === "Escape" && !pendingRef.current) {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== "Tab") return
      const items = focusable()
      const first = items[0]
      const last = items.at(-1)
      if (!first || !last) {
        event.preventDefault()
        dialog?.focus()
        return
      }
      if (!dialog?.contains(document.activeElement)) {
        event.preventDefault()
        if (event.shiftKey) last.focus(); else first.focus()
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus()
      }
    }
    document.addEventListener("keydown", handleKey)
    return () => {
      document.removeEventListener("keydown", handleKey)
      unlock()
      queueMicrotask(() => {
        if (dialog?.isConnected) return
        const active = document.activeElement
        const activeModal = active instanceof HTMLElement
          ? active.closest<HTMLElement>('[role="dialog"][aria-modal="true"]')
          : null
        if (activeModal?.isConnected) return
        for (const target of [openerRef.current, ownerRef.current, focusFallbackRefRef.current?.current]) {
          if (
            target &&
            target !== document.body &&
            target !== document.documentElement &&
            target.isConnected &&
            !target.matches(":disabled") &&
            target.getAttribute("aria-disabled") !== "true" &&
            !target.hidden &&
            !target.closest('[hidden], [aria-hidden="true"]')
          ) {
            target.focus()
            return
          }
        }
      })
    }
  }, [])

  function closeBackdrop(event: MouseEvent<HTMLDivElement>): void {
    if (!pending && event.target === event.currentTarget) onClose()
  }

  return (
    <div className="housingDialogBackdrop" data-testid="housing-dialog-backdrop" onMouseDown={closeBackdrop}>
      <section ref={dialogRef} className="housingDialog" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <header className="housingDialog__header">
          <h2 id={titleId}>{title}</h2>
          <button type="button" data-dialog-close="true" aria-label={`关闭${title}`} disabled={pending} onClick={onClose}><X aria-hidden="true" /></button>
        </header>
        {error ? <p className="housingDialog__error" role="alert">{error}</p> : null}
        {children}
      </section>
    </div>
  )
}
