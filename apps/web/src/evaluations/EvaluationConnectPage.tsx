import { useMutation } from "@tanstack/react-query"
import { useEffect, useRef } from "react"
import { useParams } from "react-router-dom"

import { createEvaluationTicket } from "./api"

import "./evaluations.css"

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function EvaluationConnectLoading() {
  return (
    <main className="evaluationConnectPage">
      <section className="evaluationConnectCard" aria-live="polite">
        <span className="evaluationConnectMark">竞</span>
        <p>正在建立安全连接…</p>
      </section>
    </main>
  )
}

function EvaluationConnectError({
  message,
  onRetry,
}: {
  message: string
  onRetry?: () => void
}) {
  return (
    <main className="evaluationConnectPage">
      <section className="evaluationConnectCard error" role="alert">
        <strong>{message}</strong>
        {onRetry ? (
          <button type="button" onClick={onRetry}>
            重试
          </button>
        ) : null}
      </section>
    </main>
  )
}

export function EvaluationConnectPage() {
  const { requestId = "" } = useParams()
  const formRef = useRef<HTMLFormElement>(null)
  const requestRef = useRef("")
  const startedRef = useRef("")
  const submittedRef = useRef(false)
  const validRequest = UUID_V4.test(requestId)
  const ticket = useMutation({
    mutationFn: () => createEvaluationTicket(requestId),
  })

  useEffect(() => {
    if (requestRef.current !== requestId) {
      requestRef.current = requestId
      submittedRef.current = false
    }
    if (!validRequest) return
    const timer = window.setTimeout(() => {
      if (startedRef.current === requestId) return
      startedRef.current = requestId
      ticket.mutate()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [requestId, ticket.mutate, validRequest])

  useEffect(() => {
    if (
      requestRef.current !== requestId ||
      !ticket.data ||
      submittedRef.current ||
      !formRef.current
    ) {
      return
    }
    submittedRef.current = true
    formRef.current.submit()
  }, [requestId, ticket.data])

  if (!validRequest) {
    return <EvaluationConnectError message="连接地址无效" />
  }
  if (requestRef.current === requestId && ticket.isError) {
    return (
      <EvaluationConnectError
        message="连接已失效，请返回评价页面重试"
        onRetry={() => {
          submittedRef.current = false
          ticket.reset()
          ticket.mutate()
        }}
      />
    )
  }
  if (requestRef.current !== requestId || !ticket.data) {
    return <EvaluationConnectLoading />
  }

  return (
    <main className="evaluationConnectPage">
      <section className="evaluationConnectCard" aria-live="polite">
        <span className="evaluationConnectMark">竞</span>
        <p>正在安全进入系统评价…</p>
        <form
          ref={formRef}
          data-testid="evaluation-callback-form"
          action={ticket.data.callback_url}
          method="post"
        >
          <input type="hidden" name="ticket" value={ticket.data.ticket} />
          <input type="hidden" name="state" value={ticket.data.state} />
        </form>
      </section>
    </main>
  )
}
