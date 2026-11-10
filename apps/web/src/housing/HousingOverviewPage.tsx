import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Download, Filter, RefreshCw } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { personHousingKeys } from "../people/housingApi"
import { useAuth } from "../auth/AuthProvider"
import { formatShanghaiDate } from "../teaching/datetime"
import {
  deleteHousingRoom,
  completeRoomCleaning,
  exportHousingOccupancy,
  exportHousingRoomStatus,
  getHousingCatalog,
  listHousingOverview,
  normalizeHousingMultiSelects,
  updateHousingRoom,
  updateHousingRoomsServiceStatus,
  verifyHousingRoom,
  verifyHousingRooms,
} from "./api"
import type {
  HousingCatalog,
  HousingFiltersValue,
  HousingOccupancyExportInput,
  HousingRoomStatusExportInput,
  HousingRoom,
  HousingRoomUpdate,
  HousingSummary,
  HousingQuickFilter,
} from "./api"
import { HousingCatalogPanel } from "./HousingCatalogPanel"
import { HousingBulkActions, HousingBulkConfirmation } from "./HousingBulkActions"
import { DeleteDialog, isHousingRoomDestinationAllowed, RoomDialog } from "./HousingCatalogDialogs"
import { HousingFilters } from "./HousingFilters"
import { HousingCleaningDialog } from "./HousingCleaningDialog"
import { useHousingFilterState } from "./HousingFilterState"
import { HousingExportDialog } from "./HousingExportDialog"
import { HousingPropertyExportsPanel } from "./HousingPropertyExportsPanel"
import { housingKeys } from "./housingKeys"
import { RoomCard } from "./RoomCard"
import { housingRoomSelection, housingSelectionProblems, isHousingRoomSelectable, MAX_HOUSING_BATCH_SELECTION, updateHousingSelection } from "./selection"
import type { HousingBulkAction, HousingRoomSelection } from "./selection"
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
  if (normalizedValue.cleaningStatuses?.length) normalized.cleaningStatuses = normalizedValue.cleaningStatuses
  if (normalizedValue.cleaningFrequencies?.length) normalized.cleaningFrequencies = normalizedValue.cleaningFrequencies
  if (normalizedValue.dailyCleaningEnabled !== undefined) normalized.dailyCleaningEnabled = normalizedValue.dailyCleaningEnabled
  if (normalizedValue.currentGender) normalized.currentGender = normalizedValue.currentGender
  if (normalizedValue.occupancy) normalized.occupancy = normalizedValue.occupancy
  if (normalizedValue.assignability) normalized.assignability = normalizedValue.assignability
  if (normalizedValue.quickFilter) normalized.quickFilter = normalizedValue.quickFilter
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
  id: string
  key: keyof HousingSummary
  label: string
  suffix: string
  quickFilter?: HousingQuickFilter
  actionLabel: string
}

// Shortcuts intersect the base sidebar scope; they never change that scope.
const SUMMARY_ITEMS: readonly SummaryItem[] = [
  { id: "total", key: "total_rooms", label: "房间总数", suffix: "", actionLabel: "查看全部房间" },
  { id: "occupied", key: "occupied_rooms", label: "已用房间数", suffix: "", quickFilter: "occupied", actionLabel: "按已用房间数筛选房间" },
  { id: "vacant", key: "unoccupied_rooms", label: "空房", suffix: "", quickFilter: "vacant", actionLabel: "按空房筛选房间" },
  { id: "assignable", key: "assignable_rooms", label: "可入住", suffix: "", quickFilter: "assignable", actionLabel: "按可入住筛选房间" },
  { id: "full", key: "full_rooms", label: "满房", suffix: "", quickFilter: "full", actionLabel: "按满房筛选房间" },
  { id: "problem", key: "problem_rooms", label: "问题房", suffix: "", quickFilter: "problem", actionLabel: "按问题房筛选房间" },
  { id: "verification", key: "pending_verification_rooms", label: "待核对", suffix: "", quickFilter: "pending_verification", actionLabel: "按待核对筛选房间" },
  { id: "cleaning", key: "pending_cleaning_rooms", label: "退房待保洁", suffix: "", quickFilter: "pending_cleaning", actionLabel: "按退房待保洁筛选房间" },
  { id: "cleaning-daily", key: "daily_cleaning_rooms", label: "每日保洁", suffix: "", quickFilter: "cleaning_daily", actionLabel: "按每日保洁筛选房间" },
  { id: "cleaning-weekly", key: "weekly_cleaning_rooms", label: "每周保洁", suffix: "", quickFilter: "cleaning_weekly", actionLabel: "按每周保洁筛选房间" },
  { id: "cleaning-on-demand", key: "on_demand_cleaning_rooms", label: "按需保洁", suffix: "", quickFilter: "cleaning_on_demand", actionLabel: "按需保洁筛选房间" },
  { id: "capacity", key: "total_capacity", label: "总床位", suffix: "", quickFilter: "usable", actionLabel: "按有效住宿床位房间筛选（总床位统计）" },
  { id: "current", key: "current_occupants", label: "当前入住", suffix: "", quickFilter: "occupied", actionLabel: "按已入住筛选房间（当前入住）" },
  { id: "remaining", key: "remaining_capacity", label: "剩余床位", suffix: "", quickFilter: "remaining", actionLabel: "按有剩余床位房间筛选（剩余床位）" },
  { id: "rate", key: "occupancy_rate", label: "入住率", suffix: "%", quickFilter: "usable", actionLabel: "按有效住宿床位房间筛选（入住率统计）" },
  { id: "single", key: "single_rooms", label: "单人间", suffix: "", quickFilter: "single", actionLabel: "按单人间筛选房间" },
  { id: "double", key: "double_rooms", label: "双人间", suffix: "", quickFilter: "double", actionLabel: "按双人间筛选房间" },
  { id: "triple", key: "triple_rooms", label: "三人间", suffix: "", quickFilter: "triple", actionLabel: "按三人间筛选房间" },
  { id: "custom", key: "custom_rooms", label: "自定义房型", suffix: "", quickFilter: "custom", actionLabel: "按自定义房型筛选房间" },
  { id: "functional", key: "functional_rooms", label: "功能间", suffix: "", quickFilter: "functional", actionLabel: "按功能间筛选房间" },
]

function errorMessage(error: unknown): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : "暂时无法读取住宿房态"
}

function isPrivateError(error: unknown): boolean {
  return error instanceof ProblemDetailsError && (error.problem.status === 403 || error.problem.status === 404)
}

type RoomDialogState =
  | { type: "complete-cleaning"; roomId: string; baseVersion: number }
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
  const [selected, setSelected] = useState<Map<string, HousingRoomSelection>>(() => new Map())
  const [bulkAction, setBulkAction] = useState<HousingBulkAction>("verify")
  const [bulkConfirmation, setBulkConfirmation] = useState(false)
  const [bulkError, setBulkError] = useState<string | null>(null)
  const [bulkDetails, setBulkDetails] = useState<string[]>([])
  const selectedRef = useRef<ReadonlyMap<string, HousingRoomSelection>>(selected)
  selectedRef.current = selected
  const selectedVersions = useMemo(() => new Map(Array.from(selected, ([id, room]) => [id, room.version])), [selected])
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
    queryFn: ({ signal }) => getHousingCatalog(signal),
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
  const bulkProblems = housingSelectionProblems(selected.values(), bulkAction)
  const revisionMismatch = Boolean(accessRevision && overview.data && !overviewRevisionMatches)
  const failure = privateAccessError ?? catalog.error ?? overview.error
  const refreshing = catalog.isFetching || overview.isFetching
  const loading = catalog.isPending || overview.isPending || revisionMismatch || refreshing
  const totalPages = acceptedOverview ? Math.max(1, Math.ceil(acceptedOverview.total / acceptedOverview.page_size)) : 1
  const currentPage = effectiveFilters.page ?? 1
  const clampedPage = Math.min(Math.max(currentPage, 1), totalPages)
  const pageOutOfRange = acceptedOverview !== undefined && currentPage !== clampedPage
  const filterValue = useMemo(() => {
    const value = { ...effectiveFilters }
    delete value.page
    delete value.pageSize
    delete value.quickFilter
    return value
  }, [effectiveFilters])
  const summaryScope = JSON.stringify([accountId, accessRevision, user?.is_super_admin, user?.permissions, user?.scoped_permissions, filterValue])
  const summarySnapshotRef = useRef<{ scope: string; summary: HousingSummary } | null>(null)
  useEffect(() => {
    if (privateFailure || accessRevisionChanged || revisionMismatch) {
      summarySnapshotRef.current = null
    } else if (acceptedOverview && !refreshing) {
      summarySnapshotRef.current = { scope: summaryScope, summary: acceptedOverview.summary }
    }
  }, [acceptedOverview, accessRevisionChanged, privateFailure, refreshing, revisionMismatch, summaryScope])
  const displayedSummary = !privateFailure && !accessRevisionChanged && !revisionMismatch && !catalog.isFetching
    ? acceptedOverview?.summary ?? (summarySnapshotRef.current?.scope === summaryScope ? summarySnapshotRef.current.summary : undefined)
    : undefined
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
  const exportScope = JSON.stringify([accountId, accessRevision, canExport, Boolean(privateFailure), user?.is_super_admin, [...(user?.permissions ?? [])].sort(), [...(user?.scoped_permissions ?? [])].sort()])
  const exportScopeRef = useRef(exportScope)
  exportScopeRef.current = exportScope
  useEffect(() => {
    setExportOpen(false)
    setExportError(null)
    setExportPending(false)
  }, [exportScope])
  const roomDialogTarget = roomDialog
    ? acceptedOverview?.rooms.find((room) => room.id === roomDialog.roomId)
    : undefined
  const roomDialogAuthorized = Boolean(
    !privateFailure && !refreshing && roomDialogTarget && (
      (roomDialog?.type === "edit" && roomDialogTarget.allowed_actions.edit) ||
      (roomDialog?.type === "complete-cleaning" && roomDialogTarget.allowed_actions.complete_cleaning) ||
      (roomDialog?.type === "delete" && roomDialogTarget.allowed_actions.delete)
    ),
  )

  const clearPrivateHousingState = useCallback(() => {
    clientStateEpochRef.current += 1
    const empty = new Map<string, HousingRoomSelection>()
    selectedRef.current = empty
    setSelected(empty)
    setBulkConfirmation(false)
    setBulkError(null)
    setBulkDetails([])
    setCatalogOpen(false)
    setRoomDialog(null)
    setRoomMutationError(null)
    setRoomMutationNotice(null)
    setSelectionNotice(null)
    setExportOpen(false)
    setExportError(null)
  }, [])

  const selectionAuthScope = JSON.stringify([accountId, user?.is_super_admin, [...(user?.permissions ?? [])].sort(), [...(user?.scoped_permissions ?? [])].sort()])
  const previousSelectionAuthScopeRef = useRef(selectionAuthScope)
  useEffect(() => {
    if (previousSelectionAuthScopeRef.current === selectionAuthScope) return
    previousSelectionAuthScopeRef.current = selectionAuthScope
    clearPrivateHousingState()
  }, [clearPrivateHousingState, selectionAuthScope])

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
    const next = new Map(selectedRef.current)
    let changed = false
    let removed = false
    for (const room of acceptedOverview.rooms) {
      if (!next.has(room.id)) continue
      if (!isHousingRoomSelectable(room)) {
        next.delete(room.id)
        changed = true
        removed = true
      } else if (JSON.stringify(next.get(room.id)) !== JSON.stringify(housingRoomSelection(room))) {
        next.set(room.id, housingRoomSelection(room))
        changed = true
      }
    }
    if (changed) {
      selectedRef.current = next
      setSelected(next)
    }
    if (removed) {
      setBulkConfirmation(false)
      setBulkError(null)
      setBulkDetails([])
      setRoomMutationNotice("所选房间操作资格已变化，已移除不可操作房间，请重新核对所选房间后确认。")
    }
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
    setBulkConfirmation(false)
    setBulkError(null)
    setBulkDetails([])
    if (selectedRef.current.size > 0) {
      const empty = new Map<string, HousingRoomSelection>()
      selectedRef.current = empty
      setSelected(empty)
    }
    setSelectionNotice(null)
    setRoomDialog(null)
    setFilters(normalizedFilters(value, 1))
  }

  function applySummaryFilter(quickFilter?: HousingQuickFilter): void {
    applyFilters({ ...filterValue, quickFilter })
  }

  function changeSelectedRoom(room: HousingRoom, checked: boolean): void {
    if (refreshing || privateFailure || mutationCoordinator.isPending() || bulkConfirmation || !isHousingRoomSelectable(room)) return
    const result = updateHousingSelection(selectedRef.current, room.id, housingRoomSelection(room), checked)
    setSelectionNotice(result.limited ? "跨页选择最多支持 500 间房，请先取消部分选择。" : null)
    if (result.next === selectedRef.current) return
    const next = new Map(result.next)
    selectedRef.current = next
    setSelected(next)
  }

  function replaceSelectedRooms(next: Map<string, HousingRoomSelection>): void {
    selectedRef.current = next
    setSelected(next)
  }

  function replaceSelectedVersions(versions: Map<string, number>): void {
    const next = new Map(selectedRef.current)
    for (const id of next.keys()) if (!versions.has(id)) next.delete(id)
    replaceSelectedRooms(next)
  }

  function currentBulkSelection(): HousingRoomSelection[] {
    const visible = new Map(acceptedOverview?.rooms.map((room) => [room.id, room]))
    return Array.from(selectedRef.current.values(), (room) => housingRoomSelection(visible.get(room.id) ?? room))
  }

  function openBulkConfirmation(): void {
    if (refreshing || mutationCoordinator.isPending() || privateFailure || housingSelectionProblems(currentBulkSelection(), bulkAction).length) return
    setBulkError(null)
    setBulkDetails([])
    setBulkConfirmation(true)
  }

  function changePage(page: number): void {
    setFilters((current) => normalizedFilters(current, page))
  }

  function changeBulkAction(action: HousingBulkAction): void {
    if (mutationCoordinator.isPending()) return
    setBulkAction(action)
    setBulkConfirmation(false)
    setBulkError(null)
    setBulkDetails([])
    setSelectionNotice(null)
  }

  async function confirmBulkAction(): Promise<void> {
    if (!bulkConfirmation || refreshing || privateFailure) return
    const selection = currentBulkSelection()
    if (housingSelectionProblems(selection, bulkAction).length) return
    const rooms = selection.map(({ id: room_id, version }) => ({ room_id, version }))
    if (!rooms.length || rooms.length > MAX_HOUSING_BATCH_SELECTION) return
    const token = mutationCoordinator.acquire()
    if (!token) return
    const epoch = clientStateEpochRef.current
    setBulkError(null)
    setBulkDetails([])
    setRoomMutationNotice(null)
    try {
      if (bulkAction === "verify") await verifyHousingRooms(rooms)
      else await updateHousingRoomsServiceStatus(rooms, bulkAction)
      if (epoch !== clientStateEpochRef.current) return
      replaceSelectedRooms(new Map())
      await refreshHousing(true)
      if (epoch !== clientStateEpochRef.current) return
      setBulkConfirmation(false)
      setRoomMutationNotice("批量操作已完成")
    } catch (caught) {
      if (epoch !== clientStateEpochRef.current) return
      if (isPrivateError(caught)) {
        enterPrivateFailure(caught)
      } else {
        setBulkError(caught instanceof ProblemDetailsError ? caught.problem.detail : "操作暂时失败，请稍后重试")
        if (caught instanceof ProblemDetailsError && caught.problem.status === 409 && caught.problem.code === "housing_batch_conflict") {
          setBulkDetails((caught.problem.errors ?? []).filter((item) => item.field.startsWith("rooms.")).map((item) => item.message))
        }
      }
    } finally {
      mutationCoordinator.release(token)
    }
  }

  async function refreshHousing(cleaning = false): Promise<void> {
    if (!accessRevision) return
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: catalogKey, exact: true }),
      queryClient.invalidateQueries({ queryKey: housingKeys.overviews(accountId, accessRevision) }),
      queryClient.invalidateQueries({ queryKey: personHousingKeys.root(accountId) }),
      ...(cleaning ? [
        queryClient.invalidateQueries({ queryKey: housingKeys.details(accountId, accessRevision) }),
        queryClient.invalidateQueries({ queryKey: [...housingKeys.scope(accountId, accessRevision), "transfer-options"] }),
      ] : []),
    ])
  }

  async function downloadOccupancyExport(input: HousingOccupancyExportInput | HousingRoomStatusExportInput): Promise<void> {
    const token = mutationCoordinator.acquire()
    if (!token) return
    const epoch = clientStateEpochRef.current
    const scope = exportScopeRef.current
    const isCurrent = () => epoch === clientStateEpochRef.current && scope === exportScopeRef.current
    setExportPending(true)
    setExportError(null)
    try {
      const result = "building_ids" in input ? await exportHousingRoomStatus(input) : await exportHousingOccupancy(input)
      if (!isCurrent()) return
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
      if (!isCurrent()) return
      setExportError(caught instanceof ProblemDetailsError ? caught.problem.code === "housing_export_empty" ? "没有符合条件的房间" : caught.problem.detail : "房态导出暂时失败")
      throw caught
    } finally {
      mutationCoordinator.release(token)
      if (isCurrent()) setExportPending(false)
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
    options: { closeOnSuccess?: boolean; deletedRoomId?: string; cleaning?: boolean } = {},
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
      await refreshHousing(options.cleaning)
      if (clientStateEpoch !== clientStateEpochRef.current) return
      if (options.closeOnSuccess !== false) setRoomDialog(null)
    } catch (caught) {
      if (clientStateEpoch !== clientStateEpochRef.current) return
      if (caught instanceof ProblemDetailsError && caught.problem.status === 409 && caught.problem.code === "housing_stale_version") {
        setSelected(new Map())
        setRoomMutationError("数据已更新，请查看当前数据后再重试。")
        setRoomMutationNotice(roomDialog ? null : "数据已更新，请查看当前数据后再重试。")
        await refreshHousing(options.cleaning)
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
    if (mutationCoordinator.isPending()) return
    setRoomDialog(type === "edit" || type === "complete-cleaning"
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

  function submitCleaning(input: { version: number }): void {
    if (roomDialog?.type !== "complete-cleaning" || !roomDialogAuthorized) return
    void runRoomMutation(() => completeRoomCleaning(roomDialog.roomId, input), { cleaning: true })
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
              disabled={exportPending || mutationCoordinator.pending || refreshing || !catalog.data || !acceptedOverview || accessRevisionChanged}
              onClick={() => {
                setExportError(null)
                setExportOpen(true)
              }}
            >
              <Download aria-hidden="true" />导出
            </button>
          ) : null}
          {hasManagementActions && !privateFailure ? <button type="button" data-housing-management-trigger disabled={mutationCoordinator.pending || refreshing} onClick={() => setCatalogOpen((open) => !open)}>目录管理</button> : null}
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
      ) : !catalog.data || accessRevisionChanged || revisionMismatch ? (
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
            <HousingPropertyExportsPanel key={`property-exports:${accountId}:${accessRevision}`} />
            {roomMutationNotice ? <p className="housingCatalog__notice" role="alert">{roomMutationNotice}</p> : null}
            {selectionNotice ? <p className="housingCatalog__notice" role="alert">{selectionNotice}</p> : null}
            {!loading && acceptedOverview && (acceptedOverview.rooms.some((room) => room.allowed_actions.edit || room.allowed_actions.verify) || selected.size > 0) ? <HousingBulkActions action={bulkAction} count={selected.size} pending={mutationCoordinator.pending || bulkConfirmation} problems={bulkProblems} onActionChange={changeBulkAction} onClear={() => { replaceSelectedRooms(new Map()); setSelectionNotice(null) }} onConfirm={openBulkConfirmation} /> : null}
            {catalogOpen && hasManagementActions && acceptedOverview ? <HousingCatalogPanel accountId={accountId} accessRevision={catalog.data.access_revision} catalog={catalog.data} rooms={acceptedOverview.rooms} pending={refreshing} selectedRooms={selectedVersions} onSelectedRoomsChange={replaceSelectedVersions} externalBulkActions mutationCoordinator={mutationCoordinator} clientStateEpochRef={clientStateEpochRef} focusFallbackRef={pageFocusRef} onPrivateFailure={enterPrivateFailure} /> : null}
            {displayedSummary ? <section className="housingSummary" aria-label="住宿汇总">
              {SUMMARY_ITEMS.map(({ id, key, label, suffix, quickFilter, actionLabel }) => (
                <button
                  key={id}
                  type="button"
                  className="housingSummary__item"
                  aria-label={actionLabel}
                  aria-describedby={`housing-summary-${id}`}
                  aria-pressed={effectiveFilters.quickFilter === quickFilter}
                  title={actionLabel}
                  onClick={() => applySummaryFilter(quickFilter)}
                >
                  <span id={`housing-summary-${id}`}><span className="housingSummary__label">{label}</span>{" "}<strong className="housingSummary__value">{displayedSummary[key] ?? "—"}{suffix}</strong></span>
                </button>
              ))}
            </section> : null}
            {loading || pageOutOfRange || !acceptedOverview ? <section className="housingState" aria-live="polite"><span className="housingState__pulse" /><p>正在读取房态数据</p></section> : <>
            <div className="housingPage__meta">
              <strong>筛选结果 {acceptedOverview.total} 间</strong>
              <span>数据截至 {formatShanghaiDate(acceptedOverview.as_of)}</span>
            </div>
            {acceptedOverview.rooms.length === 0 ? (
              <section className="housingState housingState--empty"><h2>没有匹配的房间</h2><p>请调整筛选条件后重试。</p></section>
            ) : (
              <ul className="housingRoomGrid" aria-label="房间列表">
                {acceptedOverview.rooms.map((room) => <li key={room.id}><RoomCard room={room} selected={selected.has(room.id)} pending={mutationCoordinator.pending} onSelectedChange={(checked) => changeSelectedRoom(room, checked)} onViewDetails={(target) => onViewRoom?.(target.id)} onEdit={(target) => openRoomDialog("edit", target)} onVerify={verifyRoom} onDelete={(target) => openRoomDialog("delete", target)} onCompleteCleaning={(target) => openRoomDialog("complete-cleaning", target)} /></li>)}
              </ul>
            )}
            <nav className="housingPagination" aria-label="房态分页">
              <button type="button" disabled={currentPage <= 1} onClick={() => changePage(Math.max(1, currentPage - 1))}>上一页</button>
              <span>第 {currentPage} / {totalPages} 页</span>
              <button type="button" disabled={currentPage >= totalPages} onClick={() => changePage(currentPage + 1)}>下一页</button>
            </nav>
            </>}
          </div>
        </div>
      )}
      {catalog.data && roomDialog?.type === "edit" && roomDialogAuthorized && roomDialogTarget ? <RoomDialog catalog={catalog.data} room={roomDialogTarget} pending={mutationCoordinator.pending} error={roomMutationError} focusFallbackRef={pageFocusRef} onClose={closeRoomDialog} onSubmit={(input) => submitRoomEdit(input as HousingRoomUpdate)} /> : null}
      {bulkConfirmation && !privateFailure ? <HousingBulkConfirmation action={bulkAction} count={selected.size} pending={mutationCoordinator.pending || refreshing} error={bulkError} details={[...bulkProblems, ...bulkDetails]} blocked={bulkProblems.length > 0 || refreshing} focusFallbackRef={pageFocusRef} onClose={() => { if (!mutationCoordinator.isPending()) setBulkConfirmation(false) }} onConfirm={() => void confirmBulkAction()} /> : null}
      {roomDialog?.type === "delete" && roomDialogAuthorized && roomDialogTarget ? <DeleteDialog kind="房间" name={roomDialogTarget.room_number} pending={mutationCoordinator.pending} error={roomMutationError} focusFallbackRef={pageFocusRef} onClose={closeRoomDialog} onConfirm={confirmRoomDelete} /> : null}
      {roomDialog?.type === "complete-cleaning" && roomDialogAuthorized && roomDialogTarget ? <HousingCleaningDialog roomNumber={roomDialogTarget.room_number} roomVersion={roomDialog.baseVersion} pending={mutationCoordinator.pending} error={roomMutationError} focusFallbackRef={pageFocusRef} onClose={closeRoomDialog} onSubmit={submitCleaning} /> : null}
      {exportOpen && canExport && !privateFailure && catalog.data ? (
        <HousingExportDialog
          catalog={catalog.data}
          filters={effectiveFilters}
          pending={exportPending}
          error={exportError}
          focusFallbackRef={pageFocusRef}
          onClose={() => {
            if (!exportPending) {
              setExportOpen(false)
              setExportError(null)
            }
          }}
          onSnapshot={downloadOccupancyExport}
          onHistory={downloadOccupancyExport}
        />
      ) : null}
    </section>
  )
}
