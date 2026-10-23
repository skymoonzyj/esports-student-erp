import { useLayoutEffect, useRef } from "react"
import type { ReactNode } from "react"

export function TeamDialog({ title, pending, onClose, children }: {
  title: string; pending: boolean; onClose: () => void; children: ReactNode
}) {
  const ref = useRef<HTMLElement>(null)
  const close = useRef(onClose)
  const busy = useRef(pending)
  close.current = onClose
  busy.current = pending
  useLayoutEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = ref.current!
    const controls = () => [...dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')]
    ;(dialog.querySelector<HTMLElement>("input, select, textarea") ?? controls()[0] ?? dialog).focus()
    function keyboard(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy.current) { event.preventDefault(); close.current(); return }
      if (event.key !== "Tab") return
      const items = controls()
      const index = items.indexOf(document.activeElement as HTMLElement)
      if (items.length === 0) { event.preventDefault(); dialog.focus(); return }
      if (event.shiftKey && index <= 0) { event.preventDefault(); items.at(-1)!.focus() }
      else if (!event.shiftKey && (index === -1 || index === items.length - 1)) { event.preventDefault(); items[0].focus() }
    }
    function contain(event: FocusEvent) {
      if (!dialog.contains(event.target as Node)) (controls()[0] ?? dialog).focus()
    }
    document.addEventListener("keydown", keyboard)
    document.addEventListener("focusin", contain)
    return () => {
      document.removeEventListener("keydown", keyboard)
      document.removeEventListener("focusin", contain)
      if (previous?.isConnected) previous.focus()
    }
  }, [])
  return <div className="teamDialogBackdrop" onMouseDown={pending ? undefined : onClose}>
    <section ref={ref} tabIndex={-1} className="teamDialog" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}>{children}</section>
  </div>
}
