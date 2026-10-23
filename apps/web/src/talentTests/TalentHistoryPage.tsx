import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { formatShanghaiDate } from "../teaching/datetime"
import { listTalentSessions } from "./api"
import type { SessionStatus } from "./contracts"
import "./talentTests.css"

const STATUS_NAMES: Record<SessionStatus, string> = {
  in_progress: "进行中",
  completed: "已完成",
  abandoned: "已放弃",
}

export function TalentHistoryPage({ accountId }: { accountId: string }) {
  const [page, setPage] = useState(1)
  const queryClient = useQueryClient()
  const rootKey = useMemo(() => ["talent-tests", accountId, "history"] as const, [accountId])
  const history = useQuery({
    queryKey: [...rootKey, page],
    queryFn: () => listTalentSessions(page, 20),
  })

  useEffect(() => {
    if (!(history.error instanceof ProblemDetailsError) || history.error.problem.status !== 403) return
    void queryClient.cancelQueries({ queryKey: rootKey }).finally(() => {
      queryClient.removeQueries({ queryKey: rootKey })
    })
  }, [history.error, queryClient, rootKey])

  if (history.isPending) return <p>正在读取历史记录…</p>
  if (history.isError || !history.data) return <p role="alert">无法读取天赋测试历史。</p>
  const totalPages = Math.max(1, Math.ceil(history.data.total / 20))

  return (
    <div className="talent-page">
      <header className="talent-page-heading">
        <div>
          <p className="talent-game-kicker">SERVER HISTORY</p>
          <h1>天赋测试历史</h1>
        </div>
        <Link to="/talent-tests">返回天赋测试</Link>
      </header>
      {history.data.items.length === 0 ? <p>本页没有测试轮次。</p> : (
        <div className="talent-history-list">
          {history.data.items.map((session) => (
            <article key={session.id} className="talent-history-card">
              <div>
                <strong>{STATUS_NAMES[session.status]}</strong>
                <span>{formatShanghaiDate(session.completed_at ?? session.abandoned_at ?? session.created_at)}</span>
              </div>
              <p>{session.saved_count}/{session.required_count}</p>
              {session.report_id ? (
                <Link to={`/talent-tests/reports/${session.report_id}`}>查看报告</Link>
              ) : null}
            </article>
          ))}
        </div>
      )}
      <nav className="talent-pagination" aria-label="历史分页">
        <button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
        <span>第 {page} 页</span>
        <button type="button" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
      </nav>
    </div>
  )
}
