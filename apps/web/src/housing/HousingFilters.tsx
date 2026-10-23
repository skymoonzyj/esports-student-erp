import { X } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import type {
  HousingCatalog,
  HousingFiltersValue,
  RoomType,
  ServiceStatus,
  VerificationStatus,
} from "./api"

type HousingFiltersProps = {
  catalog: HousingCatalog
  value: HousingFiltersValue
  open: boolean
  onOpenChange: (open: boolean) => void
  onApply: (value: HousingFiltersValue) => void
  onReset: () => void
}

const ROOM_TYPES: Array<[RoomType, string]> = [
  ["single", "单人间"], ["double", "双人间"], ["triple", "三人间"],
  ["custom", "自定义房型"], ["functional", "功能间"],
]
const VERIFICATION_STATUSES: Array<[VerificationStatus, string]> = [
  ["pending_verification", "待核对"], ["verified", "已核对"],
]
const SERVICE_STATUSES: Array<[ServiceStatus, string]> = [
  ["available", "正常"], ["problem", "问题房"], ["maintenance", "维修中"], ["disabled", "已停用"],
]

function useMobile(): boolean {
  const [mobile, setMobile] = useState(() => window.innerWidth < 720)
  useEffect(() => {
    const update = () => setMobile(window.innerWidth < 720)
    window.addEventListener("resize", update)
    return () => window.removeEventListener("resize", update)
  }, [])
  return mobile
}

function toggled<T>(values: T[] | undefined, value: T, checked: boolean): T[] {
  if (checked) return [...(values ?? []), value]
  return (values ?? []).filter((item) => item !== value)
}

const bodyScrollLockOwners = new Set<symbol>()
let bodyOverflowBeforeLocks = ""

function lockBodyScroll(owner: symbol): void {
  if (typeof document === "undefined" || bodyScrollLockOwners.has(owner)) return
  if (bodyScrollLockOwners.size === 0) bodyOverflowBeforeLocks = document.body.style.overflow
  bodyScrollLockOwners.add(owner)
  document.body.style.overflow = "hidden"
}

function unlockBodyScroll(owner: symbol): void {
  if (typeof document === "undefined" || !bodyScrollLockOwners.delete(owner)) return
  if (bodyScrollLockOwners.size === 0) {
    document.body.style.overflow = bodyOverflowBeforeLocks
    bodyOverflowBeforeLocks = ""
  }
}

export function HousingFilters({
  catalog,
  value,
  open,
  onOpenChange,
  onApply,
  onReset,
}: HousingFiltersProps) {
  const mobile = useMobile()
  const [draft, setDraft] = useState<HousingFiltersValue>(value)
  const panelRef = useRef<HTMLElement>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)
  const restoreFocusRef = useRef(false)
  const scrollLockOwnerRef = useRef(Symbol("housing-filter-scroll-lock"))

  const requestClose = useCallback(() => {
    restoreFocusRef.current = true
    onOpenChange(false)
  }, [onOpenChange])

  useEffect(() => {
    if (!open) setDraft(value)
  }, [open, value])

  const floors = useMemo(
    () => draft.buildingId
      ? catalog.floors.filter((floor) => floor.building_id === draft.buildingId)
      : catalog.floors,
    [catalog.floors, draft.buildingId],
  )

  useEffect(() => {
    if (!mobile || !open) return
    restoreFocusRef.current = false
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    lockBodyScroll(scrollLockOwnerRef.current)
    const panel = panelRef.current
    const selector = "button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])"
    const focusable = () => Array.from(panel?.querySelectorAll<HTMLElement>(selector) ?? [])
    focusable()[0]?.focus()

    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault()
        requestClose()
        return
      }
      if (event.key !== "Tab") return
      const items = focusable()
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("keydown", onKeyDown)
      unlockBodyScroll(scrollLockOwnerRef.current)
      if (restoreFocusRef.current && previousFocusRef.current?.isConnected) {
        previousFocusRef.current.focus()
      }
      restoreFocusRef.current = false
      previousFocusRef.current = null
    }
  }, [mobile, open, requestClose])

  if (mobile && !open) return null

  function updateArray(
    key: "roomTypes" | "verificationStatuses" | "serviceStatuses",
    option: RoomType | VerificationStatus | ServiceStatus,
    checked: boolean,
  ): void {
    setDraft((current) => ({
      ...current,
      [key]: toggled(
        current[key] as Array<RoomType | VerificationStatus | ServiceStatus> | undefined,
        option,
        checked,
      ),
    }))
  }

  const panel = (
    <aside
      ref={panelRef}
      className={`housingFilters ${mobile ? "housingFilters--sheet" : ""}`}
      {...(mobile ? { role: "dialog", "aria-modal": true, "aria-label": "筛选房间" } : { "aria-label": "筛选房间" })}
    >
      <header><h2>筛选房间</h2></header>
      <div className="housingFilters__body">
        <label>楼栋
          <select
            value={draft.buildingId ?? ""}
            onChange={(event) => {
              const buildingId = event.currentTarget.value || undefined
              setDraft((current) => {
                const floorValid = !current.floorId || catalog.floors.some(
                  (floor) => floor.id === current.floorId && (!buildingId || floor.building_id === buildingId),
                )
                return { ...current, buildingId, floorId: floorValid ? current.floorId : undefined }
              })
            }}
          >
            <option value="">全部楼栋</option>
            {catalog.buildings.map((building) => <option key={building.id} value={building.id}>{building.name}</option>)}
          </select>
        </label>
        <label>楼层
          <select value={draft.floorId ?? ""} onChange={(event) => {
            const floorId = event.currentTarget.value || undefined
            setDraft((current) => ({ ...current, floorId }))
          }}>
            <option value="">全部楼层</option>
            {floors.map((floor) => <option key={floor.id} value={floor.id}>{floor.name}</option>)}
          </select>
        </label>
        <label>搜索房间
          <input value={draft.query ?? ""} onChange={(event) => {
            const query = event.currentTarget.value
            setDraft((current) => ({ ...current, query }))
          }} placeholder="房号、楼栋或楼层" />
        </label>

        <fieldset><legend>房型</legend>{ROOM_TYPES.map(([key, label]) => (
          <label key={key}><input type="checkbox" checked={draft.roomTypes?.includes(key) ?? false} onChange={(event) => updateArray("roomTypes", key, event.currentTarget.checked)} />{label}</label>
        ))}</fieldset>
        <fieldset><legend>核对状态</legend>{VERIFICATION_STATUSES.map(([key, label]) => (
          <label key={key}><input type="checkbox" checked={draft.verificationStatuses?.includes(key) ?? false} onChange={(event) => updateArray("verificationStatuses", key, event.currentTarget.checked)} />{label}</label>
        ))}</fieldset>
        <fieldset><legend>服务状态</legend>{SERVICE_STATUSES.map(([key, label]) => (
          <label key={key}><input type="checkbox" checked={draft.serviceStatuses?.includes(key) ?? false} onChange={(event) => updateArray("serviceStatuses", key, event.currentTarget.checked)} />{label}</label>
        ))}</fieldset>

        <label>当前入住性别
          <select value={draft.currentGender ?? ""} onChange={(event) => {
            const currentGender = (event.currentTarget.value || undefined) as HousingFiltersValue["currentGender"]
            setDraft((current) => ({ ...current, currentGender }))
          }}>
            <option value="">全部性别</option><option value="male">男住</option><option value="female">女住</option><option value="unspecified">未指定</option>
          </select>
        </label>
        <label>入住状态
          <select value={draft.occupancy ?? ""} onChange={(event) => {
            const occupancy = (event.currentTarget.value || undefined) as HousingFiltersValue["occupancy"]
            setDraft((current) => ({ ...current, occupancy }))
          }}>
            <option value="">全部入住状态</option><option value="occupied">已入住</option><option value="unoccupied">未入住</option>
          </select>
        </label>
        <label>可入住状态
          <select value={draft.assignability ?? ""} onChange={(event) => {
            const assignability = (event.currentTarget.value || undefined) as HousingFiltersValue["assignability"]
            setDraft((current) => ({ ...current, assignability }))
          }}>
            <option value="">全部可入住状态</option><option value="assignable">可入住</option><option value="unassignable">不可入住</option>
          </select>
        </label>
      </div>
      <footer>
        <button type="button" className="housingFilters__apply" onClick={() => { onApply(draft); if (mobile) requestClose() }}>应用筛选</button>
        <button type="button" onClick={() => { if (mobile) requestClose(); else setDraft({}); onReset() }}>重置筛选</button>
        {mobile ? <button type="button" aria-label="关闭筛选" onClick={requestClose}><X aria-hidden="true" />关闭</button> : null}
      </footer>
    </aside>
  )

  return mobile ? <div className="housingFiltersBackdrop">{panel}</div> : panel
}
