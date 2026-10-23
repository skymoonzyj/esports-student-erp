import { ExternalLink, RefreshCw } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import { useEvaluationAccessError } from "./useEvaluationAccessError"
import { useEvaluationContext } from "./useEvaluationContext"

import "./evaluations.css"

const READY_MESSAGE = {
  type: "game-score-tool.ready",
  api_version: "v1",
} as const

function isReadyMessage(value: unknown): boolean {
  if (!value || typeof value !== "object") return false
  const candidate = value as Record<string, unknown>
  return (
    candidate.type === READY_MESSAGE.type &&
    candidate.api_version === READY_MESSAGE.api_version
  )
}

function SystemContextLoading() {
  return (
    <section className="evaluationLoading" aria-live="polite">
      <span className="evaluationLoadingBar" />
      <span className="evaluationLoadingBar short" />
      <p>正在连接系统评价</p>
    </section>
  )
}

function SystemContextError({
  message,
  onRetry,
}: {
  message: string
  onRetry: () => void
}) {
  return (
    <section className="evaluationError" role="alert">
      <div>
        <strong>系统评价暂时无法连接</strong>
        <p>{message}</p>
      </div>
      <button type="button" onClick={onRetry}>
        <RefreshCw aria-hidden="true" />
        重试
      </button>
    </section>
  )
}

export function SystemEvaluationPage() {
  const [attempt, setAttempt] = useState(0)
  const [frameReady, setFrameReady] = useState(false)
  const [timedOut, setTimedOut] = useState(false)
  const frameRef = useRef<HTMLIFrameElement>(null)
  const context = useEvaluationContext()
  const accessError = useEvaluationAccessError(context.error)

  useEffect(() => {
    if (!context.data) return
    setFrameReady(false)
    setTimedOut(false)
    const timer = window.setTimeout(() => setTimedOut(true), 10_000)

    function receive(event: MessageEvent): void {
      if (
        event.origin !== window.location.origin ||
        event.source !== frameRef.current?.contentWindow ||
        !isReadyMessage(event.data)
      ) {
        return
      }
      window.clearTimeout(timer)
      setFrameReady(true)
    }

    window.addEventListener("message", receive)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener("message", receive)
    }
  }, [attempt, context.data])

  if (context.isPending || context.isFetching || accessError.permissionRevoked) {
    return <SystemContextLoading />
  }
  if (context.isError) {
    return (
      <SystemContextError
        message={accessError.message}
        onRetry={() => void context.refetch()}
      />
    )
  }

  return (
    <div className="systemEvaluationPage">
      <header className="systemEvaluationHeader">
        <div>
          <p className="pageEyebrow">SYSTEM EVALUATION</p>
          <h1>系统评价</h1>
        </div>
        <a
          href={context.data.standalone_start_url}
          target="_blank"
          rel="noopener noreferrer"
        >
          单独打开 <ExternalLink aria-hidden="true" />
        </a>
      </header>
      <section className="evaluationFrameShell">
        {!frameReady && !timedOut ? (
          <div className="evaluationFrameLoading" aria-live="polite">
            正在连接系统评价
          </div>
        ) : null}
        {timedOut && !frameReady ? (
          <div className="evaluationFrameError" role="alert">
            <strong>系统评价暂时无法连接</strong>
            <button
              type="button"
              onClick={() => setAttempt((value) => value + 1)}
            >
              重试
            </button>
          </div>
        ) : null}
        <iframe
          key={attempt}
          ref={frameRef}
          title="系统评价"
          src={context.data.embedded_start_url}
          className={frameReady ? "ready" : "waiting"}
          referrerPolicy="same-origin"
        />
      </section>
    </div>
  )
}
