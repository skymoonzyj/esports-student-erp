import type { HousingStay, HousingRoom } from "./api"

type RoomOccupancySlotsProps = {
  roomType: HousingRoom["room_type"]
  capacity: number
  occupants: readonly HousingStay[]
}

const PERSON_TYPE_LABELS: Record<HousingStay["person_type"], string> = {
  student: "学员",
  player: "队员",
  student_player: "学员兼队员",
  team_manager: "战队经理",
  coach: "教练",
  assistant_coach: "助教",
  life_teacher: "生活老师",
  customer_service: "客服",
  administration: "行政人员",
  other: "其他工作人员",
  visitor: "临时访客",
}

function displayCapacity(roomType: HousingRoom["room_type"], capacity: number): number {
  if (roomType === "single") return Math.min(Math.max(capacity, 0), 1)
  if (roomType === "double") return Math.min(Math.max(capacity, 0), 2)
  return 0
}

function sexLabel(sex: HousingStay["sex"]): string {
  return sex === "male" ? "男" : sex === "female" ? "女" : "性别待补"
}

export function RoomOccupancySlots({ roomType, capacity, occupants }: RoomOccupancySlotsProps) {
  const slots = displayCapacity(roomType, capacity)
  if (slots === 0) return null

  const roomTypeLabel = roomType === "single" ? "单人房" : "双人房"
  const visibleOccupants = occupants.slice(0, slots)

  return (
    <section className={`housingOccupancySlots housingOccupancySlots--${roomType}`} aria-label={`${roomTypeLabel}入住人员`}>
      <header className="housingOccupancySlots__header">
        <strong>入住人员</strong>
        <span>{visibleOccupants.length} / {slots}</span>
      </header>
      <ol className="housingOccupancySlots__list">
        {Array.from({ length: slots }, (_, index) => {
          const occupant = visibleOccupants[index]
          return (
            <li
              className={`housingOccupancySlots__slot${occupant ? "" : " housingOccupancySlots__slot--empty"}`}
              key={occupant?.id ?? `empty-${index}`}
              aria-label={occupant ? `床位 ${index + 1}：${occupant.person_name}` : `床位 ${index + 1}：空位`}
            >
              <span className="housingOccupancySlots__index">床位 {index + 1}</span>
              {occupant ? (
                <span className="housingOccupancySlots__person">
                  <strong>{occupant.person_name}</strong>
                  <small>{PERSON_TYPE_LABELS[occupant.person_type]} · {sexLabel(occupant.sex)}</small>
                </span>
              ) : (
                <span className="housingOccupancySlots__person housingOccupancySlots__person--empty">
                  <strong>空位</strong>
                  <small>可安排入住</small>
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
