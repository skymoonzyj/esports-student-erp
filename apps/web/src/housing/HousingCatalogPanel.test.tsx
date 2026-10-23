import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { personHousingKeys } from "../people/housingApi"
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode, useState } from "react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type { HousingCatalog, HousingFloor, HousingRoom } from "./api"
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
import { HousingCatalogPanel as HousingCatalogPanelBase } from "./HousingCatalogPanel"
import type { HousingCatalogPanelProps } from "./HousingCatalogPanel"
import { housingKeys } from "./housingKeys"
import type { HousingMutationCoordinator } from "./useHousingMutationCoordinator"

vi.mock(import("./api"), async (importOriginal) => ({
  ...await importOriginal(),
  createHousingBuilding: vi.fn(),
  createHousingFloor: vi.fn(),
  createHousingRoom: vi.fn(),
  deleteHousingBuilding: vi.fn(),
  deleteHousingFloor: vi.fn(),
  deleteHousingRoom: vi.fn(),
  updateHousingBuilding: vi.fn(),
  updateHousingFloor: vi.fn(),
  updateHousingRoom: vi.fn(),
  verifyHousingRoom: vi.fn(),
  verifyHousingRooms: vi.fn(),
}))

const writes = {
  createBuilding: vi.mocked(createHousingBuilding), updateBuilding: vi.mocked(updateHousingBuilding), deleteBuilding: vi.mocked(deleteHousingBuilding),
  createFloor: vi.mocked(createHousingFloor), updateFloor: vi.mocked(updateHousingFloor), deleteFloor: vi.mocked(deleteHousingFloor),
  createRoom: vi.mocked(createHousingRoom), updateRoom: vi.mocked(updateHousingRoom), deleteRoom: vi.mocked(deleteHousingRoom),
  verifyRoom: vi.mocked(verifyHousingRoom), verifyRooms: vi.mocked(verifyHousingRooms),
}

const catalog: HousingCatalog = {
  access_revision: "revision-a",
  can_create_building: true,
  buildings: [
    { id: "a", code: "A", name: "一号楼", display_order: 1, is_active: true, notes: null, allowed_actions: ["edit", "delete", "create_floor"], created_at: "", updated_at: "" },
    { id: "b", code: "B", name: "二号楼", display_order: 2, is_active: false, notes: null, allowed_actions: [], created_at: "", updated_at: "" },
  ],
  floors: [
    { id: "a4", building_id: "a", building_name: "一号楼", number: 4, name: "四层", display_order: 4, is_active: true, notes: null, allowed_actions: ["edit", "delete", "create_room"], created_at: "", updated_at: "" },
    { id: "b2", building_id: "b", building_name: "二号楼", number: 2, name: "二层", display_order: 2, is_active: true, notes: null, allowed_actions: [], created_at: "", updated_at: "" },
  ],
}
const defaultClientStateEpochRef = { current: 0 }
const accountId = "account-housing-manager"
const noLifecycleActions = { create_stay: false, report_issue: false, view_detail: false, manage_care: false, complete_cleaning: false, manage_photos: false }

function HousingCatalogPanel(
  props: Omit<HousingCatalogPanelProps, "accountId" | "accessRevision">,
) {
  return (
    <HousingCatalogPanelBase
      accountId={accountId}
      accessRevision={props.catalog.access_revision}
      {...props}
    />
  )
}

function room(overrides: Partial<HousingRoom> = {}): HousingRoom {
  return {
    daily_cleaning_enabled: false, cleaning_status: "ready", cleaning_pending_at: null, cleaning_completed_at: null, photo_count: 0,
    id: "r1", building_id: "a", floor_id: "a4", building_code: "A", building_name: "一号楼", building_is_active: true,
    floor_number: 4, floor_name: "四层", floor_is_active: true, room_number: "409", room_type: "single", capacity: 1,
    area_square_meters: "18.00", verification_status: "pending_verification", service_status: "available", notes: null, version: 7,
    current_occupants: 0, remaining_capacity: 1, current_gender: null, has_future_reservation: false, occupancy_status: "unoccupied",
    is_assignable: false, status_text: "待核对", allowed_actions: { ...noLifecycleActions, edit: true, delete: true, verify: true },
    occupants: [], future_reservation_count: 0, open_issue_summary: null, pending_transfer_count: 0,
    created_at: "", updated_at: "", ...overrides,
  }
}

function renderPanel(options: { value?: HousingCatalog; rooms?: HousingRoom[]; pending?: boolean; client?: QueryClient } = {}) {
  const client = options.client ?? new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={options.value ?? catalog} rooms={options.rooms ?? [room()]} pending={options.pending ?? false} />
    </QueryClientProvider>,
  )
  return { ...view, client }
}

function SelectedPanel({ client, initialSelected = new Map([["r1", 7], ["cross-page", 3]]), rooms = [room()] }: { client: QueryClient; initialSelected?: Map<string, number>; rooms?: HousingRoom[] }) {
  const [selected, setSelected] = useState(initialSelected)
  return <QueryClientProvider client={client}>
    <HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={catalog} rooms={rooms} selectedRooms={selected} onSelectedRoomsChange={setSelected} />
  </QueryClientProvider>
}

function seedHousingTrees(client: QueryClient) {
  const currentKey = housingKeys.detail(accountId, catalog.access_revision, "room-private")
  const otherKey = housingKeys.detail("account-other", "revision-other", "room-private")
  client.setQueryData(currentKey, { occupant: "当前账号住户" })
  client.setQueryData(otherKey, { occupant: "其他账号住户" })
  return { currentKey, otherKey }
}

beforeEach(() => {
  defaultClientStateEpochRef.current = 0
  for (const mock of Object.values(writes)) mock.mockReset().mockResolvedValue({} as never)
})

afterEach(() => {
  document.body.style.overflow = ""
  vi.clearAllMocks()
})

it("renders only server-authorized building and floor actions and performs every building write", async () => {
  const user = userEvent.setup()
  renderPanel()
  expect(screen.queryByRole("button", { name: /编辑楼栋 二号楼/ })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /在二号楼新建楼层/ })).not.toBeInTheDocument()

  await user.click(screen.getByRole("button", { name: "新建楼栋" }))
  const create = screen.getByRole("dialog", { name: "新建楼栋" })
  expect(create).toHaveAttribute("aria-modal", "true")
  expect(within(create).getByLabelText("楼栋代码")).toHaveFocus()
  await user.type(within(create).getByLabelText("楼栋代码"), "C")
  await user.type(within(create).getByLabelText("楼栋名称"), "三号楼")
  await user.clear(within(create).getByLabelText("显示顺序")); await user.type(within(create).getByLabelText("显示顺序"), "3")
  await user.type(within(create).getByLabelText("备注"), "   ")
  await user.click(within(create).getByRole("button", { name: "保存楼栋" }))
  expect(writes.createBuilding).toHaveBeenCalledWith({ code: "C", name: "三号楼", display_order: 3, notes: null })

  await user.click(screen.getByRole("button", { name: "编辑楼栋 一号楼" }))
  await user.clear(screen.getByLabelText("楼栋名称")); await user.type(screen.getByLabelText("楼栋名称"), "主楼")
  await user.click(screen.getByRole("button", { name: "保存楼栋" }))
  expect(writes.updateBuilding).toHaveBeenCalledWith("a", expect.objectContaining({ name: "主楼" }))
  await user.click(screen.getByRole("button", { name: "停用楼栋 一号楼" }))
  expect(writes.updateBuilding).toHaveBeenLastCalledWith("a", { is_active: false })

  await user.click(screen.getByRole("button", { name: "删除楼栋 一号楼" }))
  const confirm = screen.getByRole("dialog", { name: "删除楼栋 一号楼" })
  expect(confirm).toHaveTextContent("一号楼")
  await user.click(within(confirm).getByRole("button", { name: "取消" }))
  expect(writes.deleteBuilding).not.toHaveBeenCalled()
  await user.click(screen.getByRole("button", { name: "删除楼栋 一号楼" }))
  await user.click(screen.getByRole("button", { name: "确认删除楼栋 一号楼" }))
  expect(writes.deleteBuilding).toHaveBeenCalledWith("a")
})

it("invalidates only the current account's person summaries after a building rename", async () => {
  const view = renderPanel()
  const own = personHousingKeys.summary(accountId, "resident")
  const other = personHousingKeys.summary("other-account", "resident")
  view.client.setQueryData(own, { building_name: "一号楼" })
  view.client.setQueryData(other, { building_name: "其他账号楼栋" })
  await userEvent.click(screen.getByRole("button", { name: "编辑楼栋 一号楼" }))
  fireEvent.change(screen.getByLabelText("楼栋名称"), { target: { value: "主楼" } })
  await userEvent.click(screen.getByRole("button", { name: "保存楼栋" }))
  await waitFor(() => expect(view.client.getQueryState(own)?.isInvalidated).toBe(true))
  expect(view.client.getQueryState(other)?.isInvalidated).toBe(false)
})

it("performs every authorized floor write under its building", async () => {
  const user = userEvent.setup()
  renderPanel()
  await user.click(screen.getByRole("button", { name: "在一号楼新建楼层" }))
  await user.type(screen.getByLabelText("楼层编号"), "5")
  await user.type(screen.getByLabelText("楼层名称"), "五层")
  await user.clear(screen.getByLabelText("显示顺序")); await user.type(screen.getByLabelText("显示顺序"), "5")
  await user.click(screen.getByRole("button", { name: "保存楼层" }))
  expect(writes.createFloor).toHaveBeenCalledWith({ building_id: "a", number: 5, name: "五层", display_order: 5, notes: null })

  await user.click(screen.getByRole("button", { name: "编辑楼层 四层" }))
  await user.clear(screen.getByLabelText("楼层名称")); await user.type(screen.getByLabelText("楼层名称"), "四楼")
  await user.click(screen.getByRole("button", { name: "保存楼层" }))
  expect(writes.updateFloor).toHaveBeenCalledWith("a4", expect.objectContaining({ name: "四楼" }))
  await user.click(screen.getByRole("button", { name: "停用楼层 四层" }))
  expect(writes.updateFloor).toHaveBeenLastCalledWith("a4", { is_active: false })
  await user.click(screen.getByRole("button", { name: "删除楼层 四层" }))
  await user.click(screen.getByRole("button", { name: "确认删除楼层 四层" }))
  expect(writes.deleteFloor).toHaveBeenCalledWith("a4")
})

it.each([
  ["single", "单人间", 1], ["double", "双人间", 2], ["triple", "三人间", 3], ["functional", "功能间", 0],
] as const)("creates a %s room with forced capacity", async (type, label, capacity) => {
  const user = userEvent.setup()
  renderPanel()
  await user.click(screen.getByRole("button", { name: "在四层新建房间" }))
  await user.type(screen.getByLabelText("房间号"), `new-${type}`)
  await user.selectOptions(screen.getByLabelText("房型"), type)
  expect(screen.queryByLabelText("自定义容量")).not.toBeInTheDocument()
  expect(within(screen.getByLabelText("房型")).getByRole("option", { name: label })).toBeVisible()
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(writes.createRoom).toHaveBeenCalledWith(expect.objectContaining({ floor_id: "a4", room_type: type, capacity }))
})

it("does not create a room in an alternate floor whose latest create action was revoked", async () => {
  const alternate: HousingFloor = { ...catalog.floors[0], id: "a5", number: 5, name: "五层", allowed_actions: ["edit", "delete", "create_room"] }
  const initialCatalog: HousingCatalog = { ...catalog, floors: [...catalog.floors, alternate] }
  const user = userEvent.setup()
  const view = renderPanel({ value: initialCatalog })

  await user.click(screen.getByRole("button", { name: "在四层新建房间" }))
  await user.selectOptions(screen.getByLabelText("所属楼层"), "a5")
  await user.type(screen.getByLabelText("房间号"), "510")
  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={{ ...initialCatalog, floors: initialCatalog.floors.map((floor) => floor.id === "a5" ? { ...floor, allowed_actions: [] } : floor) }} rooms={[room()]} /></QueryClientProvider>)

  expect(within(screen.getByLabelText("所属楼层")).queryByRole("option", { name: /五层/ })).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(writes.createRoom).not.toHaveBeenCalled()
})

it("does not move a room to an alternate floor whose latest create action was revoked", async () => {
  const alternate: HousingFloor = { ...catalog.floors[0], id: "a5", number: 5, name: "五层", allowed_actions: ["edit", "delete", "create_room"] }
  const initialCatalog: HousingCatalog = { ...catalog, floors: [...catalog.floors, alternate] }
  const user = userEvent.setup()
  const view = renderPanel({ value: initialCatalog })

  await user.click(screen.getByRole("button", { name: "编辑房间 409" }))
  await user.selectOptions(screen.getByLabelText("所属楼层"), "a5")
  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={{ ...initialCatalog, floors: initialCatalog.floors.map((floor) => floor.id === "a5" ? { ...floor, allowed_actions: [] } : floor) }} rooms={[room()]} /></QueryClientProvider>)

  expect(within(screen.getByLabelText("所属楼层")).queryByRole("option", { name: /五层/ })).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(writes.updateRoom).not.toHaveBeenCalled()
})

it("requires custom capacity from 1 to 100 and excludes problem service state", async () => {
  const user = userEvent.setup()
  renderPanel()
  await user.click(screen.getByRole("button", { name: "在四层新建房间" }))
  await user.type(screen.getByLabelText("房间号"), "411")
  await user.selectOptions(screen.getByLabelText("房型"), "custom")
  const capacity = screen.getByLabelText("自定义容量")
  expect(capacity).toBeRequired(); expect(capacity).toHaveAttribute("min", "1"); expect(capacity).toHaveAttribute("max", "100")
  await user.type(capacity, "4")
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(writes.createRoom).toHaveBeenCalledWith(expect.objectContaining({ capacity: 4 }))

  await user.click(screen.getByRole("button", { name: "编辑房间 409" }))
  expect(screen.getByText(/结构变更.*待核对/)).toBeVisible()
  const service = screen.getByLabelText("服务状态")
  expect(within(service).getByRole("option", { name: "可用" })).toBeVisible()
  expect(within(service).getByRole("option", { name: "维修" })).toBeVisible()
  expect(within(service).getByRole("option", { name: "停用" })).toBeVisible()
  expect(within(service).queryByRole("option", { name: "问题房" })).not.toBeInTheDocument()
  expect(within(screen.getByLabelText("所属楼层")).queryByRole("option", { name: /二号楼.*二层/ })).not.toBeInTheDocument()
})

it("omits floor_id when editing a room whose current parent is inactive, but sends an explicitly chosen active target", async () => {
  const inactiveRoom = room({
    building_id: "b", building_code: "B", building_name: "二号楼", building_is_active: false,
    floor_id: "b2", floor_number: 2, floor_name: "二层", floor_is_active: true,
  })
  const user = userEvent.setup()
  renderPanel({ rooms: [inactiveRoom] })

  await user.click(screen.getByRole("button", { name: "编辑房间 409" }))
  const floor = screen.getByLabelText("所属楼层")
  expect(screen.getByText(/当前位置.*二号楼.*二层/)).toBeVisible()
  expect(within(floor).queryByRole("option", { name: /二号楼.*二层/ })).not.toBeInTheDocument()
  await user.type(screen.getByLabelText("备注"), "保留位置")
  await user.click(screen.getByRole("button", { name: "保存房间" }))

  const unchangedPayload = writes.updateRoom.mock.calls[0]?.[1]
  expect(unchangedPayload).not.toHaveProperty("floor_id")

  await user.click(screen.getByRole("button", { name: "编辑房间 409" }))
  await user.selectOptions(screen.getByLabelText("所属楼层"), "a4")
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(writes.updateRoom).toHaveBeenLastCalledWith("r1", expect.objectContaining({ floor_id: "a4" }))
})

it("preserves problem service status during ordinary room edits", async () => {
  const user = userEvent.setup()
  renderPanel({ rooms: [room({ service_status: "problem", notes: "待处理" })] })

  await user.click(screen.getByRole("button", { name: "编辑房间 409" }))
  expect(screen.queryByLabelText("服务状态")).not.toBeInTheDocument()
  expect(screen.getByText("问题房状态需通过问题流程处理")).toBeVisible()
  await user.clear(screen.getByLabelText("备注"))
  await user.type(screen.getByLabelText("备注"), "仅更新备注")
  await user.click(screen.getByRole("button", { name: "保存房间" }))

  const payload = writes.updateRoom.mock.calls[0]?.[1]
  expect(payload).toEqual(expect.objectContaining({ notes: "仅更新备注", version: 7 }))
  expect(payload).not.toHaveProperty("service_status")
})

it("keeps the catalog room draft version across refetch and resets it only after reopen", async () => {
  const stale = new ProblemDetailsError({ type: "about:blank", title: "Conflict", status: 409, detail: "版本冲突", code: "housing_stale_version", request_id: "req" })
  writes.updateRoom.mockRejectedValueOnce(stale).mockRejectedValueOnce(stale).mockResolvedValueOnce(room({ version: 9 }))
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const user = userEvent.setup()
  const view = renderPanel({ client })
  await user.click(screen.getByRole("button", { name: "编辑房间 409" }))
  await user.clear(screen.getByLabelText("房间号")); await user.type(screen.getByLabelText("房间号"), "410")
  await user.type(screen.getByLabelText("备注"), "保留的目录草稿")
  view.rerender(<QueryClientProvider client={client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={catalog} rooms={[room({ room_number: "499", notes: "服务端新值", service_status: "problem", version: 8 })]} pending={false} /></QueryClientProvider>)

  expect(screen.getByLabelText("房间号")).toHaveValue("410")
  expect(screen.getByLabelText("备注")).toHaveValue("保留的目录草稿")
  expect(screen.getByLabelText("服务状态")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(writes.updateRoom).toHaveBeenLastCalledWith("r1", expect.objectContaining({ version: 7, room_number: "410", notes: "保留的目录草稿" }))
  expect(await screen.findByText(/查看当前数据/)).toBeVisible()
  expect(screen.getByLabelText("房间号")).toHaveValue("410")
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(writes.updateRoom).toHaveBeenLastCalledWith("r1", expect.objectContaining({ version: 7, room_number: "410", notes: "保留的目录草稿" }))
  expect(await screen.findByText(/查看当前数据/)).toBeVisible()

  await user.click(screen.getByRole("button", { name: /关闭编辑房间/ }))
  await user.click(screen.getByRole("button", { name: "编辑房间 499" }))
  expect(screen.getByLabelText("房间号")).toHaveValue("499")
  expect(screen.getByLabelText("备注")).toHaveValue("服务端新值")
  expect(screen.queryByLabelText("服务状态")).not.toBeInTheDocument()
  expect(screen.getByText("问题房状态需通过问题流程处理")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(writes.updateRoom).toHaveBeenLastCalledWith("r1", expect.objectContaining({ version: 8, room_number: "499", notes: "服务端新值" }))
  await waitFor(() => expect(invalidate.mock.calls.map(([input]) => input?.queryKey)).toEqual([
    housingKeys.catalog(accountId), housingKeys.overviews(accountId, catalog.access_revision),
    personHousingKeys.root(accountId),
    housingKeys.catalog(accountId), housingKeys.overviews(accountId, catalog.access_revision),
    personHousingKeys.root(accountId),
    housingKeys.catalog(accountId), housingKeys.overviews(accountId, catalog.access_revision),
    personHousingKeys.root(accountId),
  ]))
})

it("verifies only eligible latest room versions and clears batch selection only after success", async () => {
  let finish: (() => void) | undefined
  writes.verifyRooms.mockImplementation(() => new Promise((resolve) => { finish = () => resolve([]) }))
  const rooms = [room(), room({ id: "r2", room_number: "410", version: 4 }), room({ id: "r3", room_number: "411", verification_status: "verified", allowed_actions: { ...noLifecycleActions, edit: true, delete: true, verify: true } }), room({ id: "r4", room_number: "412", allowed_actions: { ...noLifecycleActions, edit: true, delete: true, verify: false } })]
  const user = userEvent.setup()
  renderPanel({ rooms })
  await user.click(screen.getByRole("button", { name: "核对房间 409" }))
  expect(writes.verifyRoom).toHaveBeenCalledWith("r1", 7)
  const eligible = screen.getAllByRole("checkbox", { name: /选择房间.*进行核对/ })
  expect(eligible).toHaveLength(2)
  await user.click(eligible[0]); await user.click(eligible[1])
  await user.click(screen.getByRole("button", { name: "批量核对 2 间房" }))
  expect(writes.verifyRooms).toHaveBeenCalledWith([{ room_id: "r1", version: 7 }, { room_id: "r2", version: 4 }])
  expect(eligible[0]).toBeChecked()
  finish?.()
  await waitFor(() => expect(eligible[0]).not.toBeChecked())
})

it("caps cross-page selection at 500, permits deselection at the cap, and supports manual clear", async () => {
  const first = room({ id: "r1", room_number: "409" })
  const next = room({ id: "r-next", room_number: "410", version: 8 })
  const selected = new Map<string, number>([[first.id, first.version]])
  for (let index = 1; index < 500; index += 1) selected.set(`cross-page-${index}`, index)
  const user = userEvent.setup()
  render(<SelectedPanel client={new QueryClient()} initialSelected={selected} rooms={[first, next]} />)

  expect(screen.getByText("已选择 500 间待核对房间")).toBeVisible()
  await user.click(screen.getByRole("checkbox", { name: "选择房间 410进行核对" }))
  expect(await screen.findByText("跨页选择最多支持 500 间房，请先取消部分选择。")).toBeVisible()
  expect(screen.getByRole("checkbox", { name: "选择房间 410进行核对" })).not.toBeChecked()

  await user.click(screen.getByRole("checkbox", { name: "选择房间 409进行核对" }))
  expect(screen.getByText("已选择 499 间待核对房间")).toBeVisible()
  await user.click(screen.getByRole("checkbox", { name: "选择房间 410进行核对" }))
  expect(screen.getByText("已选择 500 间待核对房间")).toBeVisible()

  await user.click(screen.getByRole("button", { name: "清空选择" }))
  expect(screen.queryByText(/已选择 .* 间待核对房间/)).not.toBeInTheDocument()
  expect(screen.getByRole("checkbox", { name: "选择房间 410进行核对" })).not.toBeChecked()
})

it("never sends a batch larger than the backend limit", async () => {
  const selected = new Map<string, number>()
  for (let index = 0; index < 501; index += 1) selected.set(`cross-page-${index}`, index)
  const user = userEvent.setup()
  render(<SelectedPanel client={new QueryClient()} initialSelected={selected} />)

  await user.click(screen.getByRole("button", { name: "批量核对 501 间房" }))

  expect(writes.verifyRooms).not.toHaveBeenCalled()
  expect(await screen.findByText("单次批量核对最多支持 500 间房，请先取消部分选择。")).toBeVisible()
})

it("drops a latest-page ineligible room without treating an unseen cross-page room as deleted", async () => {
  const first = room({ id: "r1", room_number: "409", version: 7 })
  const second = room({ id: "r2", room_number: "410", version: 4 })
  const user = userEvent.setup()
  const view = renderPanel({ rooms: [first, second] })

  for (const checkbox of screen.getAllByRole("checkbox", { name: /选择房间.*进行核对/ })) {
    await user.click(checkbox)
  }
  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={catalog} rooms={[{ ...first, verification_status: "verified", allowed_actions: { ...noLifecycleActions, ...first.allowed_actions, verify: false } }]} /></QueryClientProvider>)

  await waitFor(() => expect(screen.getByText("已选择 1 间待核对房间")).toBeVisible())
  await user.click(screen.getByRole("button", { name: "批量核对 1 间房" }))
  expect(writes.verifyRooms).toHaveBeenCalledWith([{ room_id: "r2", version: 4 }])
})

it("accepts mutation callbacks and clears stale batch selection while refreshing current data", async () => {
  const stale = new ProblemDetailsError({ type: "about:blank", title: "Conflict", status: 409, detail: "版本冲突", code: "housing_stale_version", request_id: "batch" })
  const verifyRoomsCallback = vi.fn().mockRejectedValue(stale)
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const user = userEvent.setup()
  render(<QueryClientProvider client={client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={catalog} rooms={[room()]} mutationCallbacks={{ verifyRooms: verifyRoomsCallback }} /></QueryClientProvider>)
  const checkbox = screen.getByRole("checkbox", { name: "选择房间 409进行核对" })
  await user.click(checkbox)
  await user.click(screen.getByRole("button", { name: "批量核对 1 间房" }))
  expect(verifyRoomsCallback).toHaveBeenCalledWith([{ room_id: "r1", version: 7 }])
  expect(await screen.findByText(/查看当前数据/)).toBeVisible()
  expect(checkbox).not.toBeChecked()
  expect(invalidate.mock.calls.map(([input]) => input?.queryKey)).toEqual([
    housingKeys.catalog(accountId), housingKeys.overviews(accountId, catalog.access_revision),
    personHousingKeys.root(accountId),
  ])
})

it.each([
  [403, "permission_denied"],
  [404, "housing_resource_not_found"],
] as const)("clears and unlocks batch selection after a private %s response and purges only the current account", async (status, code) => {
  const privateError = new ProblemDetailsError({
    type: "about:blank", title: "Private", status, detail: "不应显示的私有详情", code, request_id: `batch-${status}`,
  })
  writes.verifyRooms.mockRejectedValueOnce(privateError)
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const { currentKey, otherKey } = seedHousingTrees(client)
  const user = userEvent.setup()
  renderPanel({ client })

  const checkbox = screen.getByRole("checkbox", { name: "选择房间 409进行核对" })
  await user.click(checkbox)
  await user.click(screen.getByRole("button", { name: "批量核对 1 间房" }))

  expect(await screen.findByText("所选房间已不可用，请重新选择后重试。")).toBeVisible()
  expect(screen.queryByText("不应显示的私有详情")).not.toBeInTheDocument()
  expect(checkbox).not.toBeChecked()
  expect(checkbox).toBeEnabled()
  expect(invalidate).not.toHaveBeenCalled()
  await waitFor(() => expect(client.getQueryData(currentKey)).toBeUndefined())
  expect(client.getQueryData(otherKey)).toEqual({ occupant: "其他账号住户" })
})

it.each([
  [403, "permission_denied"],
  [404, "housing_resource_not_found"],
] as const)("removes only the selected room after single verify receives a private %s response", async (status, code) => {
  writes.verifyRoom.mockRejectedValueOnce(new ProblemDetailsError({
    type: "about:blank", title: "Private", status, detail: "不应显示的私有详情", code, request_id: `single-${status}`,
  }))
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const { currentKey, otherKey } = seedHousingTrees(client)
  const user = userEvent.setup()
  render(<SelectedPanel client={client} />)

  expect(screen.getByText("已选择 2 间待核对房间")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "核对房间 409" }))

  expect(await screen.findByText("当前操作权限或目标已变化，请查看最新房态后重试。")).toBeVisible()
  expect(screen.queryByText("不应显示的私有详情")).not.toBeInTheDocument()
  expect(screen.getByRole("checkbox", { name: "选择房间 409进行核对" })).not.toBeChecked()
  expect(screen.getByText("已选择 1 间待核对房间")).toBeVisible()
  expect(invalidate).not.toHaveBeenCalled()
  await waitFor(() => expect(client.getQueryData(currentKey)).toBeUndefined())
  expect(client.getQueryData(otherKey)).toEqual({ occupant: "其他账号住户" })

  await user.click(screen.getByRole("button", { name: "批量核对 1 间房" }))
  expect(writes.verifyRooms).toHaveBeenCalledWith([{ room_id: "cross-page", version: 3 }])
})

it.each([
  [403, "permission_denied"],
  [404, "housing_resource_not_found"],
] as const)("removes only the selected room and closes its delete dialog after a private %s response", async (status, code) => {
  writes.deleteRoom.mockRejectedValueOnce(new ProblemDetailsError({
    type: "about:blank", title: "Private", status, detail: "不应显示的私有详情",
    code, request_id: `delete-${status}`,
  }))
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const { currentKey, otherKey } = seedHousingTrees(client)
  const user = userEvent.setup()
  render(<SelectedPanel client={client} />)

  await user.click(screen.getByRole("button", { name: "删除房间 409" }))
  await user.click(screen.getByRole("button", { name: "确认删除房间 409" }))

  expect(await screen.findByText("当前操作权限或目标已变化，请查看最新房态后重试。")).toBeVisible()
  expect(screen.queryByRole("dialog", { name: "删除房间 409" })).not.toBeInTheDocument()
  expect(screen.queryByText("不应显示的私有详情")).not.toBeInTheDocument()
  expect(screen.getByRole("checkbox", { name: "选择房间 409进行核对" })).not.toBeChecked()
  expect(screen.getByText("已选择 1 间待核对房间")).toBeVisible()
  expect(invalidate).not.toHaveBeenCalled()
  await waitFor(() => expect(client.getQueryData(currentKey)).toBeUndefined())
  expect(client.getQueryData(otherKey)).toEqual({ occupant: "其他账号住户" })
})

it.each(["verify", "delete"] as const)("retains single-room selection after a retryable %s failure", async (operation) => {
  const retryable = new ProblemDetailsError({
    type: "about:blank", title: "Unavailable", status: 500, detail: "服务暂时不可用",
    code: "internal_error", request_id: `${operation}-500`,
  })
  if (operation === "verify") writes.verifyRoom.mockRejectedValueOnce(retryable)
  else writes.deleteRoom.mockRejectedValueOnce(retryable)
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const user = userEvent.setup()
  render(<SelectedPanel client={client} />)

  if (operation === "verify") {
    await user.click(screen.getByRole("button", { name: "核对房间 409" }))
    expect(await screen.findByText("服务暂时不可用")).toBeVisible()
  } else {
    await user.click(screen.getByRole("button", { name: "删除房间 409" }))
    await user.click(screen.getByRole("button", { name: "确认删除房间 409" }))
    expect(await within(screen.getByRole("dialog", { name: "删除房间 409" })).findByText("服务暂时不可用")).toBeVisible()
  }

  expect(screen.getByRole("checkbox", { name: "选择房间 409进行核对" })).toBeChecked()
  expect(screen.getByText("已选择 2 间待核对房间")).toBeVisible()
  expect(invalidate).not.toHaveBeenCalled()
})

it("retains batch selection after an ordinary retryable server failure", async () => {
  writes.verifyRooms.mockRejectedValueOnce(new ProblemDetailsError({
    type: "about:blank", title: "Unavailable", status: 503, detail: "服务暂时不可用",
    code: "service_unavailable", request_id: "batch-503",
  }))
  const user = userEvent.setup()
  renderPanel()

  const checkbox = screen.getByRole("checkbox", { name: "选择房间 409进行核对" })
  await user.click(checkbox)
  await user.click(screen.getByRole("button", { name: "批量核对 1 间房" }))

  expect(await screen.findByText("服务暂时不可用")).toBeVisible()
  expect(checkbox).toBeChecked()
  expect(checkbox).toBeEnabled()
})

it("removes a selected room immediately after its catalog delete succeeds", async () => {
  const client = new QueryClient()
  vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const user = userEvent.setup()
  renderPanel({ client })

  const checkbox = screen.getByRole("checkbox", { name: "选择房间 409进行核对" })
  await user.click(checkbox)
  await user.click(screen.getByRole("button", { name: "删除房间 409" }))
  await user.click(screen.getByRole("button", { name: "确认删除房间 409" }))

  await waitFor(() => expect(writes.deleteRoom).toHaveBeenCalledWith("r1"))
  expect(checkbox).not.toBeChecked()
  expect(screen.queryByText("已选择 1 间待核对房间")).not.toBeInTheDocument()
})

it("revokes stale building edit and top-level create dialogs from the latest catalog", async () => {
  const user = userEvent.setup()
  const view = renderPanel()

  await user.click(screen.getByRole("button", { name: "编辑楼栋 一号楼" }))
  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={{ ...catalog, buildings: catalog.buildings.map((item) => item.id === "a" ? { ...item, allowed_actions: [] } : item) }} rooms={[room()]} /></QueryClientProvider>)
  expect(screen.queryByRole("dialog", { name: "编辑楼栋 一号楼" })).not.toBeInTheDocument()
  expect(writes.updateBuilding).not.toHaveBeenCalled()

  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={catalog} rooms={[room()]} /></QueryClientProvider>)
  await user.click(screen.getByRole("button", { name: "新建楼栋" }))
  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={{ ...catalog, can_create_building: false }} rooms={[room()]} /></QueryClientProvider>)
  expect(screen.queryByRole("dialog", { name: "新建楼栋" })).not.toBeInTheDocument()
  expect(writes.createBuilding).not.toHaveBeenCalled()
})

it("revokes a child-create dialog when the latest parent action disappears", async () => {
  const user = userEvent.setup()
  const view = renderPanel()

  await user.click(screen.getByRole("button", { name: "在一号楼新建楼层" }))
  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={{ ...catalog, buildings: catalog.buildings.map((item) => item.id === "a" ? { ...item, allowed_actions: ["edit", "delete"] } : item) }} rooms={[room()]} /></QueryClientProvider>)

  expect(screen.queryByRole("dialog", { name: "在一号楼新建楼层" })).not.toBeInTheDocument()
  expect(writes.createFloor).not.toHaveBeenCalled()
})

it("revokes a room edit dialog when its latest room disappears or loses edit permission", async () => {
  const user = userEvent.setup()
  const view = renderPanel()

  await user.click(screen.getByRole("button", { name: "编辑房间 409" }))
  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={catalog} rooms={[]} /></QueryClientProvider>)
  expect(screen.queryByRole("dialog", { name: "编辑房间 409" })).not.toBeInTheDocument()

  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={catalog} rooms={[room()]} /></QueryClientProvider>)
  await user.click(screen.getByRole("button", { name: "编辑房间 409" }))
  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={catalog} rooms={[room({ allowed_actions: { ...noLifecycleActions, edit: false, delete: true, verify: true } })]} /></QueryClientProvider>)
  expect(screen.queryByRole("dialog", { name: "编辑房间 409" })).not.toBeInTheDocument()
  expect(writes.updateRoom).not.toHaveBeenCalled()
})

it.each([
  ["resolves", null],
  ["rejects", new ProblemDetailsError({
    type: "about:blank", title: "Conflict", status: 409, detail: "版本冲突",
    code: "housing_stale_version", request_id: "revoked-delete",
  })],
] as const)("ignores an already-sent room mutation that %s after the shared authorization epoch advances", async (_settlement, rejection) => {
  let finish: (() => void) | undefined
  writes.deleteRoom.mockImplementationOnce(() => new Promise((resolve, reject) => {
    finish = () => rejection ? reject(rejection) : resolve()
  }))
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const onSelectedRoomsChange = vi.fn()
  const selectedRooms = new Map([["r1", 7]])
  const user = userEvent.setup()
  const view = render(
    <QueryClientProvider client={client}>
      <HousingCatalogPanel
        clientStateEpochRef={defaultClientStateEpochRef}
        catalog={catalog}
        rooms={[room()]}
        selectedRooms={selectedRooms}
        onSelectedRoomsChange={onSelectedRoomsChange}
      />
    </QueryClientProvider>,
  )

  await user.click(screen.getByRole("button", { name: "删除房间 409" }))
  await user.click(screen.getByRole("button", { name: "确认删除房间 409" }))
  expect(writes.deleteRoom).toHaveBeenCalledTimes(1)
  defaultClientStateEpochRef.current += 1
  view.rerender(
    <QueryClientProvider client={client}>
      <HousingCatalogPanel
        clientStateEpochRef={defaultClientStateEpochRef}
        catalog={catalog}
        rooms={[room({ allowed_actions: { ...noLifecycleActions, edit: true, delete: false, verify: true } })]}
        selectedRooms={selectedRooms}
        onSelectedRoomsChange={onSelectedRoomsChange}
      />
    </QueryClientProvider>,
  )
  expect(screen.queryByRole("dialog", { name: "删除房间 409" })).not.toBeInTheDocument()

  await act(async () => {
    finish?.()
    await Promise.resolve()
  })

  expect(invalidate).not.toHaveBeenCalled()
  expect(onSelectedRoomsChange).not.toHaveBeenCalled()
  expect(screen.getByText("已选择 1 间待核对房间")).toBeVisible()
  expect(screen.getByRole("button", { name: "核对房间 409" })).toBeEnabled()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  expect(screen.queryByRole("dialog", { name: "删除房间 409" })).not.toBeInTheDocument()
  expect(writes.deleteRoom).toHaveBeenCalledTimes(1)
})

it("releases the shared lock without invalidating after an in-flight panel mutation unmounts", async () => {
  let finish: ((value: HousingRoom) => void) | undefined
  writes.verifyRoom.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const token = Symbol("panel-test")
  let active = false
  const release = vi.fn((released: symbol) => {
    if (released === token) active = false
  })
  const coordinator: HousingMutationCoordinator = {
    pending: false,
    isPending: () => active,
    acquire: vi.fn(() => {
      active = true
      return token
    }),
    release,
  }
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, "invalidateQueries")
  const view = render(
    <QueryClientProvider client={client}>
      <HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={catalog} rooms={[room()]} mutationCoordinator={coordinator} />
    </QueryClientProvider>,
  )

  fireEvent.click(screen.getByRole("button", { name: "核对房间 409" }))
  expect(writes.verifyRoom).toHaveBeenCalledTimes(1)
  view.unmount()
  await act(async () => {
    finish?.(room())
    await Promise.resolve()
  })

  expect(invalidate).not.toHaveBeenCalled()
  expect(release).toHaveBeenCalledWith(token)
  expect(active).toBe(false)
})

it("locks dialog interactions while pending and supports escape, backdrop, focus trap, and scroll restore", async () => {
  const user = userEvent.setup()
  const view = renderPanel()
  await user.click(screen.getByRole("button", { name: "新建楼栋" }))
  const dialog = screen.getByRole("dialog", { name: "新建楼栋" })
  expect(document.body.style.overflow).toBe("hidden")
  const close = within(dialog).getByRole("button", { name: "关闭新建楼栋" }); close.focus()
  await user.tab()
  expect(within(dialog).getByLabelText("楼栋代码")).toHaveFocus()
  fireEvent.keyDown(document, { key: "Escape" })
  expect(screen.queryByRole("dialog", { name: "新建楼栋" })).not.toBeInTheDocument()
  expect(document.body.style.overflow).toBe("")

  await user.click(screen.getByRole("button", { name: "新建楼栋" }))
  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={catalog} rooms={[room()]} pending /></QueryClientProvider>)
  expect(screen.getByRole("dialog", { name: "新建楼栋" })).toBeVisible()
  const pendingDialog = screen.getByRole("dialog", { name: "新建楼栋" })
  expect(within(pendingDialog).getByRole("group")).toBeDisabled()
  expect(pendingDialog).toHaveAttribute("tabindex", "-1")
  screen.getByRole("button", { name: "新建楼栋" }).focus()
  fireEvent.keyDown(document, { key: "Tab" })
  expect(pendingDialog).toHaveFocus()
  screen.getByRole("button", { name: "新建楼栋" }).focus()
  fireEvent.keyDown(document, { key: "Tab", shiftKey: true })
  expect(pendingDialog).toHaveFocus()
  fireEvent.keyDown(document, { key: "Escape" })
  expect(screen.getByRole("dialog", { name: "新建楼栋" })).toBeVisible()
  fireEvent.mouseDown(screen.getByTestId("housing-dialog-backdrop"))
  expect(screen.getByRole("dialog", { name: "新建楼栋" })).toBeVisible()
})

it("keeps the natural tab order before an autofocus field", async () => {
  const user = userEvent.setup()
  renderPanel()

  await user.click(screen.getByRole("button", { name: "在四层新建房间" }))
  const dialog = screen.getByRole("dialog", { name: "在四层新建房间" })
  const close = within(dialog).getByRole("button", { name: "关闭在四层新建房间" })
  close.focus()

  await user.tab()

  expect(within(dialog).getByLabelText("所属楼层")).toHaveFocus()
})

it("returns focus to the opener after escape, cancel, backdrop, and successful close", async () => {
  const client = new QueryClient()
  vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const user = userEvent.setup()
  renderPanel({ client })
  const opener = screen.getByRole("button", { name: "新建楼栋" })

  await user.click(opener)
  fireEvent.keyDown(document, { key: "Escape" })
  await waitFor(() => expect(opener).toHaveFocus())

  await user.click(opener)
  await user.click(screen.getByRole("button", { name: "取消" }))
  await waitFor(() => expect(opener).toHaveFocus())

  await user.click(opener)
  fireEvent.mouseDown(screen.getByTestId("housing-dialog-backdrop"))
  await waitFor(() => expect(opener).toHaveFocus())

  await user.click(opener)
  await user.type(screen.getByLabelText("楼栋代码"), "C")
  await user.type(screen.getByLabelText("楼栋名称"), "三号楼")
  await user.click(screen.getByRole("button", { name: "保存楼栋" }))
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "新建楼栋" })).not.toBeInTheDocument())
  expect(opener).toHaveFocus()
})

it("returns focus to the real opener after StrictMode replays the dialog effect", async () => {
  const client = new QueryClient()
  const user = userEvent.setup()
  render(
    <StrictMode>
      <QueryClientProvider client={client}>
        <HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={catalog} rooms={[room()]} />
      </QueryClientProvider>
    </StrictMode>,
  )
  const opener = screen.getByRole("button", { name: "新建楼栋" })

  await user.click(opener)
  expect(screen.getByLabelText("楼栋代码")).toHaveFocus()
  fireEvent.keyDown(document, { key: "Escape" })

  await waitFor(() => expect(opener).toHaveFocus())
})

it("keeps a dialog open when submit and escape happen in the same event loop", async () => {
  let finish: (() => void) | undefined
  writes.createBuilding.mockImplementationOnce(() => new Promise((resolve) => { finish = () => resolve({} as never) }))
  const client = new QueryClient()
  vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const user = userEvent.setup()
  renderPanel({ client })

  await user.click(screen.getByRole("button", { name: "新建楼栋" }))
  await user.type(screen.getByLabelText("楼栋代码"), "C")
  await user.type(screen.getByLabelText("楼栋名称"), "三号楼")
  act(() => {
    screen.getByRole("button", { name: "保存楼栋" }).click()
    fireEvent.keyDown(document, { key: "Escape" })
  })

  expect(writes.createBuilding).toHaveBeenCalledTimes(1)
  expect(screen.getByRole("dialog", { name: "新建楼栋" })).toBeVisible()
  finish?.()
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "新建楼栋" })).not.toBeInTheDocument())
})

it("focuses the dialog owner when an authorization update removes the opener", async () => {
  const user = userEvent.setup()
  const view = renderPanel()
  const panel = screen.getByRole("heading", { name: "目录管理", level: 2 }).closest("section")!

  await user.click(screen.getByRole("button", { name: "编辑楼栋 一号楼" }))
  view.rerender(<QueryClientProvider client={view.client}><HousingCatalogPanel clientStateEpochRef={defaultClientStateEpochRef} catalog={{ ...catalog, buildings: catalog.buildings.map((item) => item.id === "a" ? { ...item, allowed_actions: [] } : item) }} rooms={[room()]} /></QueryClientProvider>)

  await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑楼栋 一号楼" })).not.toBeInTheDocument())
  expect(panel).toHaveFocus()
})

it("hides all management controls for a read-only catalog", () => {
  renderPanel({ value: { ...catalog, can_create_building: false, buildings: catalog.buildings.map((building) => ({ ...building, allowed_actions: [] })), floors: catalog.floors.map((floor) => ({ ...floor, allowed_actions: [] })) }, rooms: [room({ allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: false } })] })
  expect(screen.queryByRole("button")).not.toBeInTheDocument()
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
})
