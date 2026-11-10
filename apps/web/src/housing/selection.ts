import type { HousingRoom } from "./api"

export type HousingBulkAction = "verify" | "maintenance" | "available" | "disabled"

export const HOUSING_BULK_LABELS: Record<HousingBulkAction, string> = {
  verify: "完成核对", maintenance: "设为维修", available: "设为可用", disabled: "设为停用",
}

export type HousingRoomSelection = Pick<HousingRoom, "id" | "version" | "room_number" | "verification_status"> & {
  allowed_actions: Pick<HousingRoom["allowed_actions"], "edit" | "verify">
}

export function housingRoomSelection(room: HousingRoomSelection): HousingRoomSelection {
  return {
    id: room.id, version: room.version, room_number: room.room_number,
    verification_status: room.verification_status,
    allowed_actions: { edit: room.allowed_actions.edit, verify: room.allowed_actions.verify },
  }
}

export function isHousingRoomSelectable(room: HousingRoomSelection): boolean {
  return room.allowed_actions.edit || (room.verification_status === "pending_verification" && room.allowed_actions.verify)
}

export function isHousingRoomActionAllowed(room: HousingRoomSelection, action: HousingBulkAction): boolean {
  return action === "verify"
    ? room.verification_status === "pending_verification" && room.allowed_actions.verify
    : room.allowed_actions.edit
}

export function housingSelectionProblems(rooms: Iterable<HousingRoomSelection>, action: HousingBulkAction): string[] {
  return Array.from(rooms).flatMap((room) => {
    if (isHousingRoomActionAllowed(room, action)) return []
    const reason = action !== "verify" ? "无房间编辑权限，请选择完成核对或取消该房间"
      : room.verification_status === "verified" ? "已核对，请选择维修/可用/停用或取消该房间"
        : "无房间核对权限，请选择维修/可用/停用或取消该房间"
    return [`${room.room_number}：${reason}`]
  })
}

export const MAX_HOUSING_BATCH_SELECTION = 500

export function updateHousingSelection<T>(
  current: ReadonlyMap<string, T>,
  roomId: string,
  version: T,
  checked: boolean,
): { next: ReadonlyMap<string, T>; limited: boolean } {
  if (!checked) {
    if (!current.has(roomId)) return { next: current, limited: false }
    const next = new Map(current)
    next.delete(roomId)
    return { next, limited: false }
  }
  if (!current.has(roomId) && current.size >= MAX_HOUSING_BATCH_SELECTION) {
    return { next: current, limited: true }
  }
  if (current.get(roomId) === version) return { next: current, limited: false }
  const next = new Map(current)
  next.set(roomId, version)
  return { next, limited: false }
}
