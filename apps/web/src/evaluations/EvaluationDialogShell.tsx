import {
  useEffect,
  useId,
  useRef,
  type ReactNode,
  type RefObject,
} from "react"
import { createPortal } from "react-dom"

import "./system-evaluation.css"

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",")

let dialogLocks = 0
let previousOverflow = ""
const backgroundLocks = new Map<
  HTMLElement,
  { count: number; hadInert: boolean }
>()

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

function lockBackground(excluded: HTMLElement): () => void {
  const elements = Array.from(document.body.children).filter(
    (element): element is HTMLElement =>
      element instanceof HTMLElement && element !== excluded,
  )
  for (const element of elements) {
    const existing = backgroundLocks.get(element)
    if (existing) {
      existing.count += 1
      continue
    }
    backgroundLocks.set(element, {
      count: 1,
      hadInert: element.hasAttribute("inert"),
    })
    element.setAttribute("inert", "")
  }
  return () => {
    for (const element of elements) {
      const state = backgroundLocks.get(element)
      if (!state) continue
      state.count -= 1
      if (state.count > 0) continue
      backgroundLocks.delete(element)
      if (!state.hadInert) element.removeAttribute("inert")
    }
  }
}

export type EvaluationDialogShellProps = {
  title: string
  closeLabel: string
  pending: boolean
  onClose: () => void
  focusFallbackRef?: RefObject<HTMLElement | null>
  children: ReactNode
}

export function EvaluationDialogShell({
  title,
  closeLabel,
  pending,
  onClose,
  focusFallbackRef,
  children,
}: EvaluationDialogShellProps) {
  const titleId = useId()
  const backdropRef = useRef<HTMLDivElement>(null)
  const dialogRef = useRef<HTMLElement>(null)
  const onCloseRef = useRef(onClose)
  const pendingRef = useRef(pending)
  const focusFallbackRefRef = useRef(focusFallbackRef)
  onCloseRef.current = onClose
  pendingRef.current = pending
  focusFallbackRefRef.current = focusFallbackRef

  useEffect(() => {
    const opener =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const owner = opener?.closest<HTMLElement>("[data-evaluation-dialog-owner]") ?? null
    const backdrop = backdropRef.current
    const dialog = dialogRef.current
    if (!backdrop || !dialog) return
    const releaseBodyLock = lockBody()
    const releaseBackgroundLock = lockBackground(backdrop)

    const focusableElements = (): HTMLElement[] =>
      Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
        (element) =>
          !element.hasAttribute("hidden") &&
          element.getAttribute("aria-disabled") !== "true" &&
          !element.closest('[hidden], [aria-hidden="true"]'),
      )
    const focusable = focusableElements()
    const autofocus = dialog.querySelector<HTMLElement>("[data-autofocus]")
    const initialFocus = autofocus && focusable.includes(autofocus)
      ? autofocus
      : focusable[0]
    if (initialFocus) initialFocus.focus()
    else dialog.focus()

    const handleDialogKeys = (event: KeyboardEvent): void => {
      if (event.key === "Escape" && !pendingRef.current) {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== "Tab") return

      const focusable = focusableElements()
      if (focusable.length === 0) {
        event.preventDefault()
        dialog.focus()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener("keydown", handleDialogKeys)
    return () => {
      document.removeEventListener("keydown", handleDialogKeys)
      releaseBodyLock()
      releaseBackgroundLock()
      queueMicrotask(() => {
        if (dialog.isConnected) return
        const active = document.activeElement
        const activeDialog = active instanceof HTMLElement
          ? active.closest<HTMLElement>('[role="dialog"][aria-modal="true"]')
          : null
        if (activeDialog?.isConnected) return
        for (const target of [
          opener,
          focusFallbackRefRef.current?.current,
          owner,
        ]) {
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

  return createPortal(
    <div
      ref={backdropRef}
      className="systemEvaluationDialogBackdrop"
      onMouseDown={pending ? undefined : () => onClose()}
    >
      <section
        ref={dialogRef}
        className="systemEvaluationDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="systemEvaluationDialogHeader">
          <h2 id={titleId}>{title}</h2>
          <button
            type="button"
            className="systemEvaluationDialogClose"
            aria-label={closeLabel}
            title="关闭"
            disabled={pending}
            onClick={onClose}
          >
            <span aria-hidden="true">×</span>
          </button>
        </header>
        <div className="systemEvaluationDialogBody">{children}</div>
      </section>
    </div>,
    document.body,
  )
}
