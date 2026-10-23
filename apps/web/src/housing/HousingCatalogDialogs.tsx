import { useState } from "react"
import type { FormEvent, RefObject } from "react"

import type {
  HousingBuilding,
  HousingBuildingCreate,
  HousingBuildingUpdate,
  HousingCatalog,
  HousingFloor,
  HousingFloorCreate,
  HousingFloorUpdate,
  HousingRoom,
  HousingRoomCreate,
  HousingRoomUpdate,
  RoomType,
} from "./api"
import { HousingDialogShell } from "./HousingDialogShell"

function notes(value: string): string | null {
  const normalized = value.trim()
  return normalized || null
}

export function BuildingDialog({ building, pending, error, focusFallbackRef, onClose, onSubmit }: {
  building?: HousingBuilding
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (input: HousingBuildingCreate | HousingBuildingUpdate) => void
}) {
  const [code, setCode] = useState(building?.code ?? "")
  const [name, setName] = useState(building?.name ?? "")
  const [displayOrder, setDisplayOrder] = useState(String(building?.display_order ?? 0))
  const [note, setNote] = useState(building?.notes ?? "")
  function submit(event: FormEvent): void {
    event.preventDefault()
    onSubmit({ code: code.trim(), name: name.trim(), display_order: Number(displayOrder), notes: notes(note) })
  }
  const title = building ? `编辑楼栋 ${building.name}` : "新建楼栋"
  return (
    <HousingDialogShell title={title} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}>
      <form onSubmit={submit}>
        <fieldset disabled={pending}>
          <label>楼栋代码<input data-autofocus required maxLength={16} value={code} onChange={(event) => setCode(event.currentTarget.value)} /></label>
          <label>楼栋名称<input required maxLength={80} value={name} onChange={(event) => setName(event.currentTarget.value)} /></label>
          <label>显示顺序<input required type="number" min="0" max="100000" value={displayOrder} onChange={(event) => setDisplayOrder(event.currentTarget.value)} /></label>
          <label>备注<textarea maxLength={500} value={note} onChange={(event) => setNote(event.currentTarget.value)} /></label>
          <footer><button type="button" onClick={onClose}>取消</button><button type="submit">{pending ? "正在保存" : "保存楼栋"}</button></footer>
        </fieldset>
      </form>
    </HousingDialogShell>
  )
}

export function FloorDialog({ building, floor, pending, error, focusFallbackRef, onClose, onSubmit }: {
  building: HousingBuilding
  floor?: HousingFloor
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (input: HousingFloorCreate | HousingFloorUpdate) => void
}) {
  const [number, setNumber] = useState(floor ? String(floor.number) : "")
  const [name, setName] = useState(floor?.name ?? "")
  const [displayOrder, setDisplayOrder] = useState(String(floor?.display_order ?? 0))
  const [note, setNote] = useState(floor?.notes ?? "")
  function submit(event: FormEvent): void {
    event.preventDefault()
    const input = { number: Number(number), name: name.trim(), display_order: Number(displayOrder), notes: notes(note) }
    onSubmit(floor ? input : { ...input, building_id: building.id })
  }
  const title = floor ? `编辑楼层 ${floor.name}` : `在${building.name}新建楼层`
  return (
    <HousingDialogShell title={title} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}>
      <form onSubmit={submit}>
        <fieldset disabled={pending}>
          <label>楼层编号<input data-autofocus required type="number" min="-20" max="200" value={number} onChange={(event) => setNumber(event.currentTarget.value)} /></label>
          <label>楼层名称<input required maxLength={40} value={name} onChange={(event) => setName(event.currentTarget.value)} /></label>
          <label>显示顺序<input required type="number" min="0" max="100000" value={displayOrder} onChange={(event) => setDisplayOrder(event.currentTarget.value)} /></label>
          <label>备注<textarea maxLength={500} value={note} onChange={(event) => setNote(event.currentTarget.value)} /></label>
          <footer><button type="button" onClick={onClose}>取消</button><button type="submit">{pending ? "正在保存" : "保存楼层"}</button></footer>
        </fieldset>
      </form>
    </HousingDialogShell>
  )
}

const CAPACITY: Record<Exclude<RoomType, "custom">, number> = { single: 1, double: 2, triple: 3, functional: 0 }

export function isHousingRoomDestinationAllowed(catalog: HousingCatalog, floorId: string): boolean {
  const floor = catalog.floors.find((item) => item.id === floorId)
  if (!floor?.is_active || !floor.allowed_actions.includes("create_room")) return false
  return catalog.buildings.some((item) => item.id === floor.building_id && item.is_active)
}

export function RoomDialog({ catalog, initialFloor, room, pending, error, focusFallbackRef, onClose, onSubmit }: {
  catalog: HousingCatalog
  initialFloor?: HousingFloor
  room?: HousingRoom
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (input: HousingRoomCreate | HousingRoomUpdate) => void
}) {
  const [draftRoom] = useState(room)
  const floors = catalog.floors.filter((item) => isHousingRoomDestinationAllowed(catalog, item.id))
  const [floorId, setFloorId] = useState(draftRoom ? "" : initialFloor?.id ?? floors[0]?.id ?? "")
  const [roomNumber, setRoomNumber] = useState(draftRoom?.room_number ?? "")
  const [roomType, setRoomType] = useState<RoomType>(draftRoom?.room_type ?? "single")
  const [customCapacity, setCustomCapacity] = useState(draftRoom?.room_type === "custom" ? String(draftRoom.capacity) : "")
  const [area, setArea] = useState(draftRoom?.area_square_meters ?? "")
  const [service, setService] = useState<"available" | "maintenance" | "disabled">(
    draftRoom?.service_status === "maintenance" || draftRoom?.service_status === "disabled" ? draftRoom.service_status : "available",
  )
  const [note, setNote] = useState(draftRoom?.notes ?? "")
  const [dailyCleaningEnabled, setDailyCleaningEnabled] = useState(draftRoom?.daily_cleaning_enabled ?? false)
  const canManageCare = draftRoom ? Boolean(room?.allowed_actions.manage_care)
    : Boolean(floors.find((floor) => floor.id === floorId)?.allowed_actions.includes("manage_care"))
  function submit(event: FormEvent): void {
    event.preventDefault()
    const capacity = roomType === "custom" ? Number(customCapacity) : CAPACITY[roomType]
    const common = { room_number: roomNumber.trim(), room_type: roomType, capacity, area_square_meters: area.trim() || null, notes: notes(note) }
    if (!draftRoom) {
      onSubmit({ ...common, floor_id: floorId, ...(canManageCare ? { daily_cleaning_enabled: dailyCleaningEnabled } : {}) })
      return
    }
    const input: HousingRoomUpdate = { ...common, version: draftRoom.version }
    if (room?.allowed_actions.manage_care) input.daily_cleaning_enabled = dailyCleaningEnabled
    if (floorId && floorId !== draftRoom.floor_id) input.floor_id = floorId
    if (draftRoom.service_status !== "problem") input.service_status = service
    onSubmit(input)
  }
  const title = draftRoom ? `编辑房间 ${draftRoom.room_number}` : `在${initialFloor?.name ?? "所选楼层"}新建房间`
  return (
    <HousingDialogShell title={title} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}>
      <form onSubmit={submit}>
        <fieldset disabled={pending}>
          {draftRoom ? <p className="housingDialog__warning">结构变更会将房间重置为待核对。</p> : null}
          {draftRoom ? <p className="housingDialog__currentLocation">当前位置：{draftRoom.building_name} · {draftRoom.floor_name}</p> : null}
          <label>所属楼层<select data-autofocus={draftRoom ? true : undefined} required={!draftRoom} value={floorId} onChange={(event) => {
            const nextFloorId = event.currentTarget.value
            setFloorId(nextFloorId)
            if (!draftRoom && !floors.find((floor) => floor.id === nextFloorId)?.allowed_actions.includes("manage_care")) setDailyCleaningEnabled(false)
          }}>{draftRoom ? <option value="">保持当前位置（不移动）</option> : null}{floors.map((floor) => <option key={floor.id} value={floor.id}>{floor.building_name} · {floor.name}</option>)}</select></label>
          <label>房间号<input data-autofocus={draftRoom ? undefined : true} required maxLength={32} value={roomNumber} onChange={(event) => setRoomNumber(event.currentTarget.value)} /></label>
          <label>房型<select value={roomType} onChange={(event) => setRoomType(event.currentTarget.value as RoomType)}><option value="single">单人间</option><option value="double">双人间</option><option value="triple">三人间</option><option value="custom">自定义房型</option><option value="functional">功能间</option></select></label>
          {roomType === "custom" ? <label>自定义容量<input required type="number" min="1" max="100" value={customCapacity} onChange={(event) => setCustomCapacity(event.currentTarget.value)} /></label> : null}
          <label>面积（平方米）<input type="number" min="0.01" step="0.01" value={area} onChange={(event) => setArea(event.currentTarget.value)} /></label>
          {draftRoom?.service_status === "problem" ? <p className="housingDialog__warning">问题房状态需通过问题流程处理</p> : draftRoom ? <label>服务状态<select value={service} onChange={(event) => setService(event.currentTarget.value as typeof service)}><option value="available">可用</option><option value="maintenance">维修</option><option value="disabled">停用</option></select></label> : null}
          <label>备注<textarea maxLength={500} value={note} onChange={(event) => setNote(event.currentTarget.value)} /></label>
          {draftRoom ? <>
            <p>每日保洁：{room?.daily_cleaning_enabled ? "需要" : "无需"}（只读）</p>
          </> : null}
          {canManageCare ? <label><input type="checkbox" checked={dailyCleaningEnabled} onChange={(event) => setDailyCleaningEnabled(event.currentTarget.checked)} />需要每日保洁</label> : null}
          <footer><button type="button" onClick={onClose}>取消</button><button type="submit">{pending ? "正在保存" : "保存房间"}</button></footer>
        </fieldset>
      </form>
    </HousingDialogShell>
  )
}

export function DeleteDialog({ kind, name, pending, error, focusFallbackRef, onClose, onConfirm }: {
  kind: "楼栋" | "楼层" | "房间"
  name: string
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onConfirm: () => void
}) {
  const title = `删除${kind} ${name}`
  return (
    <HousingDialogShell title={title} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}>
      <fieldset disabled={pending}>
        <p>确认删除{kind}“{name}”？此操作无法撤销。</p>
        <footer><button data-autofocus type="button" onClick={onClose}>取消</button><button type="button" onClick={onConfirm}>确认删除{kind} {name}</button></footer>
      </fieldset>
    </HousingDialogShell>
  )
}
