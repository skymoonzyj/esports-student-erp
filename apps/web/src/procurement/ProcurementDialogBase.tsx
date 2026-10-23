import type { ReactNode } from "react"
import { X } from "lucide-react"
import { ProblemDetailsError } from "../api/client"

export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

export function yuanToCents(value: string): number | null {
  const match = /^(\d{1,11})(?:\.(\d{1,2}))?$/.exec(value.trim())
  if (!match) return null
  const cents = Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0") || "0")
  return Number.isSafeInteger(cents) && cents > 0 ? cents : null
}

export function ProcurementDialog({
  title,
  description,
  children,
  onClose,
}: {
  title: string
  description?: string
  children: ReactNode
  onClose: () => void
}) {
  return (
    <div className="procurementDialogBackdrop" onMouseDown={onClose}>
      <section
        className="procurementDialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="procurementDialogHeader">
          <div>
            <h2>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          <button
            className="procurementIconButton"
            type="button"
            title="关闭"
            aria-label="关闭"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </button>
        </header>
        {children}
      </section>
    </div>
  )
}
