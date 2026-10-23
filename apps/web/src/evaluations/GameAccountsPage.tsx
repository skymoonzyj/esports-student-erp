import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus, Unlink, X } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { createPortal } from "react-dom"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import {
  bindGameAccount,
  evaluationFoundationKeys,
  listAllGameAccountProjects,
  listGameAccountTargets,
  listGameAccounts,
  unbindGameAccount,
} from "./foundationApi"
import type { StudentGameAccount } from "./foundationApi"
import { useEvaluationAccessError } from "./useEvaluationAccessError"
import { guardedEvaluationQueryOptions, useEvaluationContext } from "./useEvaluationContext"

import "./evaluation-foundation.css"

const GAME_ACCOUNT_HISTORY_PAGE_SIZE = 50

function uniqueById<T extends { id: string }>(items: T[]): T[] {
  const seen = new Set<string>()
  return items.filter((item) => {
    if (seen.has(item.id)) return false
    seen.add(item.id)
    return true
  })
}

function gameAccountHistoryOptions(accountId: string, studentId: string) {
  return {
    queryKey: evaluationFoundationKeys.gameAccountHistory(
      accountId,
      studentId,
      GAME_ACCOUNT_HISTORY_PAGE_SIZE,
    ),
    queryFn: ({ pageParam }: { pageParam: number }) =>
      listGameAccounts(studentId, {
        includeUnbound: true,
        page: pageParam,
        pageSize: GAME_ACCOUNT_HISTORY_PAGE_SIZE,
      }),
    initialPageParam: 1,
    getNextPageParam: (
      lastPage: StudentGameAccount[],
      allPages: StudentGameAccount[][],
    ) => {
      if (lastPage.length < GAME_ACCOUNT_HISTORY_PAGE_SIZE) return undefined
      const previousIds = new Set(
        allPages.slice(0, -1).flatMap((page) => page.map((account) => account.id)),
      )
      if (!lastPage.some((account) => !previousIds.has(account.id))) return undefined
      return allPages.length + 1
    },
  }
}

function formatDateTime(value: string | null): string {
  if (!value) return "-"
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value))
}

function mutationMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ProblemDetailsError)) return fallback
  if (error.problem.code === "game_id_already_bound") {
    return "该游戏账号已被绑定，无法重复绑定"
  }
  if (error.problem.code === "game_account_limit_reached") {
    return "当前有效游戏账号已达上限"
  }
  return error.problem.detail
}

function FoundationDialog({
  title,
  pending,
  onClose,
  children,
}: {
  title: string
  pending: boolean
  onClose: () => void
  children: ReactNode
}) {
  const titleId = `foundation-dialog-${title === "添加游戏账号" ? "bind" : "unbind"}`
  const backdropRef = useRef<HTMLDivElement>(null)
  const dialogRef = useRef<HTMLElement>(null)
  const closeRef = useRef(onClose)
  const pendingRef = useRef(pending)
  const previousFocusRef = useRef(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  )
  closeRef.current = onClose
  pendingRef.current = pending

  useEffect(() => {
    const backdrop = backdropRef.current
    const dialog = dialogRef.current
    if (!backdrop || !dialog) return

    const backgroundStates = Array.from(document.body.children)
      .filter(
        (element): element is HTMLElement =>
          element instanceof HTMLElement && element !== backdrop,
      )
      .map((element) => ({ element, hadInert: element.hasAttribute("inert") }))
    for (const { element } of backgroundStates) element.setAttribute("inert", "")

    const focusableSelector = [
      "button:not([disabled])",
      "a[href]",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      '[tabindex]:not([tabindex="-1"])',
    ].join(",")
    const focusableElements = (): HTMLElement[] =>
      Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (element) => !element.hasAttribute("hidden"),
      )
    if (!dialog.contains(document.activeElement)) focusableElements()[0]?.focus()

    function handleKeys(event: KeyboardEvent): void {
      if (event.key === "Escape" && !pendingRef.current) {
        event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== "Tab") return
      const focusable = focusableElements()
      if (!focusable.length) {
        event.preventDefault()
        dialog?.focus()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !dialog?.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener("keydown", handleKeys)
    return () => {
      document.removeEventListener("keydown", handleKeys)
      for (const { element, hadInert } of backgroundStates) {
        if (!hadInert) element.removeAttribute("inert")
      }
      previousFocusRef.current?.focus()
    }
  }, [])

  return createPortal(
    <div ref={backdropRef} className="foundationDialogBackdrop">
      <section
        ref={dialogRef}
        className="foundationDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header>
          <h2 id={titleId}>{title}</h2>
          <button type="button" aria-label={`关闭${title}`} disabled={pending} onClick={onClose}>
            <X aria-hidden="true" />
          </button>
        </header>
        {children}
      </section>
    </div>,
    document.body,
  )
}

type UnbindDialog = {
  account: StudentGameAccount
  studentId: string
}

type AuthorizationRefreshState =
  | { status: "idle" | "refreshing"; message: null }
  | { status: "error"; message: string }

type MutationFocusTarget =
  | { kind: "bind" }
  | { kind: "unbind"; accountId: string }

export function GameAccountsPage() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const queryClient = useQueryClient()
  const context = useEvaluationContext({ refetchOnMount: false })
  const targets = useQuery({
    queryKey: evaluationFoundationKeys.targets(accountId),
    queryFn: listGameAccountTargets,
    enabled: context.data?.game_accounts_available === true,
    ...guardedEvaluationQueryOptions,
  })
  const [studentId, setStudentId] = useState("")
  const [bindStudentId, setBindStudentId] = useState<string | null>(null)
  const [unbindDialog, setUnbindDialog] = useState<UnbindDialog | null>(null)
  const [projectId, setProjectId] = useState("")
  const [gameId, setGameId] = useState("")
  const [unbindReason, setUnbindReason] = useState("")
  const [formError, setFormError] = useState<string | null>(null)
  const contextUpdatedAt = useRef(0)
  const refreshGeneration = useRef(0)
  const bindOpenerRef = useRef<HTMLButtonElement>(null)
  const unbindOpenerRefs = useRef(new Map<string, HTMLButtonElement>())
  const [mutationFocusTarget, setMutationFocusTarget] =
    useState<MutationFocusTarget | null>(null)
  const [authorizationRefresh, setAuthorizationRefresh] =
    useState<AuthorizationRefreshState>({ status: "idle", message: null })

  useEffect(() => {
    if (!targets.data?.length) {
      setStudentId("")
      setBindStudentId(null)
      setUnbindDialog(null)
      return
    }
    const targetIds = new Set(targets.data.map((target) => target.student_person_id))
    if (bindStudentId && !targetIds.has(bindStudentId)) {
      setBindStudentId(null)
      setProjectId("")
      setGameId("")
      setFormError(null)
    }
    if (unbindDialog && !targetIds.has(unbindDialog.studentId)) {
      setUnbindDialog(null)
      setUnbindReason("")
      setFormError(null)
    }
    if (targetIds.has(studentId)) return
    const self = targets.data.find((target) => target.access_source === "self")
    setStudentId((self ?? targets.data[0]).student_person_id)
  }, [bindStudentId, studentId, targets.data, unbindDialog])

  const defaultTarget =
    targets.data?.find((target) => target.access_source === "self") ?? targets.data?.[0]
  const currentStudentId =
    targets.data?.some((target) => target.student_person_id === studentId)
      ? studentId
      : (defaultTarget?.student_person_id ?? "")

  const accounts = useInfiniteQuery({
    ...gameAccountHistoryOptions(accountId, currentStudentId),
    enabled: Boolean(currentStudentId),
    ...guardedEvaluationQueryOptions,
  })
  const accountHistory = useMemo(
    () =>
      accounts.data
        ? uniqueById(accounts.data.pages.flatMap((page) => page))
        : undefined,
    [accounts.data],
  )
  const projects = useQuery({
    queryKey: evaluationFoundationKeys.gameAccountProjects(accountId, bindStudentId ?? ""),
    queryFn: () => listAllGameAccountProjects(bindStudentId ?? ""),
    enabled: Boolean(bindStudentId),
    ...guardedEvaluationQueryOptions,
  })

  const revalidateProtectedAuthorization = useCallback(async (): Promise<void> => {
    const generation = ++refreshGeneration.current
    setAuthorizationRefresh({ status: "refreshing", message: null })
    try {
      const targetResult = await targets.refetch()
      if (targetResult.isError) throw targetResult.error
      const freshTargets = targetResult.data ?? []
      const defaultFreshTarget =
        freshTargets.find((target) => target.access_source === "self") ?? freshTargets[0]
      const selectedStudentId = freshTargets.some(
        (target) => target.student_person_id === studentId,
      )
        ? studentId
        : (defaultFreshTarget?.student_person_id ?? "")
      const freshTargetIds = new Set(
        freshTargets.map((target) => target.student_person_id),
      )
      const refreshedAccounts = new Map<string, StudentGameAccount[]>()
      const refreshedProjectIds = new Set<string>()
      const refreshes: Promise<unknown>[] = []
      const accountStudentIds = new Set(
        [selectedStudentId, unbindDialog?.studentId].filter(
          (value): value is string => Boolean(value) && freshTargetIds.has(value ?? ""),
        ),
      )
      for (const accountStudentId of accountStudentIds) {
        refreshes.push(
          queryClient.fetchInfiniteQuery({
            ...gameAccountHistoryOptions(accountId, accountStudentId),
            staleTime: 0,
            gcTime: 0,
            pages: 1,
          }).then((data) => {
            refreshedAccounts.set(accountStudentId, data.pages.flatMap((page) => page))
          }),
        )
      }
      if (
        bindStudentId &&
        freshTargetIds.has(bindStudentId)
      ) {
        refreshes.push(
          queryClient.fetchQuery({
            queryKey: evaluationFoundationKeys.gameAccountProjects(accountId, bindStudentId),
            queryFn: () => listAllGameAccountProjects(bindStudentId),
            staleTime: 0,
            gcTime: 0,
          }).then((items) => {
            for (const item of items) refreshedProjectIds.add(item.id)
          }),
        )
      }
      await Promise.all(refreshes)
      if (generation === refreshGeneration.current) {
        if (
          unbindDialog &&
          (!freshTargetIds.has(unbindDialog.studentId) ||
            !refreshedAccounts
              .get(unbindDialog.studentId)
              ?.some(
                (item) =>
                  item.id === unbindDialog.account.id && item.status === "active",
              ))
        ) {
          setUnbindDialog(null)
          setUnbindReason("")
          setFormError(null)
        }
        if (
          bindStudentId &&
          (!freshTargetIds.has(bindStudentId) ||
            (projectId !== "" &&
              !refreshedProjectIds.has(projectId)))
        ) {
          setBindStudentId(null)
          setProjectId("")
          setGameId("")
          setFormError(null)
        }
        setAuthorizationRefresh({ status: "idle", message: null })
      }
    } catch (error) {
      if (generation !== refreshGeneration.current) return
      setAuthorizationRefresh({
        status: "error",
        message:
          error instanceof ProblemDetailsError
            ? error.problem.detail
            : "游戏账号权限暂时无法更新，请重试。",
      })
    }
  }, [
    accountId,
    bindStudentId,
    gameId,
    projectId,
    queryClient,
    studentId,
    targets,
    unbindDialog,
  ])

  useEffect(() => {
    if (!context.dataUpdatedAt) return
    if (!contextUpdatedAt.current) {
      contextUpdatedAt.current = context.dataUpdatedAt
      return
    }
    if (contextUpdatedAt.current === context.dataUpdatedAt) return
    contextUpdatedAt.current = context.dataUpdatedAt
    void revalidateProtectedAuthorization()
  }, [context.dataUpdatedAt, revalidateProtectedAuthorization])

  async function invalidateTarget(targetStudentId: string): Promise<void> {
    await queryClient.invalidateQueries({
      queryKey: evaluationFoundationKeys.targets(accountId),
    })
    await queryClient.fetchInfiniteQuery({
      ...gameAccountHistoryOptions(accountId, targetStudentId),
      staleTime: 0,
      gcTime: 0,
      pages: 1,
    })
  }

  function handleMutationError(error: unknown, fallback: string): void {
    setFormError(mutationMessage(error, fallback))
    if (
      error instanceof ProblemDetailsError &&
      (error.problem.status === 403 ||
        error.problem.code === "evaluation_resource_not_found")
    ) {
      setAuthorizationRefresh({ status: "refreshing", message: null })
      void context.refetch().then((result) => {
        if (!result.isError) return
        setAuthorizationRefresh({
          status: "error",
          message:
            result.error instanceof ProblemDetailsError
              ? result.error.problem.detail
              : "游戏账号权限暂时无法更新，请重试。",
        })
      })
    }
  }

  const bind = useMutation({
    gcTime: 0,
    mutationFn: (input: { studentId: string; projectId: string; gameId: string }) =>
      bindGameAccount(input.studentId, {
        project_id: input.projectId,
        game_id: input.gameId,
      }),
    onSuccess: async (_, input) => {
      setBindStudentId(null)
      setProjectId("")
      setGameId("")
      setFormError(null)
      await invalidateTarget(input.studentId)
      setMutationFocusTarget({ kind: "bind" })
    },
    onError: (error) => {
      handleMutationError(error, "游戏账号暂时无法绑定")
    },
  })
  const unbind = useMutation({
    gcTime: 0,
    mutationFn: (input: { studentId: string; accountId: string; reason: string }) =>
      unbindGameAccount(input.studentId, input.accountId, { reason: input.reason }),
    onSuccess: async (_, input) => {
      setUnbindDialog(null)
      setUnbindReason("")
      setFormError(null)
      await invalidateTarget(input.studentId)
      setMutationFocusTarget({ kind: "unbind", accountId: input.accountId })
    },
    onError: (error) => {
      handleMutationError(error, "游戏账号暂时无法解绑")
    },
  })
  const accessError = useEvaluationAccessError([
    context.error,
    targets.error,
    accounts.error,
    projects.error,
    bind.error,
    unbind.error,
  ])

  useEffect(() => {
    if (bind.isSuccess || bind.isError) bind.reset()
    if (unbind.isSuccess || unbind.isError) unbind.reset()
  }, [
    bind.isError,
    bind.isSuccess,
    bind.reset,
    unbind.isError,
    unbind.isSuccess,
    unbind.reset,
  ])

  const selectedTarget = useMemo(
    () => targets.data?.find((target) => target.student_person_id === currentStudentId),
    [currentStudentId, targets.data],
  )

  function submitBind(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!bindStudentId || !projectId || !gameId.trim() || bind.isPending) return
    if (!projects.data?.some((project) => project.id === projectId)) {
      setBindStudentId(null)
      setProjectId("")
      setGameId("")
      setFormError(null)
      return
    }
    setFormError(null)
    bind.mutate({ studentId: bindStudentId, projectId, gameId })
  }

  function submitUnbind(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!unbindReason.trim()) {
      setFormError("请填写解绑原因")
      return
    }
    if (!unbindDialog || unbind.isPending) return
    setFormError(null)
    unbind.mutate({
      studentId: unbindDialog.studentId,
      accountId: unbindDialog.account.id,
      reason: unbindReason.trim(),
    })
  }

  const contextVersionChanged =
    contextUpdatedAt.current !== 0 && context.dataUpdatedAt !== contextUpdatedAt.current
  const authorizationStale =
    context.isFetching ||
    targets.isFetching ||
    contextVersionChanged ||
    authorizationRefresh.status === "refreshing"

  useEffect(() => {
    if (!mutationFocusTarget || authorizationStale || accounts.isFetching) return
    const target =
      mutationFocusTarget.kind === "bind"
        ? bindOpenerRef.current
        : (unbindOpenerRefs.current.get(mutationFocusTarget.accountId) ??
          bindOpenerRef.current)
    if (!target?.isConnected) return
    target.focus()
    setMutationFocusTarget(null)
  }, [accountHistory, accounts.isFetching, authorizationStale, mutationFocusTarget])

  if (context.isPending || authorizationStale || accessError.permissionRevoked) {
    return <div className="foundationState">正在更新游戏账号权限...</div>
  }
  if (authorizationRefresh.status === "error") {
    return (
      <div className="foundationState" role="alert">
        <p>{authorizationRefresh.message}</p>
        <button type="button" onClick={() => void revalidateProtectedAuthorization()}>
          重试权限检查
        </button>
      </div>
    )
  }
  if (context.isError || targets.isError) {
    return (
      <div className="foundationState" role="alert">
        <p>{accessError.message}</p>
        {targets.isError ? (
          <button type="button" onClick={() => void revalidateProtectedAuthorization()}>
            重试权限检查
          </button>
        ) : null}
      </div>
    )
  }
  if (targets.isPending) {
    return <div className="foundationState">正在读取游戏账号...</div>
  }
  if (
    currentStudentId &&
    (accounts.isPending || (accounts.isFetching && !accounts.isFetchingNextPage))
  ) {
    return <div className="foundationState">正在读取游戏账号...</div>
  }
  if (accounts.isError) {
    return (
      <div className="foundationState" role="alert">
        <p>{accessError.message}</p>
        <button type="button" onClick={() => void accounts.refetch()}>
          重试游戏账号
        </button>
      </div>
    )
  }

  return (
    <div className="foundationPage gameAccountsPage">
      <header className="foundationPageHeader">
        <div>
          <Link to="/evaluations">返回评价中心</Link>
          <p className="pageEyebrow">GAME ACCOUNTS</p>
          <h1>我的游戏账号</h1>
        </div>
        <button
          ref={bindOpenerRef}
          type="button"
          className="foundationPrimaryButton"
          onClick={() => setBindStudentId(currentStudentId)}
        >
          <Plus aria-hidden="true" />
          添加游戏账号
        </button>
      </header>

      <div className="foundationToolbar">
        <label>
          <span>管理对象</span>
          <select value={currentStudentId} onChange={(event) => setStudentId(event.target.value)}>
            {targets.data?.map((target) => (
              <option key={target.student_person_id} value={target.student_person_id}>
                {target.student_name} · {target.student_number} · {target.access_source === "self" ? "本人" : "家庭"}
              </option>
            ))}
          </select>
        </label>
        {selectedTarget ? (
          <strong>当前有效 {selectedTarget.active_count} / {selectedTarget.active_limit} 个</strong>
        ) : null}
      </div>

      {accountHistory ? (
        <>
          <div className="foundationTableWrap">
            <table className="foundationTable responsiveRecords">
            <thead>
              <tr>
                <th>项目</th>
                <th>游戏账号 ID</th>
                <th>状态</th>
                <th>绑定时间</th>
                <th>解绑时间</th>
                <th>解绑原因</th>
                <th><span className="srOnly">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {accountHistory.map((account) => (
                <tr key={account.id}>
                  <td data-label="项目">{account.project_name}</td>
                  <td data-label="游戏账号 ID"><strong>{account.game_id}</strong></td>
                  <td data-label="状态">{account.status === "active" ? "有效" : "已解绑"}</td>
                  <td data-label="绑定时间">{formatDateTime(account.valid_from)}</td>
                  <td data-label="解绑时间">{formatDateTime(account.unbound_at)}</td>
                  <td data-label="解绑原因">{account.unbind_reason ?? "-"}</td>
                  <td data-label="操作">
                    {account.status === "active" ? (
                      <button
                        ref={(element) => {
                          if (element) unbindOpenerRefs.current.set(account.id, element)
                          else unbindOpenerRefs.current.delete(account.id)
                        }}
                        type="button"
                        className="foundationIconTextButton danger"
                        aria-label={`解绑 ${account.game_id}`}
                        onClick={() => {
                          setFormError(null)
                          setUnbindReason("")
                          setUnbindDialog({ account, studentId: currentStudentId })
                        }}
                      >
                        <Unlink aria-hidden="true" />解绑
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
            </table>
          </div>
          {accounts.hasNextPage ? (
            <div className="foundationPager foundationLoadOlder">
              <button
                type="button"
                disabled={accounts.isFetchingNextPage}
                onClick={() => void accounts.fetchNextPage()}
              >
                {accounts.isFetchingNextPage ? "正在加载..." : "加载更早记录"}
              </button>
            </div>
          ) : null}
        </>
      ) : null}

      {bindStudentId ? (
        <FoundationDialog
          title="添加游戏账号"
          pending={bind.isPending}
          onClose={() => {
            if (!bind.isPending) setBindStudentId(null)
          }}
        >
          <form className="foundationForm" onSubmit={submitBind}>
            <label>
              <span>项目</span>
              <select
                autoFocus
                value={projectId}
                onChange={(event) => setProjectId(event.target.value)}
                disabled={bind.isPending}
                required
              >
                <option value="">请选择项目</option>
                {projects.data?.map((project) => (
                  <option key={project.id} value={project.id}>{project.name}</option>
                ))}
              </select>
            </label>
            <label>
              <span>游戏账号 ID</span>
              <input
                value={gameId}
                onChange={(event) => setGameId(event.target.value)}
                disabled={bind.isPending}
                minLength={2}
                maxLength={64}
                required
              />
            </label>
            {formError ? <p className="foundationFormError" role="alert">{formError}</p> : null}
            <footer className="foundationDialogActions">
              <button type="button" disabled={bind.isPending} onClick={() => setBindStudentId(null)}>取消</button>
              <button type="submit" disabled={bind.isPending}>确认绑定</button>
            </footer>
          </form>
        </FoundationDialog>
      ) : null}

      {unbindDialog ? (
        <FoundationDialog
          title="解绑游戏账号"
          pending={unbind.isPending}
          onClose={() => {
            if (!unbind.isPending) setUnbindDialog(null)
          }}
        >
          <form className="foundationForm" onSubmit={submitUnbind}>
            <p>确认解绑 {unbindDialog.account.game_id}</p>
            <label>
              <span>解绑原因</span>
              <textarea
                autoFocus
                value={unbindReason}
                onChange={(event) => setUnbindReason(event.target.value)}
                disabled={unbind.isPending}
                maxLength={200}
                required
              />
            </label>
            {formError ? <p className="foundationFormError" role="alert">{formError}</p> : null}
            <footer className="foundationDialogActions">
              <button type="button" disabled={unbind.isPending} onClick={() => setUnbindDialog(null)}>取消</button>
              <button type="submit" disabled={unbind.isPending}>确认解绑</button>
            </footer>
          </form>
        </FoundationDialog>
      ) : null}
    </div>
  )
}
