import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Download, Filter, RefreshCw } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { formatShanghaiDate } from "../teaching/datetime"
import {
  deleteHousingRoom,
  exportHousingOccupancy,
  getHousingCatalog,
  listHousingOverview,
  normalizeHousingMultiSelects,
  updateHousingRoom,
  verifyHousingRoom,
} from "./api"
import type {
  HousingCatalog,
  HousingFiltersValue,
  HousingOccupancyExportInput,
  HousingRoom,
  HousingRoomUpdate,
  HousingSummary,
} from "./api"
import { HousingCatalogPanel } from "./HousingCatalogPanel"
import { DeleteDialog, isHousingRoomDestinationAllowed, RoomDialog } from "./HousingCatalogDialogs"
import { HousingFilters } from "./HousingFilters"
import { useHousingFilterState } from "./HousingFilterState"
import { HousingOccupancyExportDialog } from "./HousingOccupancyExportDialog"
import { housingKeys } from "./housingKeys"
import { RoomCard } from "./RoomCard"
import { MAX_HOUSING_BATCH_SELECTION, updateHousingSelection } from "./selection"
import { useHousingMutationCoordinator } from "./useHousingMutationCoordinator"

import "./housing.css"

const PAGE_SIZE = 20

function useMobile(): boolean {
  const [mobile, setMobile] = useState(() => window.innerWidth < 720)
  useEffect(() => {
    const update = () => setMobile(window.innerWidth < 720)
    window.addEventListener("resize", update)
    return () => window.removeEventListener("resize", update)
  }, [])
  return mobile
}

function normalizedFilters(value: HousingFiltersValue, page = 1): HousingFiltersValue {
  const normalizedValue = normalizeHousingMultiSelects(value)
  const normalized: HousingFiltersValue = {}
  if (normalizedValue.buildingId) normalized.buildingId = normalizedValue.buildingId
  if (normalizedValue.floorId) normalized.floorId = normalizedValue.floorId
  if (normalizedValue.query?.trim()) normalized.query = normalizedValue.query.trim()
  if (normalizedValue.roomTypes?.length) normalized.roomTypes = normalizedValue.roomTypes
  if (normalizedValue.verificationStatuses?.length) normalized.verificationStatuses = normalizedValue.verificationStatuses
  if (normalizedValue.serviceStatuses?.length) normalized.serviceStatuses = normalizedValue.serviceStatuses
  if (normalizedValue.currentGender) normalized.currentGender = normalizedValue.currentGender
  if (normalizedValue.occupancy) normalized.occupancy = normalizedValue.occupancy
  if (normalizedValue.assignability) normalized.assignability = normalizedValue.assignability
  normalized.page = page
  normalized.pageSize = PAGE_SIZE
  return normalized
}

function catalogNormalizedFilters(value: HousingFiltersValue, catalog: HousingCatalog): HousingFiltersValue {
  const normalized = normalizedFilters(value, value.page ?? 1)
  const building = normalized.buildingId
    ? catalog.buildings.find((item) => item.id === normalized.buildingId)
    : undefined
  if (normalized.buildingId && !building) {
    delete normalized.buildingId
    delete normalized.floorId
    return normalized
  }
  if (normalized.floorId) {
    const floor = catalog.floors.find((item) => item.id === normalized.floorId)
    if (!floor || (building && floor.building_id !== building.id)) delete normalized.floorId
  }
  return normalized
}

type SummaryItem = {
  key: keyof HousingSummary
  label: string
  suffix: string
  filter: HousingFiltersValue
  actionLabel: string
}

// Summary values are also shortcuts into the existing server-side room filters.
// Aggregate values that do not have a room-level predicate open the complete room
// list (or the closest meaningful view) rather than pretending to filter by a sum.
const SUMMARY_ITEMS: readonly SummaryItem[] = [
  { key: "total_rooms", label: "房间总数", suffix: "", filter: {}, actionLabel: "查看全部房间" },
  { key: "occupied_rooms", label: "已入住", suffix: "", filter: { occupancy: "occupied" }, actionLabel: "按已入住筛选房间" },
  { key: "unoccupied_rooms", label: "空房", suffix: "", filter: { occupancy: "unoccupied" }, actionLabel: "按空房筛选房间" },
  { key: "assignable_rooms", label: "可入住", suffix: "", filter: { assignability: "assignable" }, actionLabel: "按可入住筛选房间" },
  { key: "unassignable_rooms", label: "不可入住", suffix: "", filter: { assignability: "unassignable" }, actionLabel: "按不可入住筛选房间" },
  { key: "problem_rooms", label: "问题房", suffix: "", filter: { serviceStatuses: ["problem"] }, actionLabel: "按问题房筛选房间" },
  { key: "pending_verification_rooms", label: "待核对", suffix: "", filter: { verificationStatuses: ["pending_verification"] }, actionLabel: "按待核对筛选房间" },
  { key: "total_capacity", label: "总床位", suffix: "", filter: {}, actionLabel: "查看全部房间（总床位统计）" },
  { key: "current_occupants", label: "当前入住", suffix: "", filter: { occupancy: "occupied" }, actionLabel: "按已入住筛选房间（当前入住）" },
  { key: "remaining_capacity", label: "剩余床位", suffix: "", filter: { assignability: "assignable" }, actionLabel: "按可入住筛选房间（剩余床位）" },
  { key: "occupancy_rate", label: "入住率", suffix: "%", filter: {}, actionLabel: "查看全部房间（入住率统计）" },
  { key: "single_rooms", label: "单人间", suffix: "", filter: { roomTypes: ["single"] }, actionLabel: "按单人间筛选房间" },
  { key: "double_rooms", label: "双人间", suffix: "", filter: { roomTypes: ["double"] }, actionLabel: "按双人间筛选房间" },
  { key: "triple_rooms", label: "三人间", suffix: "", filter: { roomTypes: ["triple"] }, actionLabel: "按三人间筛选房间" },
  { key: "custom_rooms", label: "自定义房型", suffix: "", filter: { roomTypes: ["custom"] }, actionLabel: "按自定义房型筛选房间" },
  { key: "functional_rooms", label: "功能间", suffix: "", filter: { roomTypes: ["functional"] }, actionLabel: "按功能间筛选房间" },
]

function errorMessage(error: unknown): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : "暂时无法读取住宿房态"
}

function isPrivateError(error: unknown): boolean {
  return error instanceof ProblemDetailsError && (error.problem.status === 403 || error.problem.status === 404)
}

type RoomDialogState =
  | { type: "edit"; roomId: string; baseVersion: number }
  | { type: "delete"; roomId: string }

export type HousingOverviewPageProps = {
  permissions?: ReadonlySet<string>
  scopedPermissions?: ReadonlySet<string>
  onViewRoom?: (roomId: string) => void
}

export function HousingOverviewPage({ onViewRoom }: HousingOverviewPageProps = {}) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const savedFilterState = useHousingFilterState()
  const [initialFilterState] = useState(() => savedFilterState?.current ?? null)
  const accountId = user?.account_id ?? "anonymous"
  const pageFocusRef = useRef<HTMLElement>(null)
  const mobile = useMobile()
  const [filterOpen, setFilterOpen] = useState(false)
  const [filters, setFilters] = useState<HousingFiltersValue>(() => normalizedFilters(
    initialFilterState?.filters ?? {}, initialFilterState?.filters.page ?? 1,
  ))
  const [selected, setSelected] = useState<Map<string, number>>(() => new Map())
  const selectedRef = useRef<ReadonlyMap<string, number>>(selected)
  selectedRef.current = selected
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [roomDialog, setRoomDialog] = useState<RoomDialogState | null>(null)
  const mutationCoordinator = useHousingMutationCoordinator()
  const [roomMutationError, setRoomMutationError] = useState<string | null>(null)
  const [roomMutationNotice, setRoomMutationNotice] = useState<string | null>(null)
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const [exportPending, setExportPending] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  const [privateAccessError, setPrivateAccessError] = useState<unknown>(null)
  const catalogKey = housingKeys.catalog(accountId)
  const catalog = useQuery({
    queryKey: catalogKey,
    queryFn: getHousingCatalog,
    enabled: Boolean(user?.account_id) && privateAccessError === null,
  })
  const previousAccessRevisionRef = useRef<string | null>(initialFilterState?.accessRevision ?? null)
  const accessRevision = catalog.data?.access_revision ?? null
  const accessRevisionChanged = accessRevision !== null
    && previousAccessRevisionRef.current !== null
    && previousAccessRevisionRef.current !== accessRevision
  const effectiveFilters = useMemo(() => {
    if (accessRevisionChanged) return normalizedFilters({}, 1)
    return catalog.data
      ? catalogNormalizedFilters(filters, catalog.data)
      : normalizedFilters(filters, filters.page ?? 1)
  }, [accessRevisionChanged, catalog.data, filters])
  const overviewKey = housingKeys.overview(
    accountId,
    accessRevision ?? "pending",
    effectiveFilters,
  )
  const overview = useQuery({
    queryKey: overviewKey,
    queryFn: () => listHousingOverview(effectiveFilters),
    enabled: Boolean(user?.account_id) && privateAccessError === null && accessRevision !== null,
  })

  const closeFilters = useCallback((open: boolean) => setFilterOpen(open), [])
  const privateQueryError = [catalog.error, overview.error].find(isPrivateError) ?? null
  const privateFailure = privateAccessError !== null || privateQueryError !== null
  const handledPrivateFailureRef = useRef(false)
  const clientStateEpochRef = useRef(0)
  const overviewRevisionMatches = Boolean(
    accessRevision && overview.data?.access_revision === accessRevision,
  )
  const acceptedOverview = overviewRevisionMatches ? overview.data : undefined
  const revisionMismatch = Boolean(accessRevision && overview.data && !overviewRevisionMatches)
  const failure = privateAccessError ?? catalog.error ?? overview.error
  const refreshing = catalog.isFetching || overview.isFetching
  const loading = catalog.isPending || overview.isPending || revisionMismatch || (refreshing && !catalogOpen)
  const totalPages = acceptedOverview ? Math.max(1, Math.ceil(acceptedOverview.total / acceptedOverview.page_size)) : 1
  const currentPage = effectiveFilters.page ?? 1
  const clampedPage = Math.min(Math.max(currentPage, 1), totalPages)
  const pageOutOfRange = acceptedOverview !== undefined && currentPage !== clampedPage
  const filterValue = useMemo(() => {
    const value = { ...effectiveFilters }
    delete value.page
    delete value.pageSize
    return value
  }, [effectiveFilters])
  const hasManagementActions = Boolean(
    catalog.data && acceptedOverview && (
      catalog.data.can_create_building ||
      catalog.data.buildings.some((building) => building.allowed_actions.length > 0) ||
      catalog.data.floors.some((floor) => floor.allowed_actions.length > 0) ||
      acceptedOverview.rooms.some((room) => room.allowed_actions.edit || room.allowed_actions.delete || room.allowed_actions.verify)
    ),
  )
  const canExport = Boolean(
    user?.permissions.includes("housing.reports.export_sensitive") ||
      user?.scoped_permissions?.includes("housing.reports.export_sensitive"),
  )
  const roomDialogTarget = roomDialog
    ? acceptedOverview?.rooms.find((room) => room.id === roomDialog.roomId)
    : undefined
  const roomDialogAuthorized = Boolean(
    !privateFailure && roomDialogTarget && (
      (roomDialog?.type === "edit" && roomDialogTarget.allowed_actions.edit) ||
      (roomDialog?.type === "delete" && roomDialogTarget.allowed_actions.delete)
    ),
  )

  const clearPrivateHousingState = useCallback(() => {
    clientStateEpochRef.current += 1
    const empty = new Map<string, number>()
    selectedRef.current = empty
    setSelected(empty)
    setCatalogOpen(false)
    setRoomDialog(null)
    setRoomMutationError(null)
    setRoomMutationNotice(null)
    setSelectionNotice(null)
    setExportOpen(false)
    setExportError(null)
  }, [])

  const purgeCurrentAccountHousing = useCallback(() => {
    const root = housingKeys.root(accountId)
    const cancellation = queryClient.cancelQueries({ queryKey: root })
    queryClient.removeQueries({ queryKey: root })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey: root }))
  }, [accountId, queryClient])

  const enterPrivateFailure = useCallback((error: unknown) => {
    if (savedFilterState) savedFilterState.current = null
    if (!handledPrivateFailureRef.current) {
      handledPrivateFailureRef.current = true
      setPrivateAccessError(error)
      previousAccessRevisionRef.current = null
      clearPrivateHousingState()
    }
    purgeCurrentAccountHousing()
  }, [clearPrivateHousingState, purgeCurrentAccountHousing, savedFilterState])

  useEffect(() => {
    if (!savedFilterState) return
    if (privateFailure) {
      savedFilterState.current = null
    } else if (accessRevision && !revisionMismatch) {
      savedFilterState.current = { filters: effectiveFilters, accessRevision }
    }
  }, [accessRevision, effectiveFilters, privateFailure, revisionMismatch, savedFilterState])

  useEffect(() => () => {
    clientStateEpochRef.current += 1
  }, [])

  useEffect(() => {
    if (!mobile) setFilterOpen(false)
  }, [mobile])

  useEffect(() => {
    if (!pageOutOfRange) return
    setFilters((current) => {
      if ((current.page ?? 1) === clampedPage) return current
      return normalizedFilters(current, clampedPage)
    })
  }, [clampedPage, pageOutOfRange])

  useEffect(() => {
    if (!catalog.data || accessRevisionChanged) return
    setFilters((current) => {
      const normalized = catalogNormalizedFilters(current, catalog.data)
      return JSON.stringify(current) === JSON.stringify(normalized) ? current : normalized
    })
  }, [accessRevisionChanged, catalog.data])

  useEffect(() => {
    if (!acceptedOverview) return
    setSelected((current) => {
      const next = new Map(current)
      let changed = false
      for (const room of acceptedOverview.rooms) {
        if (!next.has(room.id)) continue
        if (room.verification_status !== "pending_verification" || !room.allowed_actions.verify) {
          next.delete(room.id)
          changed = true
        } else if (next.get(room.id) !== room.version) {
          next.set(room.id, room.version)
          changed = true
        }
      }
      return changed ? next : current
    })
  }, [acceptedOverview])

  useEffect(() => {
    if (!privateFailure) {
      handledPrivateFailureRef.current = false
      return
    }
    enterPrivateFailure(privateAccessError ?? privateQueryError)
  }, [enterPrivateFailure, privateAccessError, privateFailure, privateQueryError])

  useEffect(() => {
    if (!accessRevision || privateFailure) return
    const previousAccessRevision = previousAccessRevisionRef.current
    previousAccessRevisionRef.current = accessRevision
    if (previousAccessRevision === null || previousAccessRevision === accessRevision) return
    clearPrivateHousingState()
    setFilters(normalizedFilters({}, 1))
    const previousScope = housingKeys.scope(accountId, previousAccessRevision)
    const cancellation = queryClient.cancelQueries({ queryKey: previousScope })
    queryClient.removeQueries({ queryKey: previousScope })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey: previousScope }))
  }, [accessRevision, accountId, clearPrivateHousingState, privateFailure, queryClient])

  const handledRevisionMismatchRef = useRef<string | null>(null)
  useEffect(() => {
    if (!revisionMismatch || !accessRevision || !overview.data) {
      if (overview.data?.access_revision === accessRevision) {
        handledRevisionMismatchRef.current = null
      }
      return
    }
    const mismatch = `${accessRevision}:${overview.data.access_revision}`
    const mismatchedKey = housingKeys.overview(accountId, accessRevision, effectiveFilters)
    queryClient.setQueryData(mismatchedKey, null)
    if (handledRevisionMismatchRef.current === mismatch) return
    handledRevisionMismatchRef.current = mismatch
    clearPrivateHousingState()
    setFilters(normalizedFilters({}, 1))
    const clientStateEpoch = clientStateEpochRef.current
    const cancellation = queryClient.cancelQueries({ queryKey: mismatchedKey, exact: true })
    void cancellation.then(async () => {
      if (clientStateEpoch !== clientStateEpochRef.current) {
        queryClient.removeQueries({ queryKey: mismatchedKey, exact: true })
        return
      }
      await queryClient.invalidateQueries({ queryKey: catalogKey, exact: true })
      if (clientStateEpoch !== clientStateEpochRef.current) return
      await queryClient.invalidateQueries({ queryKey: housingKeys.overviews(accountId, accessRevision) })
    })
  }, [accessRevision, accountId, catalogKey, clearPrivateHousingState, effectiveFilters, overview.data, queryClient, revisionMismatch])

  useEffect(() => {
    if (selected.size < MAX_HOUSING_BATCH_SELECTION) setSelectionNotice(null)
  }, [selected.size])

  useEffect(() => {
    if (!roomDialog || mutationCoordinator.pending || roomDialogAuthorized) return
    setRoomDialog(null)
    setRoomMutationError(null)
  }, [mutationCoordinator.pending, roomDialog, roomDialogAuthorized])

  function applyFilters(value: HousingFiltersValue): void {
    if (selectedRef.current.size > 0) {
      const empty = new Map<string, number>()
      selectedRef.current = empty
      setSelected(empty)
    }
    setSelectionNotice(null)
    setFilters(normalizedFilters(value, 1))
  }

  function changeSelectedRoom(room: HousingRoom, checked: boolean): void {
    if (room.verification_status !== "pending_verification" || !room.allowed_actions.verify) return
    const result = updateHousingSelection(selectedRef.current, room.id, room.version, checked)
    setSelectionNotice(result.limited ? "跨页选择最多支持 500 间房，请先取消部分选择。" : null)
    if (result.next === selectedRef.current) return
    const next = new Map(result.next)
    selectedRef.current = next
    setSelected(next)
  }

  function replaceSelectedRooms(next: Map<string, number>): void {
    selectedRef.current = next
    setSelected(next)
  }

  function changePage(page: number): void {
    setFilters((current) => normalizedFilters(current, page))
  }

  async function refreshHousing(): Promise<void> {
    if (!accessRevision) return
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: catalogKey, exact: true }),
      queryClient.invalidateQueries({ queryKey: housingKeys.overviews(accountId, accessRevision) }),
    ])
  }

  async function downloadOccupancyExport(input: HousingOccupancyExportInput): Promise<void> {
    setExportPending(true)
    setExportError(null)
    try {
      const result = await exportHousingOccupancy(input)
      const url = URL.createObjectURL(result.blob)
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = result.filename
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 0)
      setExportOpen(false)
      setRoomMutationNotice("房态入住名单已导出")
    } catch (caught) {
      setExportError(caught instanceof ProblemDetailsError ? caught.problem.detail : "房态导出暂时失败")
      throw caught
    } finally {
      setExportPending(false)
    }
  }

  function retryHousingQueries(): void {
    const clientStateEpoch = clientStateEpochRef.current
    void Promise.all([catalog.refetch(), overview.refetch()]).then(([catalogResult, overviewResult]) => {
      if (clientStateEpoch !== clientStateEpochRef.current) return
      if (catalogResult.isSuccess && overviewResult.isSuccess) setPrivateAccessError(null)
    })
  }

  async function runRoomMutation(
    operation: () => Promise<unknown>,
    options: { closeOnSuccess?: boolean; deletedRoomId?: string } = {},
  ): Promise<void> {
    const mutationToken = mutationCoordinator.acquire()
    if (!mutationToken) return
    const clientStateEpoch = clientStateEpochRef.current
    setRoomMutationError(null)
    setRoomMutationNotice(null)
    try {
      await operation()
      if (clientStateEpoch !== clientStateEpochRef.current) return
      const deletedRoomId = options.deletedRoomId
      if (deletedRoomId) {
        setSelected((current) => {
          if (!current.has(deletedRoomId)) return current
          const next = new Map(current)
          next.delete(deletedRoomId)
          return next
        })
      }
      await refreshHousing()
      if (clientStateEpoch !== clientStateEpochRef.current) return
      if (options.closeOnSuccess !== false) setRoomDialog(null)
    } catch (caught) {
      if (clientStateEpoch !== clientStateEpochRef.current) return
      if (caught instanceof ProblemDetailsError && caught.problem.status === 409 && caught.problem.code === "housing_stale_version") {
        setSelected(new Map())
        setRoomMutationError("数据已更新，请查看当前数据后再重试。")
        setRoomMutationNotice(roomDialog ? null : "数据已更新，请查看当前数据后再重试。")
        await refreshHousing()
        if (clientStateEpoch !== clientStateEpochRef.current) return
      } else if (isPrivateError(caught)) {
        enterPrivateFailure(caught)
      } else {
        const message = caught instanceof ProblemDetailsError ? caught.problem.detail : "操作暂时失败，请稍后重试"
        setRoomMutationError(message)
        setRoomMutationNotice(roomDialog ? null : message)
      }
    } finally {
      mutationCoordinator.release(mutationToken)
    }
  }

  function openRoomDialog(type: RoomDialogState["type"], room: HousingRoom): void {
    setRoomMutationError(null)
    setRoomMutationNotice(null)
    setRoomDialog(type === "edit"
      ? { type, roomId: room.id, baseVersion: room.version }
      : { type, roomId: room.id })
  }

  function closeRoomDialog(): void {
    if (mutationCoordinator.isPending()) return
    setRoomDialog(null)
    setRoomMutationError(null)
  }

  function submitRoomEdit(input: HousingRoomUpdate): void {
    if (roomDialog?.type !== "edit") return
    const room = acceptedOverview?.rooms.find((item) => item.id === roomDialog.roomId)
    if (privateFailure || !room?.allowed_actions.edit || (input.floor_id && (!catalog.data || !isHousingRoomDestinationAllowed(catalog.data, input.floor_id)))) return
    void runRoomMutation(() => updateHousingRoom(room.id, { ...input, version: roomDialog.baseVersion }))
  }

  function verifyRoom(room: HousingRoom): void {
    const current = acceptedOverview?.rooms.find((item) => item.id === room.id)
    if (privateFailure || current?.verification_status !== "pending_verification" || !current.allowed_actions.verify) return
    void runRoomMutation(() => verifyHousingRoom(current.id, current.version), { closeOnSuccess: false })
  }

  function confirmRoomDelete(): void {
    if (roomDialog?.type !== "delete") return
    const room = acceptedOverview?.rooms.find((item) => item.id === roomDialog.roomId)
    if (privateFailure || !room?.allowed_actions.delete) return
    void runRoomMutation(() => deleteHousingRoom(room.id), { deletedRoomId: room.id })
  }

  return (
    <section ref={pageFocusRef} className="housingPage" aria-labelledby="housing-overview-title" data-housing-dialog-owner tabIndex={-1}>
      <header className="housingPage__header">
        <div><p className="pageEyebrow">HOUSING</p><h1 id="housing-overview-title">住宿房态</h1><p>按楼栋、楼层与入住状态查看房间</p></div>
        <div className="housingPage__headerActions">
          {canExport && !privateFailure ? (
            <button
              type="button"
              title="导出房态入住名单"
              disabled={exportPending || mutationCoordinator.pending}
              onClick={() => {
                setExportError(null)
                setExportOpen(true)
              }}
            >
              <Download aria-hidden="true" />导出
            </button>
          ) : null}
          {hasManagementActions && !privateFailure ? <button type="button" data-housing-management-trigger disabled={mutationCoordinator.pending} onClick={() => setCatalogOpen((open) => !open)}>目录管理</button> : null}
          {mobile && !privateFailure ? (
            <button type="button" className="housingPage__filterTrigger" aria-label="打开筛选" onClick={() => setFilterOpen(true)}>
              <Filter aria-hidden="true" />筛选
            </button>
          ) : null}
        </div>
      </header>

      {failure ? (
        <section className="housingState housingState--error" role="alert">
          <div><strong>{privateFailure ? "房态数据不可访问" : "房态数据读取失败"}</strong><p>{errorMessage(failure)}</p></div>
          <button type="button" onClick={retryHousingQueries}><RefreshCw aria-hidden="true" />重试</button>
        </section>
      ) : loading || pageOutOfRange || !catalog.data || !acceptedOverview ? (
        <section className="housingState" aria-live="polite"><span className="housingState__pulse" /><p>正在读取房态数据</p></section>
      ) : (
        <div className="housingPage__layout">
          <HousingFilters
            catalog={catalog.data}
            value={filterValue}
            open={filterOpen}
            onOpenChange={closeFilters}
            onApply={applyFilters}
            onReset={() => applyFilters({})}
          />
          <div className="housingPage__content">
            {roomMutationNotice ? <p className="housingCatalog__notice" role="alert">{roomMutationNotice}</p> : null}
            {selectionNotice ? <p className="housingCatalog__notice" role="alert">{selectionNotice}</p> : null}
            {catalogOpen && hasManagementActions ? <HousingCatalogPanel accountId={accountId} accessRevision={catalog.data.access_revision} catalog={catalog.data} rooms={acceptedOverview.rooms} pending={refreshing} selectedRooms={selected} onSelectedRoomsChange={replaceSelectedRooms} mutationCoordinator={mutationCoordinator} clientStateEpochRef={clientStateEpochRef} focusFallbackRef={pageFocusRef} onPrivateFailure={enterPrivateFailure} /> : null}
            <section className="housingSummary" aria-label="住宿汇总">
              {SUMMARY_ITEMS.map(({ key, label, suffix, filter, actionLabel }) => (
                <button
                  key={key}
                  type="button"
                  className="housingSummary__item"
                  aria-label={actionLabel}
                  aria-describedby={`housing-summary-${key}`}
                  title={actionLabel}
                  onClick={() => applyFilters(filter)}
                >
                  <span id={`housing-summary-${key}`}>{label} {acceptedOverview.summary[key]}{suffix}</span>
                </button>
              ))}
            </section>
            <div className="housingPage__meta">
              <strong>筛选结果 {acceptedOverview.total} 间</strong>
              <span>数据截至 {formatShanghaiDate(acceptedOverview.as_of)}</span>
            </div>
            {acceptedOverview.rooms.length === 0 ? (
              <section className="housingState housingState--empty"><h2>没有匹配的房间</h2><p>请调整筛选条件后重试。</p></section>
            ) : (
              <ul className="housingRoomGrid" aria-label="房间列表">
                {acceptedOverview.rooms.map((room) => <li key={room.id}><RoomCard room={room} selected={selected.has(room.id)} pending={mutationCoordinator.pending} onSelectedChange={(checked) => changeSelectedRoom(room, checked)} onViewDetails={(target) => onViewRoom?.(target.id)} onEdit={(target) => openRoomDialog("edit", target)} onVerify={verifyRoom} onDelete={(target) => openRoomDialog("delete", target)} /></li>)}
              </ul>
            )}
            <nav className="housingPagination" aria-label="房态分页">
              <button type="button" disabled={currentPage <= 1} onClick={() => changePage(Math.max(1, currentPage - 1))}>上一页</button>
              <span>第 {currentPage} / {totalPages} 页</span>
              <button type="button" disabled={currentPage >= totalPages} onClick={() => changePage(currentPage + 1)}>下一页</button>
            </nav>
          </div>
        </div>
      )}
      {catalog.data && roomDialog?.type === "edit" && roomDialogAuthorized && roomDialogTarget ? <RoomDialog catalog={catalog.data} room={roomDialogTarget} pending={mutationCoordinator.pending} error={roomMutationError} focusFallbackRef={pageFocusRef} onClose={closeRoomDialog} onSubmit={(input) => submitRoomEdit(input as HousingRoomUpdate)} /> : null}
      {roomDialog?.type === "delete" && roomDialogAuthorized && roomDialogTarget ? <DeleteDialog kind="房间" name={roomDialogTarget.room_number} pending={mutationCoordinator.pending} error={roomMutationError} focusFallbackRef={pageFocusRef} onClose={closeRoomDialog} onConfirm={confirmRoomDelete} /> : null}
      {exportOpen && canExport && !privateFailure ? (
        <HousingOccupancyExportDialog
          filters={filterValue}
          pending={exportPending}
          error={exportError}
          focusFallbackRef={pageFocusRef}
          onClose={() => {
            if (!exportPending) {
              setExportOpen(false)
              setExportError(null)
            }
          }}
          onSubmit={downloadOccupancyExport}
        />
      ) : null}
    </section>
  )
}
