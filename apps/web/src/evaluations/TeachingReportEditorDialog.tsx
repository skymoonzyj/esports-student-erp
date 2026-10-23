import { useMutation } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { EvaluationDialogShell } from "./EvaluationDialogShell"
import {
  captureTeachingReportWrite,
  saveTeachingReportDraft,
  startTeachingReportCorrection,
  startTeachingReportRevision,
  submitTeachingReport,
  type TeachingReportSlot,
  type TeachingReportWrite,
} from "./reportApi"
import { reportKeys } from "./reportKeys"

export type TeachingReportEditorDialogProps = {
  slot: TeachingReportSlot
  onClose: () => void
  onChanged: (reportId: string) => Promise<void> | void
  onConflict: (reportId: string) => Promise<void> | void
  onUnavailable: (reportId: string) => Promise<void> | void
  authoritativeReady?: boolean
  idempotencyKeyFactory?: () => string
  reportIdFactory?: () => string
}

type EditorOperation =
  | "save_draft"
  | "submit"
  | "start_correction"
  | "start_revision"

type RetryAttempt = {
  operation: EditorOperation
  slotKey: string
  reportId: string
  idempotencyKey: string
  expectedLockVersion: number | null
  expectedDraftRevision: number
}

type EditorMutationVariables = {
  operation: EditorOperation
  reportId: string
  idempotencyKey: string
  write: TeachingReportWrite
}

function defaultId(): string {
  return crypto.randomUUID()
}

function typedErrorMessage(error: ProblemDetailsError): string {
  if (error.problem.status === 409) {
    return "评语状态已变化，请刷新并比较后重试。"
  }
  if (error.problem.status === 422) {
    return "提交内容无效，请检查后重试。"
  }
  if (error.problem.status === 403 || error.problem.status === 404) {
    return "当前评语已不可操作，请刷新后重试。"
  }
  return "操作暂时无法完成，请重试。"
}

export function TeachingReportEditorDialog({
  slot,
  onClose,
  onChanged,
  onConflict,
  onUnavailable,
  authoritativeReady = true,
  idempotencyKeyFactory = defaultId,
  reportIdFactory = defaultId,
}: TeachingReportEditorDialogProps) {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const attemptRef = useRef<RetryAttempt | null>(null)
  const [dirty, setDirty] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [uncertainOperation, setUncertainOperation] =
    useState<EditorOperation | null>(null)
  const [conflictDetected, setConflictDetected] = useState(false)
  const slotIdentityKey = [
    slot.enrollment_id,
    slot.report_kind,
    slot.period_start_date,
  ].join(":")
  const retrySlotKey = [
    slot.report_id ?? "new",
    slot.status ?? "empty",
    slot.lock_version ?? 0,
    slot.draft_revision ?? 0,
  ].join(":")

  useEffect(() => {
    attemptRef.current = null
    setDirty(false)
    setMessage(null)
    setUncertainOperation(null)
    setConflictDetected(false)
  }, [slotIdentityKey])

  const mutation = useMutation<
    { report_id: string },
    unknown,
    EditorMutationVariables
  >({
    mutationKey: [...reportKeys.root(accountId), "mutation", "author"],
    mutationFn: ({ operation, reportId, idempotencyKey, write }) => {
      if (operation === "save_draft") {
        return saveTeachingReportDraft(reportId, write, idempotencyKey)
      }
      if (operation === "submit") {
        return submitTeachingReport(reportId, write, idempotencyKey)
      }
      if (operation === "start_correction") {
        return startTeachingReportCorrection(reportId, write, idempotencyKey)
      }
      return startTeachingReportRevision(reportId, write, idempotencyKey)
    },
    gcTime: 0,
  })

  function retryAttempt(operation: EditorOperation): RetryAttempt {
    const current = attemptRef.current
    if (
      current?.operation === operation &&
      (current.slotKey === retrySlotKey || uncertainOperation === operation)
    ) {
      return current
    }
    const attempt = {
      operation,
      slotKey: retrySlotKey,
      reportId: slot.report_id ?? reportIdFactory(),
      idempotencyKey: idempotencyKeyFactory(),
      expectedLockVersion: slot.lock_version,
      expectedDraftRevision: slot.draft_revision ?? 0,
    }
    attemptRef.current = attempt
    return attempt
  }

  function writeFor(
    operation: EditorOperation,
    attempt: RetryAttempt | null,
  ): TeachingReportWrite | null {
    if (operation === "save_draft") {
      const body = textareaRef.current?.value.trim() ?? ""
      if (!body) {
        setMessage("请填写评语正文。")
        return null
      }
      return captureTeachingReportWrite({
        kind: "draft",
        enrollment_id: slot.enrollment_id,
        report_kind: slot.report_kind,
        period_start_date: slot.period_start_date,
        body,
        dimension_values: {},
        expected_draft_revision: attempt
          ? attempt.expectedDraftRevision
          : slot.draft_revision ?? 0,
      })
    }
    if (operation === "submit") {
      const lockVersion = attempt
        ? attempt.expectedLockVersion
        : slot.lock_version
      const draftRevision = attempt
        ? attempt.expectedDraftRevision
        : slot.draft_revision
      if (lockVersion === null || draftRevision === null) return null
      return captureTeachingReportWrite({
        kind: "submit",
        expected_lock_version: lockVersion,
        expected_draft_revision: draftRevision,
      })
    }
    const lockVersion = attempt ? attempt.expectedLockVersion : slot.lock_version
    if (lockVersion === null) return null
    return captureTeachingReportWrite({
      kind: operation === "start_correction" ? "correction" : "revision",
      expected_lock_version: lockVersion,
    })
  }

  async function perform(operation: EditorOperation): Promise<void> {
    if (
      mutation.isPending ||
      !authoritativeReady ||
      (uncertainOperation !== null && uncertainOperation !== operation)
    ) {
      return
    }
    const write = writeFor(operation, attemptRef.current)
    if (!write) return
    const attempt = retryAttempt(operation)
    setMessage(null)

    let result: { report_id: string }
    try {
      result = await mutation.mutateAsync({
        operation,
        reportId: attempt.reportId,
        idempotencyKey: attempt.idempotencyKey,
        write,
      })
    } catch (error) {
      if (error instanceof ProblemDetailsError) {
        attemptRef.current = null
        setUncertainOperation(null)
        if (
          error.problem.status === 404 &&
          error.problem.code === "evaluation_resource_not_found"
        ) {
          mutation.reset()
          try {
            await onUnavailable(attempt.reportId)
          } catch {
            setMessage("当前评语已不可操作，请刷新后重试。")
          }
          return
        }
        setMessage(typedErrorMessage(error))
        if (error.problem.status === 409) {
          setConflictDetected(true)
          try {
            await onConflict(attempt.reportId)
          } catch {
            // Keep the local body visible even when the authoritative refresh fails.
          }
        }
      } else {
        setUncertainOperation(operation)
        setMessage("网络结果未知，请重试；本次请求标识将保持不变。")
      }
      mutation.reset()
      return
    }

    attemptRef.current = null
    setUncertainOperation(null)
    setConflictDetected(false)
    mutation.reset()
    try {
      await onChanged(result.report_id)
    } catch {
      setMessage("操作已成功，但页面刷新失败，请手动刷新。")
      return
    }
    onClose()
  }

  function close(): void {
    attemptRef.current = null
    setUncertainOperation(null)
    setConflictDetected(false)
    onClose()
  }

  const editable =
    slot.allowed_actions.includes("save_draft") ||
    slot.allowed_actions.includes("submit")
  const showBody = editable || conflictDetected

  return (
    <EvaluationDialogShell
      title="编辑教学评语"
      closeLabel="关闭教学评语编辑"
      pending={mutation.isPending}
      onClose={close}
    >
      <div className="teachingReportEditorMeta">
        <p>{slot.cohort_name} · {slot.project_name} · {slot.class_name}</p>
        <p>{slot.report_kind === "daily" ? "日报" : "周报"} · 第 {slot.period_sequence} 期</p>
      </div>

      {showBody ? (
        <label className="teachingReportEditorBody">
          <span>评语正文</span>
          <textarea
            key={slotIdentityKey}
            ref={textareaRef}
            data-autofocus
            defaultValue={slot.draft_body ?? ""}
            maxLength={5000}
            disabled={mutation.isPending}
            readOnly={
              !authoritativeReady || uncertainOperation !== null || !editable
            }
            onInput={() => {
              setDirty(true)
              setMessage(null)
            }}
          />
        </label>
      ) : null}

      {dirty && slot.allowed_actions.includes("submit") ? (
        <p role="status">请先保存草稿</p>
      ) : null}
      {message ? <p role="alert">{message}</p> : null}

      <div className="teachingReportEditorActions">
        <button type="button" disabled={mutation.isPending} onClick={close}>
          取消
        </button>
        {slot.allowed_actions.includes("save_draft") ? (
          <button
            type="button"
            disabled={
              mutation.isPending ||
              !authoritativeReady ||
              (uncertainOperation !== null && uncertainOperation !== "save_draft")
            }
            onClick={() => void perform("save_draft")}
          >
            保存草稿
          </button>
        ) : null}
        {slot.allowed_actions.includes("submit") ? (
          <button
            type="button"
            disabled={
              dirty ||
              mutation.isPending ||
              !authoritativeReady ||
              (uncertainOperation !== null && uncertainOperation !== "submit")
            }
            onClick={() => void perform("submit")}
          >
            提交审核
          </button>
        ) : null}
        {slot.allowed_actions.includes("start_correction") ? (
          <button
            type="button"
            disabled={
              mutation.isPending ||
              !authoritativeReady ||
              (uncertainOperation !== null &&
                uncertainOperation !== "start_correction")
            }
            onClick={() => void perform("start_correction")}
          >
            开始整改
          </button>
        ) : null}
        {slot.allowed_actions.includes("start_revision") ? (
          <button
            type="button"
            disabled={
              mutation.isPending ||
              !authoritativeReady ||
              (uncertainOperation !== null &&
                uncertainOperation !== "start_revision")
            }
            onClick={() => void perform("start_revision")}
          >
            发起修订
          </button>
        ) : null}
      </div>
    </EvaluationDialogShell>
  )
}
