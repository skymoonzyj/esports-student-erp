import { useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useState } from "react"
import type { ReactNode, RefObject } from "react"

import { ProblemDetailsError } from "../api/client"
import {
  cancelHousingReservation,
  confirmHousingCheckIn,
  confirmHousingCheckout,
  createHousingStay,
  reportHousingIssue,
  requestHousingCheckout,
  resolveHousingIssue,
  transferHousingStay,
  updateHousingStay,
} from "./api"
import type {
  HousingCancelReservation,
  HousingCheckoutConfirmation,
  HousingCheckoutRequest,
  HousingConfirmCheckIn,
  HousingRoomDetail,
  HousingRoomIssue,
  HousingRoomIssueCreate,
  HousingRoomIssueResolve,
  HousingStay,
  HousingStayCreate,
  HousingStayUpdate,
  HousingTransferStay,
} from "./api"
import {
  CancelReservationDialog,
  ConfirmCheckInDialog,
  ConfirmCheckoutDialog,
  EditReservationDialog,
  RequestCheckoutDialog,
  TransferStayDialog,
} from "./HousingStayActionDialogs"
import { HousingStayDialog } from "./HousingStayDialog"
import { ReportIssueDialog, ResolveIssueDialog } from "./HousingIssueDialogs"
import { housingKeys } from "./housingKeys"
import { useHousingMutationCoordinator } from "./useHousingMutationCoordinator"

export type StayActionKind = keyof HousingStay["allowed_actions"]

export const STAY_ACTION_LABELS: Record<StayActionKind, string> = {
  edit_reservation: "编辑预约",
  confirm_check_in: "确认入住",
  cancel_reservation: "取消预约",
  transfer: "调房",
  request_checkout: "申请退房",
  confirm_checkout: "二次确认退房",
}

type DetailDialog =
  | { type: "create-stay"; roomId: string }
  | { type: StayActionKind; roomId: string; stayId: string; baseVersion: number }
  | { type: "report-issue"; roomId: string; baseRoomVersion: number }
  | { type: "resolve-issue"; roomId: string; issueId: string; baseRoomVersion: number }

function isPrivateFailure(error: unknown): boolean {
  return error instanceof ProblemDetailsError && (error.problem.status === 403 || error.problem.status === 404)
}

export function useHousingRoomLifecycle({
  accountId,
  accessRevision,
  detail,
  accessActive,
  clientStateEpochRef,
  focusFallbackRef,
  onPrivateFailure,
  onAccessRevisionMismatch,
}: {
  accountId: string
  accessRevision: string
  detail: HousingRoomDetail | undefined
  accessActive: boolean
  clientStateEpochRef: RefObject<number>
  focusFallbackRef: RefObject<HTMLElement | null>
  onPrivateFailure: (error: unknown) => void
  onAccessRevisionMismatch: () => void
}): {
  pending: boolean
  openCreateStay: () => void
  openStayAction: (type: StayActionKind, stay: HousingStay) => void
  openReportIssue: () => void
  openResolveIssue: (issue: HousingRoomIssue) => void
  dialogs: ReactNode
} {
  const queryClient = useQueryClient()
  const coordinator = useHousingMutationCoordinator()
  const [dialog, setDialog] = useState<DetailDialog | null>(null)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const room = detail?.room
  const allStays = detail
    ? [...detail.current_stays, ...detail.future_reservations, ...detail.history]
    : []
  const dialogStay = dialog && "stayId" in dialog
    ? allStays.find((stay) => stay.id === dialog.stayId)
    : undefined
  const dialogIssue = dialog?.type === "resolve-issue"
    ? detail?.issues.find((issue) => issue.id === dialog.issueId)
    : undefined
  const dialogAuthorized = (() => {
    if (!accessActive || !dialog || !detail) return false
    if (dialog.type === "create-stay") return detail.room.allowed_actions.create_stay
    if (dialog.type === "report-issue") return detail.room.allowed_actions.report_issue
    if (dialog.type === "resolve-issue") return Boolean(dialogIssue?.allowed_actions.resolve)
    return Boolean(dialogStay?.allowed_actions[dialog.type])
  })()
  const dialogStayWithOpeningVersion = dialogStay && dialog && "baseVersion" in dialog
    ? { ...dialogStay, version: dialog.baseVersion }
    : undefined

  useEffect(() => {
    if (!dialog || coordinator.pending || dialogAuthorized) return
    setDialog(null)
    setDialogError(null)
  }, [coordinator.pending, dialog, dialogAuthorized])

  const closeDialog = useCallback(() => {
    if (coordinator.isPending()) return
    setDialog(null)
    setDialogError(null)
  }, [coordinator])

  const handleDialogPrivateFailure = useCallback((error: unknown) => {
    if (isPrivateFailure(error)) onPrivateFailure(error)
  }, [onPrivateFailure])

  async function refreshAfterMutation(options: { roomIds: readonly string[]; catalog?: boolean }): Promise<void> {
    const roomIds = [...new Set(options.roomIds)]
    const scope = housingKeys.scope(accountId, accessRevision)
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: housingKeys.overviews(accountId, accessRevision) }),
      queryClient.invalidateQueries({ queryKey: [...scope, "transfer-options"] }),
      ...roomIds.map((affectedRoomId) => queryClient.invalidateQueries({
        queryKey: housingKeys.detail(accountId, accessRevision, affectedRoomId),
        exact: true,
      })),
      ...(options.catalog ? [
        queryClient.invalidateQueries({ queryKey: housingKeys.catalog(accountId), exact: true }),
      ] : []),
    ])
  }

  async function runMutation<T>(
    operation: () => Promise<T>,
    options: { roomIds: readonly string[]; catalog?: boolean; onSuccess?: (result: T) => void },
  ): Promise<void> {
    const token = coordinator.acquire()
    if (!token) return
    const epoch = clientStateEpochRef.current
    const stateIsCurrent = () => epoch === clientStateEpochRef.current
    setDialogError(null)
    try {
      const result = await operation()
      if (!stateIsCurrent()) return
      await refreshAfterMutation(options)
      if (!stateIsCurrent()) return
      options.onSuccess?.(result)
      if (!options.onSuccess) setDialog(null)
    } catch (caught) {
      if (!stateIsCurrent()) return
      if (isPrivateFailure(caught)) {
        onPrivateFailure(caught)
        return
      }
      if (caught instanceof ProblemDetailsError && caught.problem.status === 409) {
        setDialogError(caught.problem.code === "housing_stale_version"
          ? "数据已更新，请查看当前数据后再重试。"
          : caught.problem.detail)
        await refreshAfterMutation(options)
        return
      }
      setDialogError(caught instanceof ProblemDetailsError ? caught.problem.detail : "操作暂时失败，请稍后重试")
    } finally {
      coordinator.release(token)
    }
  }

  function submitCreateStay(input: HousingStayCreate): void {
    if (dialog?.type !== "create-stay" || !dialogAuthorized) return
    void runMutation(() => createHousingStay(dialog.roomId, input), { roomIds: [dialog.roomId] })
  }

  function submitReservation(input: HousingStayUpdate): void {
    if (dialog?.type !== "edit_reservation" || !dialogAuthorized) return
    void runMutation(() => updateHousingStay(dialog.stayId, input), { roomIds: [dialog.roomId] })
  }

  function submitCheckIn(input: HousingConfirmCheckIn): void {
    if (dialog?.type !== "confirm_check_in" || !dialogAuthorized) return
    void runMutation(() => confirmHousingCheckIn(dialog.stayId, input), { roomIds: [dialog.roomId] })
  }

  function submitCancellation(input: HousingCancelReservation): void {
    if (dialog?.type !== "cancel_reservation" || !dialogAuthorized) return
    void runMutation(() => cancelHousingReservation(dialog.stayId, input), { roomIds: [dialog.roomId] })
  }

  function submitTransfer(input: HousingTransferStay): void {
    if (dialog?.type !== "transfer" || !dialogAuthorized) return
    void runMutation(() => transferHousingStay(dialog.stayId, input), { roomIds: [dialog.roomId, input.target_room_id] })
  }

  function submitCheckoutRequest(input: HousingCheckoutRequest): void {
    if (dialog?.type !== "request_checkout" || !dialogAuthorized) return
    const sourceRoomId = dialog.roomId
    void runMutation(() => requestHousingCheckout(dialog.stayId, input), {
      roomIds: [sourceRoomId],
      onSuccess: (result) => setDialog({
        type: "confirm_checkout",
        roomId: result.room_id,
        stayId: result.id,
        baseVersion: result.version,
      }),
    })
  }

  function submitCheckoutConfirmation(input: HousingCheckoutConfirmation): void {
    if (dialog?.type !== "confirm_checkout" || !dialogAuthorized) return
    void runMutation(() => confirmHousingCheckout(dialog.stayId, input), { roomIds: [dialog.roomId] })
  }

  function submitIssue(input: HousingRoomIssueCreate): void {
    if (dialog?.type !== "report-issue" || !dialogAuthorized) return
    void runMutation(() => reportHousingIssue(dialog.roomId, input), { roomIds: [dialog.roomId], catalog: true })
  }

  function submitIssueResolution(input: HousingRoomIssueResolve): void {
    if (dialog?.type !== "resolve-issue" || !dialogAuthorized) return
    void runMutation(() => resolveHousingIssue(dialog.issueId, input), { roomIds: [dialog.roomId], catalog: true })
  }

  const dialogs = room ? (
    <>
      {dialog?.type === "create-stay" ? <HousingStayDialog accountId={accountId} accessRevision={accessRevision} room={room} pending={coordinator.pending} error={dialogError} focusFallbackRef={focusFallbackRef} now={new Date(detail.as_of)} onClose={closeDialog} onSubmit={submitCreateStay} onAccessRevisionMismatch={onAccessRevisionMismatch} onPrivateFailure={handleDialogPrivateFailure} /> : null}
      {dialog?.type === "edit_reservation" && dialogStayWithOpeningVersion ? <EditReservationDialog stay={dialogStayWithOpeningVersion} pending={coordinator.pending} error={dialogError} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={submitReservation} /> : null}
      {dialog?.type === "confirm_check_in" && dialogStayWithOpeningVersion ? <ConfirmCheckInDialog stay={dialogStayWithOpeningVersion} pending={coordinator.pending} error={dialogError} defaultTime={detail.as_of} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={submitCheckIn} /> : null}
      {dialog?.type === "cancel_reservation" && dialogStayWithOpeningVersion ? <CancelReservationDialog stay={dialogStayWithOpeningVersion} pending={coordinator.pending} error={dialogError} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={submitCancellation} /> : null}
      {dialog?.type === "transfer" && dialogStayWithOpeningVersion ? <TransferStayDialog accountId={accountId} accessRevision={accessRevision} stay={dialogStayWithOpeningVersion} pending={coordinator.pending} error={dialogError} defaultTime={detail.as_of} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={submitTransfer} onAccessRevisionMismatch={onAccessRevisionMismatch} onPrivateFailure={handleDialogPrivateFailure} /> : null}
      {dialog?.type === "request_checkout" && dialogStayWithOpeningVersion ? <RequestCheckoutDialog stay={dialogStayWithOpeningVersion} pending={coordinator.pending} error={dialogError} defaultTime={detail.as_of} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={submitCheckoutRequest} /> : null}
      {dialog?.type === "confirm_checkout" && dialogStayWithOpeningVersion ? <ConfirmCheckoutDialog stay={dialogStayWithOpeningVersion} pending={coordinator.pending} error={dialogError} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={submitCheckoutConfirmation} /> : null}
      {dialog?.type === "report-issue" ? <ReportIssueDialog roomNumber={room.room_number} roomVersion={dialog.baseRoomVersion} pending={coordinator.pending} error={dialogError} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={submitIssue} /> : null}
      {dialog?.type === "resolve-issue" && dialogIssue ? <ResolveIssueDialog roomNumber={room.room_number} roomVersion={dialog.baseRoomVersion} issue={dialogIssue} pending={coordinator.pending} error={dialogError} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={submitIssueResolution} /> : null}
    </>
  ) : null

  return {
    pending: coordinator.pending,
    openCreateStay: () => {
      if (!room?.allowed_actions.create_stay) return
      setDialogError(null)
      setDialog({ type: "create-stay", roomId: room.id })
    },
    openStayAction: (type, stay) => {
      if (!stay.allowed_actions[type]) return
      setDialogError(null)
      setDialog({ type, roomId: stay.room_id, stayId: stay.id, baseVersion: stay.version })
    },
    openReportIssue: () => {
      if (!room?.allowed_actions.report_issue) return
      setDialogError(null)
      setDialog({ type: "report-issue", roomId: room.id, baseRoomVersion: room.version })
    },
    openResolveIssue: (issue) => {
      if (!room || !issue.allowed_actions.resolve) return
      setDialogError(null)
      setDialog({ type: "resolve-issue", roomId: room.id, issueId: issue.id, baseRoomVersion: room.version })
    },
    dialogs,
  }
}
