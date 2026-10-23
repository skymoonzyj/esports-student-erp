import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronLeft, ChevronRight, Pencil, Phone, Search, X } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { createPortal } from "react-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import {
  addAuxiliaryPhone,
  captureAuxiliaryPhoneWrite,
  deactivateAuxiliaryPhone,
  evaluationFoundationKeys,
  getStudentMatchingProfile,
  listAuxiliaryPhones,
  listGameAccounts,
  listStudents,
  replaceAuxiliaryPhone,
  updateGameAccountLimit,
} from "./foundationApi"
import type {
  AuxiliaryPhone,
  AuxiliaryPhoneWriteOnly,
  StudentGameAccount,
} from "./foundationApi"
import { useEvaluationAuthorization } from "./EvaluationAuthorization"
import { useEvaluationAccessError } from "./useEvaluationAccessError"

import "./evaluation-foundation.css"

type MatchingDialog =
  | { kind: "limit"; studentId: string }
  | { kind: "add"; studentId: string }
  | { kind: "replace"; studentId: string; phone: AuxiliaryPhone }
  | { kind: "deactivate"; studentId: string; phone: AuxiliaryPhone }

const dialogTitles: Record<MatchingDialog["kind"], string> = {
  limit: "调整游戏账号上限",
  add: "添加辅助手机号",
  replace: "替换辅助手机号",
  deactivate: "停用辅助手机号",
}

const MATCHING_HISTORY_PAGE_SIZE = 50

function uniqueById<T extends { id: string }>(items: T[]): T[] {
  return Array.from(new Map(items.map((item) => [item.id, item])).values())
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function isObjectAccessError(error: unknown): error is ProblemDetailsError {
  return (
    error instanceof ProblemDetailsError &&
    (error.problem.status === 403 || error.problem.status === 404) &&
    error.problem.code !== "evaluation_access_denied"
  )
}

function MatchingModal({
  dialog,
  pending,
  onClose,
  children,
}: {
  dialog: MatchingDialog
  pending: boolean
  onClose: () => void
  children: ReactNode
}) {
  const title = dialogTitles[dialog.kind]
  const titleId = `matching-${dialog.kind}-title`
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
    const modal = dialogRef.current
    if (!backdrop || !modal) return

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
      Array.from(modal.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (element) => !element.hasAttribute("hidden"),
      )
    if (!modal.contains(document.activeElement)) focusableElements()[0]?.focus()

    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape" && !pendingRef.current) {
        event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== "Tab") return
      const focusable = focusableElements()
      if (!focusable.length) {
        event.preventDefault()
        modal?.focus()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !modal?.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("keydown", onKeyDown)
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
        <header><h2 id={titleId}>{title}</h2><button type="button" aria-label={`关闭${title}`} disabled={pending} onClick={onClose}><X aria-hidden="true" /></button></header>
        {children}
      </section>
    </div>,
    document.body,
  )
}

export function StudentMatchingPanel({
  studentId: fixedStudentId,
  onAccessRevoked,
}: {
  studentId?: string
  onAccessRevoked?: () => void
}) {
  const { pending: authorizationPending, revision: authorizationRevision } =
    useEvaluationAuthorization()
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const queryClient = useQueryClient()
  const adminKey = [...evaluationFoundationKeys.admin(accountId), "student-matching"] as const
  const [search, setSearch] = useState("")
  const [submittedSearch, setSubmittedSearch] = useState("")
  const [page, setPage] = useState(1)
  const [selectedStudentId, setSelectedStudentId] = useState(fixedStudentId ?? "")
  const studentId = fixedStudentId ?? selectedStudentId
  const [dialog, setDialog] = useState<MatchingDialog | null>(null)
  const [limit, setLimit] = useState("3")
  const [phone, setPhone] = useState("")
  const [reason, setReason] = useState("")
  const [formError, setFormError] = useState<string | null>(null)
  const [profileAccessError, setProfileAccessError] = useState<string | null>(null)
  const [evaluationAccessRevoked, setEvaluationAccessRevoked] = useState(false)
  const previousAuthorizationRevision = useRef(authorizationRevision)
  const profileAccessErrorStudentId = useRef<string | null>(null)
  const sensitiveStudentId = profileAccessError ? "" : studentId

  const students = useQuery({
    queryKey: [
      ...adminKey,
      "students",
      submittedSearch,
      page,
      "authorization",
      authorizationRevision,
    ],
    queryFn: () => listStudents({ query: submittedSearch, page, pageSize: 20 }),
    enabled: !fixedStudentId && !evaluationAccessRevoked,
    gcTime: 0,
  })
  const profileKey = [
    ...adminKey,
    "profile",
    sensitiveStudentId,
    "authorization",
    authorizationRevision,
  ] as const
  const profile = useQuery({
    queryKey: profileKey,
    queryFn: () => getStudentMatchingProfile(sensitiveStudentId),
    enabled: Boolean(sensitiveStudentId) && !evaluationAccessRevoked,
    gcTime: 0,
  })
  const olderHistoryKey = [
    ...adminKey,
    "older-history",
    sensitiveStudentId,
    "authorization",
    authorizationRevision,
  ] as const
  const olderGames = useInfiniteQuery({
    queryKey: [...olderHistoryKey, "game-accounts"] as const,
    queryFn: ({ pageParam }: { pageParam: number }) =>
      listGameAccounts(sensitiveStudentId, {
        includeUnbound: true,
        page: pageParam,
        pageSize: MATCHING_HISTORY_PAGE_SIZE,
      }),
    initialPageParam: 2,
    getNextPageParam: (
      lastPage: StudentGameAccount[],
      allPages: StudentGameAccount[][],
    ) => {
      const loaded =
        (profile.data?.game_accounts.length ?? 0) +
        allPages.reduce((count, items) => count + items.length, 0)
      if (
        lastPage.length < MATCHING_HISTORY_PAGE_SIZE ||
        loaded >= (profile.data?.game_accounts_total ?? 0)
      ) {
        return undefined
      }
      return allPages.length + 2
    },
    enabled: false,
    gcTime: 0,
  })
  const olderPhones = useInfiniteQuery({
    queryKey: [...olderHistoryKey, "auxiliary-phones"] as const,
    queryFn: ({ pageParam }: { pageParam: number }) =>
      listAuxiliaryPhones(sensitiveStudentId, {
        includeInactive: true,
        page: pageParam,
        pageSize: MATCHING_HISTORY_PAGE_SIZE,
      }),
    initialPageParam: 2,
    getNextPageParam: (
      lastPage: AuxiliaryPhone[],
      allPages: AuxiliaryPhone[][],
    ) => {
      const loaded =
        (profile.data?.auxiliary_phones.length ?? 0) +
        allPages.reduce((count, items) => count + items.length, 0)
      if (
        lastPage.length < MATCHING_HISTORY_PAGE_SIZE ||
        loaded >= (profile.data?.auxiliary_phones_total ?? 0)
      ) {
        return undefined
      }
      return allPages.length + 2
    },
    enabled: false,
    gcTime: 0,
  })
  const standaloneProfileObjectDenied =
    !fixedStudentId && Boolean(studentId) && isObjectAccessError(profile.error)
  const olderHistoryObjectError = isObjectAccessError(olderGames.error)
    ? olderGames.error
    : isObjectAccessError(olderPhones.error)
      ? olderPhones.error
      : null
  const synchronousObjectAccessError =
    standaloneProfileObjectDenied && isObjectAccessError(profile.error)
      ? profile.error
      : olderHistoryObjectError

  useEffect(() => {
    const previousRevision = previousAuthorizationRevision.current
    previousAuthorizationRevision.current = authorizationRevision
    if (previousRevision === authorizationRevision || !studentId) return
    const matchingKey = [
      ...evaluationFoundationKeys.admin(accountId),
      "student-matching",
    ] as const
    queryClient.removeQueries({
      queryKey: [
        ...matchingKey,
        "profile",
        studentId,
        "authorization",
        previousRevision,
      ],
      exact: true,
    })
    queryClient.removeQueries({
      queryKey: [
        ...matchingKey,
        "older-history",
        studentId,
        "authorization",
        previousRevision,
      ],
    })
  }, [accountId, authorizationRevision, queryClient, studentId])

  const displayedGames = useMemo(
    () =>
      uniqueById([
        ...(profile.data?.game_accounts ?? []),
        ...(olderGames.data?.pages.flatMap((items) => items) ?? []),
      ]),
    [olderGames.data, profile.data?.game_accounts],
  )
  const displayedPhones = useMemo(
    () =>
      uniqueById([
        ...(profile.data?.auxiliary_phones ?? []),
        ...(olderPhones.data?.pages.flatMap((items) => items) ?? []),
      ]),
    [olderPhones.data, profile.data?.auxiliary_phones],
  )

  useEffect(() => {
    const deniedStudentId = profileAccessErrorStudentId.current
    if (!profileAccessError || !deniedStudentId) return
    const matchingKey = [
      ...evaluationFoundationKeys.admin(accountId),
      "student-matching",
    ] as const
    queryClient.removeQueries({
      queryKey: [...matchingKey, "profile", deniedStudentId],
    })
    queryClient.removeQueries({
      queryKey: [...matchingKey, "older-history", deniedStudentId],
    })
  }, [accountId, profileAccessError, queryClient])

  useEffect(() => {
    profileAccessErrorStudentId.current = null
    setProfileAccessError(null)
  }, [studentId])

  useEffect(() => {
    if (!olderHistoryObjectError || !sensitiveStudentId) return
    setDialog(null)
    setPhone("")
    setReason("")
    setFormError(null)
    profileAccessErrorStudentId.current = sensitiveStudentId
    setProfileAccessError(olderHistoryObjectError.problem.detail)
  }, [olderHistoryObjectError, sensitiveStudentId])

  useEffect(() => {
    if (!standaloneProfileObjectDenied || !studentId) return
    const deniedStudentId = studentId
    setDialog(null)
    setPhone("")
    setReason("")
    setFormError(null)
    profileAccessErrorStudentId.current = null
    setProfileAccessError(null)
    const matchingKey = [
      ...evaluationFoundationKeys.admin(accountId),
      "student-matching",
    ] as const
    queryClient.removeQueries({
      queryKey: [...matchingKey, "profile", deniedStudentId],
    })
    queryClient.removeQueries({
      queryKey: [...matchingKey, "older-history", deniedStudentId],
    })
    setSelectedStudentId((current) =>
      current === deniedStudentId ? "" : current,
    )
  }, [accountId, queryClient, standaloneProfileObjectDenied, studentId])

  async function refreshProfile(targetStudentId: string): Promise<void> {
    await queryClient.invalidateQueries({
      queryKey: [...adminKey, "profile", targetStudentId],
    })
  }

  async function completeMutation(targetStudentId: string): Promise<void> {
    queryClient.removeQueries({
      queryKey: [...adminKey, "older-history", targetStudentId],
    })
    await refreshProfile(targetStudentId)
    setDialog(null)
    setPhone("")
    setReason("")
    setFormError(null)
    setProfileAccessError(null)
  }

  function handleMutationError(
    error: unknown,
    fallback: string,
    targetStudentId: string,
  ): void {
    if (
      error instanceof ProblemDetailsError &&
      error.problem.code === "evaluation_access_denied"
    ) {
      setEvaluationAccessRevoked(true)
    }
    if (isObjectAccessError(error)) {
      setDialog(null)
      setPhone("")
      setReason("")
      setFormError(null)
      profileAccessErrorStudentId.current = targetStudentId
      setProfileAccessError(error.problem.detail)
      return
    }
    setFormError(errorMessage(error, fallback))
  }

  const limitMutation = useMutation({
    mutationFn: (input: { studentId: string; limit: number; reason: string }) =>
      updateGameAccountLimit(input.studentId, {
        max_active_accounts: input.limit,
        reason: input.reason,
      }),
    onSuccess: (_, input) => completeMutation(input.studentId),
    onError: (error, input) => {
      handleMutationError(error, "游戏账号上限暂时无法调整", input.studentId)
    },
  })
  const addMutation = useMutation({
    gcTime: 0,
    mutationFn: (input: { studentId: string; write: AuxiliaryPhoneWriteOnly }) =>
      addAuxiliaryPhone(input.studentId, input.write),
    onSuccess: (_, input) => completeMutation(input.studentId),
    onError: (error, input) => {
      handleMutationError(error, "辅助手机号暂时无法添加", input.studentId)
    },
  })
  const replaceMutation = useMutation({
    gcTime: 0,
    mutationFn: (input: {
      studentId: string
      phoneId: string
      write: AuxiliaryPhoneWriteOnly
    }) => replaceAuxiliaryPhone(input.studentId, input.phoneId, input.write),
    onSuccess: (_, input) => completeMutation(input.studentId),
    onError: (error, input) => {
      handleMutationError(error, "辅助手机号暂时无法替换", input.studentId)
    },
  })
  const deactivateMutation = useMutation({
    mutationFn: (input: { studentId: string; phoneId: string; reason: string }) =>
      deactivateAuxiliaryPhone(input.studentId, input.phoneId, { reason: input.reason }),
    onSuccess: (_, input) => completeMutation(input.studentId),
    onError: (error, input) => {
      handleMutationError(error, "辅助手机号暂时无法停用", input.studentId)
    },
  })
  const accessError = useEvaluationAccessError([
    limitMutation.error,
    addMutation.error,
    replaceMutation.error,
    deactivateMutation.error,
    olderGames.error,
    olderPhones.error,
    students.error,
    profile.error,
  ])
  const pending =
    limitMutation.isPending || addMutation.isPending || replaceMutation.isPending || deactivateMutation.isPending
  const protectedDataFetching =
    authorizationPending ||
    (!fixedStudentId && students.isFetching) ||
    (Boolean(studentId) && profile.isFetching)
  const failedProtectedQuery = students.isError
    ? students
    : profile.isError && !standaloneProfileObjectDenied
      ? profile
      : undefined

  async function retryProtectedData(): Promise<void> {
    const retryingObjectAccess = profileAccessError !== null
    profileAccessErrorStudentId.current = null
    setProfileAccessError(null)
    if (retryingObjectAccess) return
    const retries: Array<Promise<unknown>> = []
    if (!fixedStudentId) retries.push(students.refetch())
    if (studentId) retries.push(profile.refetch())
    await Promise.all(retries)
  }

  useEffect(() => {
    if (accessError.permissionRevoked) onAccessRevoked?.()
  }, [accessError.permissionRevoked, onAccessRevoked])

  useEffect(() => {
    if (addMutation.isSuccess || addMutation.isError) addMutation.reset()
    if (replaceMutation.isSuccess || replaceMutation.isError) replaceMutation.reset()
  }, [
    addMutation.isError,
    addMutation.isSuccess,
    addMutation.reset,
    replaceMutation.isError,
    replaceMutation.isSuccess,
    replaceMutation.reset,
  ])

  function openDialog(next: MatchingDialog): void {
    setDialog(next)
    setFormError(null)
    setPhone("")
    setReason("")
    setProfileAccessError(null)
    if (next.kind === "limit") setLimit(String(profile.data?.active_limit ?? 3))
  }

  function searchStudents(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setPage(1)
    setSubmittedSearch(search.trim())
  }

  function submitDialog(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!dialog || pending) return
    if (dialog.kind !== "add" && !reason.trim()) {
      setFormError(dialog.kind === "limit" ? "请填写调整原因" : dialog.kind === "replace" ? "请填写替换原因" : "请填写停用原因")
      return
    }
    setFormError(null)
    if (dialog.kind === "limit") {
      limitMutation.mutate({
        studentId: dialog.studentId,
        limit: Number(limit),
        reason: reason.trim(),
      })
    }
    if (dialog.kind === "add") {
      addMutation.mutate({
        studentId: dialog.studentId,
        write: captureAuxiliaryPhoneWrite(event.currentTarget),
      })
    }
    if (dialog.kind === "replace") {
      replaceMutation.mutate({
        studentId: dialog.studentId,
        phoneId: dialog.phone.id,
        write: captureAuxiliaryPhoneWrite(event.currentTarget),
      })
    }
    if (dialog.kind === "deactivate") {
      deactivateMutation.mutate({
        studentId: dialog.studentId,
        phoneId: dialog.phone.id,
        reason: reason.trim(),
      })
    }
  }

  if (accessError.permissionRevoked) {
    return <div className="foundationState">正在更新基础数据权限...</div>
  }
  if (protectedDataFetching) {
    return <div className="foundationState">正在更新匹配资料...</div>
  }
  if (synchronousObjectAccessError) {
    return (
      <div className="foundationState" role="alert">
        <p>{synchronousObjectAccessError.problem.detail}</p>
      </div>
    )
  }
  if (profileAccessError || failedProtectedQuery) {
    return (
      <div className="foundationState" role="alert">
        <p>
          {profileAccessError ??
            errorMessage(failedProtectedQuery?.error, "匹配资料暂时无法读取")}
        </p>
        <button type="button" onClick={() => void retryProtectedData()}>
          重试匹配资料
        </button>
      </div>
    )
  }

  return (
    <section className="foundationPanel studentMatchingPanel" aria-label="学员匹配">
      {!fixedStudentId ? (
        <aside className="matchingDirectory" aria-label="学员目录">
          <form className="foundationSearch" onSubmit={searchStudents}>
            <label><span>搜索学员</span><input value={search} onChange={(event) => setSearch(event.target.value)} /></label>
            <button type="submit"><Search aria-hidden="true" />搜索</button>
          </form>
          <div className="matchingStudentList">
            {students.data?.items.map((student) => (
              <button
                type="button"
                key={student.student_person_id}
                aria-label={`选择 ${student.student_name} · ${student.student_number} · ${student.student_person_id}`}
                className={student.student_person_id === studentId ? "active" : ""}
                onClick={() => setSelectedStudentId(student.student_person_id)}
              >
                <strong>{student.student_name}</strong><span>{student.student_number}</span>
              </button>
            ))}
          </div>
          {(students.data?.total ?? 0) > 20 ? (
            <div className="foundationPager"><button type="button" aria-label="上一页" disabled={page <= 1} onClick={() => setPage((current) => current - 1)}><ChevronLeft aria-hidden="true" /></button><span>{page}</span><button type="button" aria-label="下一页" disabled={page * 20 >= (students.data?.total ?? 0)} onClick={() => setPage((current) => current + 1)}><ChevronRight aria-hidden="true" /></button></div>
          ) : null}
        </aside>
      ) : null}

      <div className="matchingDetail">
        {!studentId ? <div className="foundationState">请选择学员</div> : null}
        {profile.data ? (
          <>
            {fixedStudentId ? <h2 className="embeddedMatchingTitle">评价匹配资料</h2> : null}
            <header className="matchingDetailHeader">
              <div><h2>{profile.data.student_name}</h2><p>{profile.data.student_number} · <span>{profile.data.student_person_id}</span></p><strong>当前有效 {profile.data.active_count} / {profile.data.active_limit} 个</strong></div>
              <button type="button" onClick={() => openDialog({ kind: "limit", studentId })}><Pencil aria-hidden="true" />调整账号上限</button>
            </header>
            <section className="matchingSection" aria-labelledby="matching-games-title">
              <h3 id="matching-games-title">游戏账号历史</h3>
              <p className="matchingHistorySummary">
                {profile.data.game_accounts_truncated
                  ? `显示最近 ${displayedGames.length} 条，共 ${profile.data.game_accounts_total} 条`
                  : `共 ${profile.data.game_accounts_total} 条`}
              </p>
              <div className="foundationTableWrap"><table className="foundationTable responsiveRecords"><thead><tr><th>项目</th><th>完整授权游戏 ID</th><th>状态</th><th>原因</th></tr></thead><tbody>{displayedGames.map((account) => <tr key={account.id}><td data-label="项目">{account.project_name}</td><td data-label="完整授权游戏 ID"><strong>{account.game_id}</strong></td><td data-label="状态">{account.status === "active" ? "有效" : "已解绑"}</td><td data-label="原因">{account.unbind_reason ?? "-"}</td></tr>)}</tbody></table></div>
              {olderGames.error ? <p className="foundationFormError" role="alert">{errorMessage(olderGames.error, "更早游戏账号记录暂时无法读取")}</p> : null}
              {profile.data.game_accounts_truncated && (!olderGames.data || olderGames.hasNextPage) ? <button type="button" className="matchingLoadOlder" disabled={olderGames.isFetchingNextPage} onClick={() => void olderGames.fetchNextPage()}>{olderGames.isFetchingNextPage ? "正在加载更早游戏账号记录..." : "加载更早游戏账号记录"}</button> : null}
            </section>
            <section className="matchingSection" aria-labelledby="matching-phones-title">
              <div className="matchingSectionHeader"><h3 id="matching-phones-title">辅助手机号</h3><button type="button" onClick={() => openDialog({ kind: "add", studentId })}><Phone aria-hidden="true" />添加辅助手机号</button></div>
              <p className="matchingHistorySummary">
                {profile.data.auxiliary_phones_truncated
                  ? `显示最近 ${displayedPhones.length} 条，共 ${profile.data.auxiliary_phones_total} 条`
                  : `共 ${profile.data.auxiliary_phones_total} 条`}
              </p>
              <div className="foundationTableWrap"><table className="foundationTable responsiveRecords"><thead><tr><th>手机号</th><th>状态</th><th><span className="srOnly">操作</span></th></tr></thead><tbody>{displayedPhones.map((item) => <tr key={item.id}><td data-label="手机号"><strong>{item.masked_phone}</strong></td><td data-label="状态">{item.status === "active" ? "有效" : "已停用"}</td><td data-label="操作">{item.status === "active" ? <span className="matchingRowActions"><button type="button" aria-label={`替换 ${item.masked_phone}`} onClick={() => openDialog({ kind: "replace", studentId, phone: item })}>替换</button><button type="button" aria-label={`停用 ${item.masked_phone}`} onClick={() => openDialog({ kind: "deactivate", studentId, phone: item })}>停用</button></span> : null}</td></tr>)}</tbody></table></div>
              {olderPhones.error ? <p className="foundationFormError" role="alert">{errorMessage(olderPhones.error, "更早辅助手机号记录暂时无法读取")}</p> : null}
              {profile.data.auxiliary_phones_truncated && (!olderPhones.data || olderPhones.hasNextPage) ? <button type="button" className="matchingLoadOlder" disabled={olderPhones.isFetchingNextPage} onClick={() => void olderPhones.fetchNextPage()}>{olderPhones.isFetchingNextPage ? "正在加载更早辅助手机号记录..." : "加载更早辅助手机号记录"}</button> : null}
            </section>
          </>
        ) : null}
      </div>

      {dialog && !authorizationPending ? (
        <MatchingModal dialog={dialog} pending={pending} onClose={() => !pending && setDialog(null)}>
          <form className="foundationForm" onSubmit={submitDialog}>
            {dialog.kind === "limit" ? <label><span>账号上限</span><input autoFocus type="number" min={3} value={limit} disabled={pending} onChange={(event) => setLimit(event.target.value)} required /></label> : null}
            {dialog.kind === "add" ? <label><span>手机号</span><input name="phone" autoFocus type="tel" value={phone} disabled={pending} onChange={(event) => setPhone(event.target.value)} required /></label> : null}
            {dialog.kind === "replace" ? <label><span>新手机号</span><input name="phone" autoFocus type="tel" value={phone} disabled={pending} onChange={(event) => setPhone(event.target.value)} required /></label> : null}
            {dialog.kind !== "add" ? <label><span>{dialog.kind === "limit" ? "调整原因" : dialog.kind === "replace" ? "替换原因" : "停用原因"}</span><textarea name="reason" autoFocus={dialog.kind === "deactivate"} value={reason} disabled={pending} onChange={(event) => setReason(event.target.value)} required /></label> : null}
            {formError ? <p className="foundationFormError" role="alert">{formError}</p> : null}
            <footer className="foundationDialogActions"><button type="button" disabled={pending} onClick={() => setDialog(null)}>取消</button><button type="submit" disabled={pending}>{dialog.kind === "limit" ? "确认调整" : dialog.kind === "add" ? "确认添加" : dialog.kind === "replace" ? "确认替换" : "确认停用"}</button></footer>
          </form>
        </MatchingModal>
      ) : null}
    </section>
  )
}
