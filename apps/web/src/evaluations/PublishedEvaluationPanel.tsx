import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { listSystemEvaluationTasks } from "./systemEvaluationApi"
import { systemEvaluationKeys } from "./systemEvaluationKeys"

import "./system-evaluation.css"

const PAGE_SIZE = 10

function historyErrorMessage(error: unknown): string {
  return error instanceof ProblemDetailsError
    ? error.problem.detail
    : "系统评价历史暂时无法加载，请重试。"
}

export function PublishedEvaluationPanel({ studentId }: { studentId: string }) {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const [pagination, setPagination] = useState({ studentId, page: 1 })
  const page = pagination.studentId === studentId ? pagination.page : 1
  useEffect(() => {
    if (pagination.studentId === studentId) return
    setPagination({ studentId, page: 1 })
  }, [pagination.studentId, studentId])
  const history = useQuery({
    queryKey: systemEvaluationKeys.published(accountId, studentId, page),
    queryFn: () =>
      listSystemEvaluationTasks("account", {
        studentId,
        publishedOnly: true,
        page,
        pageSize: PAGE_SIZE,
      }),
    enabled: Boolean(user?.account_id) && Boolean(studentId),
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchInterval: false,
  })

  if (history.isPending) {
    return <section className="systemEvaluationState">正在更新系统评价历史...</section>
  }
  if (history.isError) {
    return (
      <section className="systemEvaluationState" role="alert">
        <p>{historyErrorMessage(history.error)}</p>
        <button type="button" onClick={() => void history.refetch()}>
          重试系统评价历史
        </button>
      </section>
    )
  }

  const publishedItems = history.data.items.flatMap((task) =>
    task.published_result ? [{ task, result: task.published_result }] : [],
  )
  const hasPrevious = history.data.page > 1
  const hasNext = history.data.page * history.data.page_size < history.data.total

  return (
    <section className="publishedEvaluationPanel" role="region" aria-label="系统评价历史">
      <header>
        <h2>系统评价历史</h2>
        <p>这里只展示已经审核发布的正式版本。</p>
      </header>
      {publishedItems.length ? (
        <div className="publishedEvaluationList">
          {publishedItems.map(({ task, result }) => (
            <article key={`${task.id}:${result.id}`} aria-label={`${task.public_number} 已发布评价`}>
              <header>
                <div>
                  <strong>{task.project_name}</strong>
                  <p>{task.cohort_name ?? "未关联营期"} · {task.class_name ?? "未关联班级"}</p>
                </div>
                <span>V{result.version}</span>
              </header>
              <p><strong>{result.score} 分 · {result.grade}</strong></p>
              <p>{result.advice}</p>
              <Link to={`/evaluations/system/tasks/${task.id}`}>查看完整评价</Link>
            </article>
          ))}
        </div>
      ) : (
        <p>该学员暂无已发布的系统评价</p>
      )}
      {hasPrevious || hasNext ? (
        <nav className="systemEvaluationPager" aria-label="系统评价历史分页">
          <button
            type="button"
            disabled={!hasPrevious}
            onClick={() => setPagination({ studentId, page: page - 1 })}
          >
            上一页
          </button>
          <span>第 {history.data.page} 页</span>
          <button
            type="button"
            disabled={!hasNext}
            onClick={() => setPagination({ studentId, page: page + 1 })}
          >
            下一页
          </button>
        </nav>
      ) : null}
    </section>
  )
}
