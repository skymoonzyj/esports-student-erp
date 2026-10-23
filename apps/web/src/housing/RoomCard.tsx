import { Eye, Pencil, ShieldCheck, Trash2 } from "lucide-react"

import type { HousingRoom } from "./api"
import { RoomOccupancySlots } from "./RoomOccupancySlots"

type RoomCardProps = {
  room: HousingRoom
  selected: boolean
  pending?: boolean
  onSelectedChange: (selected: boolean) => void
  onViewDetails: (room: HousingRoom) => void
  onEdit: (room: HousingRoom) => void
  onVerify: (room: HousingRoom) => void
  onDelete: (room: HousingRoom) => void
}

const ROOM_TYPE_LABELS: Record<HousingRoom["room_type"], string> = {
  single: "单人间",
  double: "双人间",
  triple: "三人间",
  custom: "自定义房型",
  functional: "功能间",
}

function genderLabel(room: HousingRoom): string {
  if (room.current_gender === "male") return "男住"
  if (room.current_gender === "female") return "女住"
  return "未指定"
}

function cardTone(room: HousingRoom): "occupied" | "available" | "unavailable" {
  if (room.current_occupants > 0) return "occupied"
  return room.is_assignable ? "available" : "unavailable"
}

export function RoomCard({
  room,
  selected,
  pending = false,
  onSelectedChange,
  onViewDetails,
  onEdit,
  onVerify,
  onDelete,
}: RoomCardProps) {
  const tone = cardTone(room)
  const occupiedText = `已入住 ${room.current_occupants} / ${room.capacity}`
  const displayStatus = room.current_occupants > 0 ? occupiedText : room.status_text
  const pendingFunctional =
    room.verification_status === "pending_verification" && room.room_type === "functional"

  return (
    <article
      className={`housingRoomCard housingRoomCard--${tone}`}
      aria-label={`房间 ${room.room_number}：${displayStatus}`}
    >
      <header className="housingRoomCard__header">
        <div>
          <p>{room.building_name} · {room.floor_name}</p>
          <h3>{room.room_number}</h3>
        </div>
        {room.verification_status === "pending_verification" && room.allowed_actions.verify ? (
          <label className="housingRoomCard__selection">
            <input
              type="checkbox"
              aria-label={`选择房间 ${room.room_number}进行核对`}
              checked={selected}
              disabled={pending}
              onChange={(event) => onSelectedChange(event.currentTarget.checked)}
            />
          </label>
        ) : null}
      </header>

      <div className="housingRoomCard__status">
        <span role="img" aria-label={tone === "occupied" ? "已入住" : tone === "available" ? "空闲" : "不可入住"}>
          {tone === "occupied" ? "●" : tone === "available" ? "✓" : "—"}
        </span>
        {pendingFunctional ? (
          <strong><span>待核对</span><span>功能间，不可入住</span></strong>
        ) : (
          <strong>{displayStatus}</strong>
        )}
      </div>
      {room.service_status === "problem" ? (
        <span className="housingRoomCard__problem">问题房</span>
      ) : null}
      {room.pending_transfer_count > 0 ? (
        <span className="housingRoomCard__pendingTransfer">
          待调房 {room.pending_transfer_count} 人
        </span>
      ) : null}
      {room.occupancy_status === "occupied" && room.remaining_capacity > 0 && room.is_assignable ? (
        <p className="housingRoomCard__partial">可继续入住 · 剩余 {room.remaining_capacity} 位</p>
      ) : null}

      <RoomOccupancySlots
        roomType={room.room_type}
        capacity={room.capacity}
        occupants={room.occupants}
      />

      <dl className="housingRoomCard__details">
        <div><dt>房型</dt><dd>{ROOM_TYPE_LABELS[room.room_type]}</dd></div>
        <div><dt>面积</dt><dd>{room.area_square_meters ? `${room.area_square_meters} ㎡` : "面积未录入"}</dd></div>
        <div><dt>入住性别</dt><dd>{genderLabel(room)}</dd></div>
        <div><dt>容量</dt><dd>容量 {room.capacity} 位</dd></div>
        <div><dt>未来预订</dt><dd>{room.has_future_reservation ? "有未来预订" : "无未来预订"}</dd></div>
      </dl>
      <p className="housingRoomCard__notes">{room.notes ?? "暂无备注"}</p>

      <footer className="housingRoomCard__actions">
        <button type="button" aria-label={`查看房间 ${room.room_number} 详情`} disabled={pending} onClick={() => onViewDetails(room)}>
          <Eye aria-hidden="true" /> 详情
        </button>
        {room.allowed_actions.edit ? (
          <button type="button" aria-label={`编辑房间 ${room.room_number}`} disabled={pending} onClick={() => onEdit(room)}>
            <Pencil aria-hidden="true" /> 编辑
          </button>
        ) : null}
        {room.allowed_actions.verify ? (
          <button type="button" aria-label={`核对房间 ${room.room_number}`} disabled={pending} onClick={() => onVerify(room)}>
            <ShieldCheck aria-hidden="true" /> 核对
          </button>
        ) : null}
        {room.allowed_actions.delete ? (
          <button type="button" aria-label={`删除房间 ${room.room_number}`} disabled={pending} onClick={() => onDelete(room)}>
            <Trash2 aria-hidden="true" /> 删除
          </button>
        ) : null}
      </footer>
    </article>
  )
}
