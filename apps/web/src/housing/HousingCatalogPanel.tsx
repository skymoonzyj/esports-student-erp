import { useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { RefObject } from "react"

import { ProblemDetailsError } from "../api/client"
import {
  createHousingBuilding,
  createHousingFloor,
  createHousingRoom,
  deleteHousingBuilding,
  deleteHousingFloor,
  deleteHousingRoom,
  updateHousingBuilding,
  updateHousingFloor,
  updateHousingRoom,
  verifyHousingRoom,
  verifyHousingRooms,
} from "./api"
import type {
  HousingBuildingCreate,
  HousingBuildingUpdate,
  HousingCatalog,
  HousingFloorCreate,
  HousingFloorUpdate,
  HousingRoom,
  HousingRoomCreate,
  HousingRoomUpdate,
  HousingRoomVersion,
} from "./api"
import { BuildingDialog, DeleteDialog, FloorDialog, isHousingRoomDestinationAllowed, RoomDialog } from "./HousingCatalogDialogs"
import { housingKeys } from "./housingKeys"
import { MAX_HOUSING_BATCH_SELECTION, updateHousingSelection } from "./selection"
import { useHousingMutationCoordinator } from "./useHousingMutationCoordinator"
import type { HousingMutationCoordinator } from "./useHousingMutationCoordinator"

export type HousingCatalogMutationCallbacks = {
  createBuilding: typeof createHousingBuilding
  updateBuilding: typeof updateHousingBuilding
  deleteBuilding: typeof deleteHousingBuilding
  createFloor: typeof createHousingFloor
  updateFloor: typeof updateHousingFloor
  deleteFloor: typeof deleteHousingFloor
  createRoom: typeof createHousingRoom
  updateRoom: typeof updateHousingRoom
  deleteRoom: typeof deleteHousingRoom
  verifyRoom: typeof verifyHousingRoom
  verifyRooms: typeof verifyHousingRooms
}

const defaultMutations: HousingCatalogMutationCallbacks = {
  createBuilding: createHousingBuilding,
  updateBuilding: updateHousingBuilding,
  deleteBuilding: deleteHousingBuilding,
  createFloor: createHousingFloor,
  updateFloor: updateHousingFloor,
  deleteFloor: deleteHousingFloor,
  createRoom: createHousingRoom,
  updateRoom: updateHousingRoom,
  deleteRoom: deleteHousingRoom,
  verifyRoom: verifyHousingRoom,
  verifyRooms: verifyHousingRooms,
}

type DialogState =
  | { type: "create-building" }
  | { type: "edit-building"; buildingId: string }
  | { type: "create-floor"; buildingId: string }
  | { type: "edit-floor"; floorId: string }
  | { type: "create-room"; floorId: string }
  | { type: "edit-room"; roomId: string; baseVersion: number }
  | { type: "delete-building"; buildingId: string }
  | { type: "delete-floor"; floorId: string }
  | { type: "delete-room"; roomId: string }

export type HousingCatalogPanelProps = {
  accountId: string
  accessRevision: string
  catalog: HousingCatalog
  rooms?: HousingRoom[]
  currentRooms?: HousingRoom[]
  pending?: boolean
  selectedRooms?: ReadonlyMap<string, number>
  onSelectedRoomsChange?: (rooms: Map<string, number>) => void
  mutations?: Partial<HousingCatalogMutationCallbacks>
  mutationCallbacks?: Partial<HousingCatalogMutationCallbacks>
  mutationCoordinator?: HousingMutationCoordinator
  clientStateEpochRef: RefObject<number>
  focusFallbackRef?: RefObject<HTMLElement | null>
  onPrivateFailure?: (error: unknown) => void
}

function safeError(error: unknown): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : "操作暂时失败，请稍后重试"
}

function isStale(error: unknown): boolean {
  return error instanceof ProblemDetailsError && error.problem.status === 409 && error.problem.code === "housing_stale_version"
}

function isPrivate(error: unknown): boolean {
  return error instanceof ProblemDetailsError && (error.problem.status === 403 || error.problem.status === 404)
}

export function HousingCatalogPanel({ accountId, accessRevision, catalog, rooms, currentRooms, pending = false, selectedRooms, onSelectedRoomsChange, mutations, mutationCallbacks, mutationCoordinator, clientStateEpochRef, focusFallbackRef, onPrivateFailure }: HousingCatalogPanelProps) {
  const queryClient = useQueryClient()
  const localMutationCoordinator = useHousingMutationCoordinator()
  const coordinator = mutationCoordinator ?? localMutationCoordinator
  const callbacks = useMemo(() => ({ ...defaultMutations, ...mutations, ...mutationCallbacks }), [mutationCallbacks, mutations])
  const visibleRooms = rooms ?? currentRooms ?? []
  const [dialog, setDialog] = useState<DialogState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null)
  const [internalSelected, setInternalSelected] = useState<Map<string, number>>(() => new Map())
  const selected = selectedRooms ?? internalSelected
  const selectedRef = useRef<ReadonlyMap<string, number>>(selected)
  const generationRef = useRef(0)
  selectedRef.current = selected
  const locked = pending || coordinator.pending

  useEffect(() => () => {
    generationRef.current += 1
  }, [])

  const dialogBuildingId = dialog && "buildingId" in dialog ? dialog.buildingId : undefined
  const dialogFloorId = dialog && "floorId" in dialog ? dialog.floorId : undefined
  const dialogRoomId = dialog && "roomId" in dialog ? dialog.roomId : undefined
  const dialogBuilding = dialogBuildingId ? catalog.buildings.find((building) => building.id === dialogBuildingId) : undefined
  const dialogFloor = dialogFloorId ? catalog.floors.find((floor) => floor.id === dialogFloorId) : undefined
  const dialogFloorBuilding = dialogFloor ? catalog.buildings.find((building) => building.id === dialogFloor.building_id) : undefined
  const dialogRoom = dialogRoomId ? visibleRooms.find((room) => room.id === dialogRoomId) : undefined
  const dialogAuthorized = (() => {
    switch (dialog?.type) {
      case "create-building": return catalog.can_create_building
      case "edit-building": return Boolean(dialogBuilding?.allowed_actions.includes("edit"))
      case "create-floor": return Boolean(dialogBuilding?.allowed_actions.includes("create_floor"))
      case "edit-floor": return Boolean(dialogFloorBuilding && dialogFloor?.allowed_actions.includes("edit"))
      case "create-room": return Boolean(dialogFloor?.allowed_actions.includes("create_room"))
      case "edit-room": return Boolean(dialogRoom?.allowed_actions.edit)
      case "delete-building": return Boolean(dialogBuilding?.allowed_actions.includes("delete"))
      case "delete-floor": return Boolean(dialogFloor?.allowed_actions.includes("delete"))
      case "delete-room": return Boolean(dialogRoom?.allowed_actions.delete)
      default: return false
    }
  })()

  const updateSelected = useCallback((updater: (current: ReadonlyMap<string, number>) => ReadonlyMap<string, number>) => {
    const current = selectedRef.current
    const next = updater(current)
    if (next === current) return
    const mutableNext = new Map(next)
    selectedRef.current = mutableNext
    if (selectedRooms !== undefined) onSelectedRoomsChange?.(mutableNext)
    else setInternalSelected(mutableNext)
  }, [onSelectedRoomsChange, selectedRooms])

  useEffect(() => {
    updateSelected((current) => {
      const next = new Map(current)
      let changed = false
      for (const room of visibleRooms) {
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
  }, [updateSelected, visibleRooms])

  useEffect(() => {
    if (selected.size < MAX_HOUSING_BATCH_SELECTION) setSelectionNotice(null)
  }, [selected.size])

  useEffect(() => {
    if (!dialog || coordinator.pending || dialogAuthorized) return
    setDialog(null)
    setError(null)
  }, [coordinator.pending, dialog, dialogAuthorized])

  const closeDialog = useCallback(() => {
    if (!pending && !coordinator.isPending()) { setDialog(null); setError(null) }
  }, [coordinator, pending])

  async function refresh(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: housingKeys.catalog(accountId), exact: true }),
      queryClient.invalidateQueries({ queryKey: housingKeys.overviews(accountId, accessRevision) }),
    ])
  }

  function purgePrivateHousing(): void {
    const root = housingKeys.root(accountId)
    const cancellation = queryClient.cancelQueries({ queryKey: root })
    queryClient.removeQueries({ queryKey: root })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey: root }))
  }

  async function run(operation: () => Promise<unknown>, options: { close?: boolean; clearSelection?: boolean; affectedRoomIds?: readonly string[]; removeAffectedSelectionOnSuccess?: boolean; batch?: boolean } = {}): Promise<void> {
    if (pending) return
    const mutationToken = coordinator.acquire()
    if (!mutationToken) return
    const clientStateEpoch = clientStateEpochRef.current
    const generation = generationRef.current
    const clientStateIsCurrent = () => (
      generation === generationRef.current
      && clientStateEpoch === clientStateEpochRef.current
    )
    setError(null)
    setNotice(null)
    const removeAffectedSelections = () => {
      if (!options.affectedRoomIds?.length) return
      const affectedRoomIds = new Set(options.affectedRoomIds)
      updateSelected((current) => {
        const next = new Map(current)
        let changed = false
        for (const roomId of affectedRoomIds) changed = next.delete(roomId) || changed
        return changed ? next : current
      })
    }
    try {
      await operation()
      if (!clientStateIsCurrent()) return
      if (options.clearSelection) updateSelected(() => new Map())
      if (options.removeAffectedSelectionOnSuccess) removeAffectedSelections()
      await refresh()
      if (!clientStateIsCurrent()) return
      if (options.close !== false) setDialog(null)
    } catch (caught) {
      if (!clientStateIsCurrent()) return
      if (isStale(caught)) {
        updateSelected(() => new Map())
        setError("数据已更新，请查看当前数据后再重试。")
        setNotice(dialog ? null : "数据已更新，请查看当前数据后再重试。")
        await refresh()
        if (!clientStateIsCurrent()) return
      } else if (isPrivate(caught)) {
        if (options.batch) updateSelected(() => new Map())
        else removeAffectedSelections()
        setDialog(null)
        setError(null)
        setNotice(options.batch
          ? "所选房间已不可用，请重新选择后重试。"
          : "当前操作权限或目标已变化，请查看最新房态后重试。")
        purgePrivateHousing()
        onPrivateFailure?.(caught)
      } else {
        const message = safeError(caught)
        setError(message)
        setNotice(dialog ? null : message)
      }
    } finally {
      coordinator.release(mutationToken)
    }
  }

  function buildingSubmit(input: HousingBuildingCreate | HousingBuildingUpdate): void {
    if (dialog?.type === "create-building" && catalog.can_create_building) {
      void run(() => callbacks.createBuilding(input as HousingBuildingCreate))
      return
    }
    if (dialog?.type !== "edit-building") return
    const target = catalog.buildings.find((building) => building.id === dialog.buildingId)
    if (!target?.allowed_actions.includes("edit")) return
    void run(() => callbacks.updateBuilding(target.id, input as HousingBuildingUpdate))
  }

  function floorSubmit(input: HousingFloorCreate | HousingFloorUpdate): void {
    if (dialog?.type === "create-floor") {
      const parent = catalog.buildings.find((building) => building.id === dialog.buildingId)
      if (!parent?.allowed_actions.includes("create_floor")) return
      void run(() => callbacks.createFloor(input as HousingFloorCreate))
      return
    }
    if (dialog?.type !== "edit-floor") return
    const target = catalog.floors.find((floor) => floor.id === dialog.floorId)
    if (!target?.allowed_actions.includes("edit")) return
    void run(() => callbacks.updateFloor(target.id, input as HousingFloorUpdate))
  }

  function roomSubmit(input: HousingRoomCreate | HousingRoomUpdate): void {
    if (dialog?.type === "create-room") {
      const parent = catalog.floors.find((floor) => floor.id === dialog.floorId)
      const createInput = input as HousingRoomCreate
      if (!parent?.allowed_actions.includes("create_room") || !isHousingRoomDestinationAllowed(catalog, createInput.floor_id)) return
      void run(() => callbacks.createRoom(createInput))
      return
    }
    if (dialog?.type !== "edit-room") return
    const target = visibleRooms.find((room) => room.id === dialog.roomId)
    const updateInput = input as HousingRoomUpdate
    if (!target?.allowed_actions.edit || (updateInput.floor_id && !isHousingRoomDestinationAllowed(catalog, updateInput.floor_id))) return
    void run(() => callbacks.updateRoom(target.id, { ...updateInput, version: dialog.baseVersion }))
  }

  function confirmDelete(): void {
    if (!dialog) return
    if (dialog.type === "delete-building") {
      const target = catalog.buildings.find((building) => building.id === dialog.buildingId)
      if (target?.allowed_actions.includes("delete")) void run(() => callbacks.deleteBuilding(target.id))
    }
    if (dialog.type === "delete-floor") {
      const target = catalog.floors.find((floor) => floor.id === dialog.floorId)
      if (target?.allowed_actions.includes("delete")) void run(() => callbacks.deleteFloor(target.id))
    }
    if (dialog.type === "delete-room") {
      const target = visibleRooms.find((room) => room.id === dialog.roomId)
      if (target?.allowed_actions.delete) void run(() => callbacks.deleteRoom(target.id), { affectedRoomIds: [target.id], removeAffectedSelectionOnSuccess: true })
    }
  }

  function toggleSelected(room: HousingRoom, checked: boolean): void {
    if (room.verification_status !== "pending_verification" || !room.allowed_actions.verify || locked) return
    updateSelected((current) => {
      const result = updateHousingSelection(current, room.id, room.version, checked)
      setSelectionNotice(result.limited ? "跨页选择最多支持 500 间房，请先取消部分选择。" : null)
      return result.next
    })
  }

  function batchVerify(): void {
    const versions: HousingRoomVersion[] = Array.from(selectedRef.current, ([room_id, version]) => ({ room_id, version }))
    if (!versions.length) return
    if (versions.length > MAX_HOUSING_BATCH_SELECTION) {
      setSelectionNotice("单次批量核对最多支持 500 间房，请先取消部分选择。")
      return
    }
    void run(() => callbacks.verifyRooms(versions), { close: false, clearSelection: true, batch: true })
  }

  return (
    <section className="housingCatalog" aria-labelledby="housing-catalog-title" data-housing-dialog-owner tabIndex={-1}>
      <header className="housingCatalog__header"><div><h2 id="housing-catalog-title">目录管理</h2><p>按楼栋和楼层维护房间目录</p></div>{catalog.can_create_building ? <button type="button" disabled={locked} onClick={() => { setError(null); setDialog({ type: "create-building" }) }}>新建楼栋</button> : null}</header>
      {notice ? <p className="housingCatalog__notice" role="alert">{notice}</p> : null}
      {selectionNotice ? <p className="housingCatalog__notice" role="alert">{selectionNotice}</p> : null}
      {selected.size > 0 ? <div className="housingCatalog__batch"><span aria-label={`跨页已选总数 ${selected.size} 间`}><span>已选择 {selected.size} 间待核对房间</span><small>跨页总数</small></span><div className="housingCatalog__batchActions"><button type="button" disabled={locked} onClick={() => { setSelectionNotice(null); updateSelected(() => new Map()) }}>清空选择</button><button type="button" disabled={locked} onClick={batchVerify}>批量核对 {selected.size} 间房</button></div></div> : null}
      <ul className="housingCatalog__buildings" aria-label="楼栋目录">
        {catalog.buildings.map((building) => {
          const floors = catalog.floors.filter((floor) => floor.building_id === building.id)
          const buildingRooms = visibleRooms.filter((room) => room.building_id === building.id)
          return <li key={building.id} className="housingCatalog__building"><article aria-label={`楼栋 ${building.name}`}>
            <header><div><h3>{building.name} <small>{building.code}</small></h3><span>{floors.length} 层 · {buildingRooms.length} 间房 · {building.is_active ? "启用" : "停用"}</span></div><div className="housingCatalog__actions">
              {building.allowed_actions.includes("create_floor") ? <button type="button" disabled={locked} onClick={() => { setError(null); setDialog({ type: "create-floor", buildingId: building.id }) }}>在{building.name}新建楼层</button> : null}
              {building.allowed_actions.includes("edit") ? <><button type="button" aria-label={`编辑楼栋 ${building.name}`} disabled={locked} onClick={() => { setError(null); setDialog({ type: "edit-building", buildingId: building.id }) }}>编辑</button><button type="button" aria-label={`${building.is_active ? "停用" : "启用"}楼栋 ${building.name}`} disabled={locked} onClick={() => void run(() => callbacks.updateBuilding(building.id, { is_active: !building.is_active }))}>{building.is_active ? "停用" : "启用"}</button></> : null}
              {building.allowed_actions.includes("delete") ? <button type="button" aria-label={`删除楼栋 ${building.name}`} disabled={locked} onClick={() => { setError(null); setDialog({ type: "delete-building", buildingId: building.id }) }}>删除</button> : null}
            </div></header>
            <ul aria-label={`${building.name}楼层`}>
              {floors.map((floor) => {
                const floorRooms = visibleRooms.filter((room) => room.floor_id === floor.id)
                return <li key={floor.id}><article aria-label={`楼层 ${floor.name}`}><header><div><h4>{floor.name}</h4><span>{floorRooms.length} 间房 · {floor.is_active ? "启用" : "停用"}</span></div><div className="housingCatalog__actions">
                  {floor.allowed_actions.includes("create_room") ? <button type="button" disabled={locked} onClick={() => { setError(null); setDialog({ type: "create-room", floorId: floor.id }) }}>在{floor.name}新建房间</button> : null}
                  {floor.allowed_actions.includes("edit") ? <><button type="button" aria-label={`编辑楼层 ${floor.name}`} disabled={locked} onClick={() => { setError(null); setDialog({ type: "edit-floor", floorId: floor.id }) }}>编辑</button><button type="button" aria-label={`${floor.is_active ? "停用" : "启用"}楼层 ${floor.name}`} disabled={locked} onClick={() => void run(() => callbacks.updateFloor(floor.id, { is_active: !floor.is_active }))}>{floor.is_active ? "停用" : "启用"}</button></> : null}
                  {floor.allowed_actions.includes("delete") ? <button type="button" aria-label={`删除楼层 ${floor.name}`} disabled={locked} onClick={() => { setError(null); setDialog({ type: "delete-floor", floorId: floor.id }) }}>删除</button> : null}
                </div></header>
                {floorRooms.length ? <ul aria-label={`${floor.name}房间`}>{floorRooms.map((room) => <li key={room.id}><span>{room.room_number} · {room.verification_status === "pending_verification" ? "待核对" : "已核对"}</span><div className="housingCatalog__actions">
                  {room.verification_status === "pending_verification" && room.allowed_actions.verify ? <label><input type="checkbox" aria-label={`选择房间 ${room.room_number}进行核对`} checked={selected.has(room.id)} disabled={locked} onChange={(event) => toggleSelected(room, event.currentTarget.checked)} />选择</label> : null}
                  {room.allowed_actions.edit ? <button type="button" aria-label={`编辑房间 ${room.room_number}`} disabled={locked} onClick={() => { setError(null); setDialog({ type: "edit-room", roomId: room.id, baseVersion: room.version }) }}>编辑</button> : null}
                  {room.verification_status === "pending_verification" && room.allowed_actions.verify ? <button type="button" aria-label={`核对房间 ${room.room_number}`} disabled={locked} onClick={() => void run(() => callbacks.verifyRoom(room.id, room.version), { close: false, affectedRoomIds: [room.id] })}>核对</button> : null}
                  {room.allowed_actions.delete ? <button type="button" aria-label={`删除房间 ${room.room_number}`} disabled={locked} onClick={() => { setError(null); setDialog({ type: "delete-room", roomId: room.id }) }}>删除</button> : null}
                </div></li>)}</ul> : null}
              </article></li>})}
            </ul>
          </article></li>
        })}
      </ul>

      {dialog?.type === "create-building" && dialogAuthorized ? <BuildingDialog pending={locked} error={error} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={buildingSubmit} /> : null}
      {dialog?.type === "edit-building" && dialogAuthorized && dialogBuilding ? <BuildingDialog building={dialogBuilding} pending={locked} error={error} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={buildingSubmit} /> : null}
      {dialog?.type === "create-floor" && dialogAuthorized && dialogBuilding ? <FloorDialog building={dialogBuilding} pending={locked} error={error} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={floorSubmit} /> : null}
      {dialog?.type === "edit-floor" && dialogAuthorized && dialogFloor && dialogFloorBuilding ? <FloorDialog building={dialogFloorBuilding} floor={dialogFloor} pending={locked} error={error} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={floorSubmit} /> : null}
      {dialog?.type === "create-room" && dialogAuthorized && dialogFloor ? <RoomDialog catalog={catalog} initialFloor={dialogFloor} pending={locked} error={error} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={roomSubmit} /> : null}
      {dialog?.type === "edit-room" && dialogAuthorized && dialogRoom ? <RoomDialog catalog={catalog} room={dialogRoom} pending={locked} error={error} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onSubmit={roomSubmit} /> : null}
      {dialog?.type === "delete-building" && dialogAuthorized && dialogBuilding ? <DeleteDialog kind="楼栋" name={dialogBuilding.name} pending={locked} error={error} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onConfirm={confirmDelete} /> : null}
      {dialog?.type === "delete-floor" && dialogAuthorized && dialogFloor ? <DeleteDialog kind="楼层" name={dialogFloor.name} pending={locked} error={error} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onConfirm={confirmDelete} /> : null}
      {dialog?.type === "delete-room" && dialogAuthorized && dialogRoom ? <DeleteDialog kind="房间" name={dialogRoom.room_number} pending={locked} error={error} focusFallbackRef={focusFallbackRef} onClose={closeDialog} onConfirm={confirmDelete} /> : null}
    </section>
  )
}
