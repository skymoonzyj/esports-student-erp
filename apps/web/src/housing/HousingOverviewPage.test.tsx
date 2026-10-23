import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { cwd } from "node:process"
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type { HousingCatalog, HousingFiltersValue, HousingFloor, HousingOverview, HousingRoom } from "./api"
import {
  deleteHousingRoom,
  getHousingCatalog,
  listHousingOverview,
  updateHousingRoom,
  verifyHousingRoom,
  verifyHousingRooms,
} from "./api"
import { HousingFilters } from "./HousingFilters"
import { HousingOverviewPage } from "./HousingOverviewPage"
import type { HousingOverviewPageProps } from "./HousingOverviewPage"
import { housingKeys } from "./housingKeys"
import { updateHousingSelection } from "./selection"

const accountId = "account-housing-manager"

vi.mock(import("../auth/AuthProvider"), () => ({
  useAuth: () => ({
    status: "authenticated" as const,
    user: {
      account_id: accountId,
      person_id: "person-housing-manager",
      full_name: "房态管理员",
      email: "housing-manager@example.test",
      force_password_change: false,
      permissions: ["housing.read"],
    },
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshUser: vi.fn(),
  }),
}))

vi.mock(import("./api"), async (importOriginal) => ({
  ...await importOriginal(),
  deleteHousingRoom: vi.fn(),
  getHousingCatalog: vi.fn(),
  listHousingOverview: vi.fn(),
  updateHousingRoom: vi.fn(),
  verifyHousingRoom: vi.fn(),
  verifyHousingRooms: vi.fn(),
}))

vi.mock(import("./selection"), async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, updateHousingSelection: vi.fn(actual.updateHousingSelection) }
})

const catalogMock = vi.mocked(getHousingCatalog)
const overviewMock = vi.mocked(listHousingOverview)
const deleteRoomMock = vi.mocked(deleteHousingRoom)
const updateRoomMock = vi.mocked(updateHousingRoom)
const verifyRoomMock = vi.mocked(verifyHousingRoom)
const verifyRoomsMock = vi.mocked(verifyHousingRooms)
const updateSelectionMock = vi.mocked(updateHousingSelection)

const housingStyles = readFileSync(resolve(cwd(), "src/housing/housing.css"), "utf8")
const noLifecycleActions = { create_stay: false, report_issue: false, view_detail: false }

const catalog: HousingCatalog = {
  access_revision: "revision-a",
  can_create_building: false,
  buildings: [
    { id: "a", code: "A", name: "一号楼", display_order: 1, is_active: true, notes: null, allowed_actions: [], created_at: "", updated_at: "" },
    { id: "b", code: "B", name: "二号楼", display_order: 2, is_active: true, notes: null, allowed_actions: [], created_at: "", updated_at: "" },
  ],
  floors: [
    { id: "a4", building_id: "a", building_name: "一号楼", number: 4, name: "四层", display_order: 4, is_active: true, notes: null, allowed_actions: [], created_at: "", updated_at: "" },
    { id: "a5", building_id: "a", building_name: "一号楼", number: 5, name: "五层", display_order: 5, is_active: true, notes: null, allowed_actions: [], created_at: "", updated_at: "" },
    { id: "b2", building_id: "b", building_name: "二号楼", number: 2, name: "二层", display_order: 2, is_active: true, notes: null, allowed_actions: [], created_at: "", updated_at: "" },
  ],
}

const ignoreOpenChange = (): void => undefined
const ignoreApply = (): void => undefined
const ignoreReset = (): void => undefined

function FilterPair({ firstOpen, secondOpen }: { firstOpen: boolean; secondOpen: boolean }) {
  return (
    <>
      <HousingFilters catalog={catalog} value={{}} open={firstOpen} onOpenChange={ignoreOpenChange} onApply={ignoreApply} onReset={ignoreReset} />
      <HousingFilters catalog={catalog} value={{}} open={secondOpen} onOpenChange={ignoreOpenChange} onApply={ignoreApply} onReset={ignoreReset} />
    </>
  )
}

function room(overrides: Partial<HousingRoom> = {}): HousingRoom {
  return {
    id: "room-409", building_id: "a", floor_id: "a4", building_code: "A", building_name: "一号楼", building_is_active: true,
    floor_number: 4, floor_name: "四层", floor_is_active: true, room_number: "409", room_type: "single", capacity: 1,
    area_square_meters: "18.00", verification_status: "verified", service_status: "available", notes: null, version: 1,
    current_occupants: 0, remaining_capacity: 1, current_gender: null, has_future_reservation: false,
    occupancy_status: "unoccupied", is_assignable: true, status_text: "空闲，可入住",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: false },
    occupants: [], future_reservation_count: 0, open_issue_summary: null, pending_transfer_count: 0,
    created_at: "", updated_at: "", ...overrides,
  }
}

function overview(overrides: Partial<HousingOverview> = {}): HousingOverview {
  return {
    access_revision: "revision-a",
    as_of: "2026-08-04T01:30:00Z",
    summary: {
      total_rooms: 8, occupied_rooms: 3, unoccupied_rooms: 5, assignable_rooms: 4, unassignable_rooms: 4,
      problem_rooms: 1, pending_verification_rooms: 2, total_capacity: 12, current_occupants: 3,
      remaining_capacity: 9, occupancy_rate: "25.00", single_rooms: 4, double_rooms: 2,
      triple_rooms: 1, custom_rooms: 0, functional_rooms: 1,
    },
    rooms: [room()], page: 1, page_size: 20, total: 1, ...overrides,
  }
}

function setWidth(width: number): void {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: width })
  fireEvent(window, new Event("resize"))
}

function renderPage(
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  props: HousingOverviewPageProps = {},
) {
  return render(
    <QueryClientProvider client={queryClient}>
      <HousingOverviewPage {...props} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  setWidth(1024)
  catalogMock.mockReset()
  overviewMock.mockReset()
  deleteRoomMock.mockReset().mockResolvedValue()
  updateRoomMock.mockReset().mockResolvedValue(room())
  verifyRoomMock.mockReset().mockResolvedValue(room())
  verifyRoomsMock.mockReset().mockResolvedValue([])
  catalogMock.mockResolvedValue(catalog)
  overviewMock.mockResolvedValue(overview())
})

afterEach(() => {
  document.body.style.overflow = ""
  vi.clearAllMocks()
})

it("renders every summary, filtered count, semantic grid, and Shanghai as-of time", async () => {
  renderPage()

  expect(await screen.findByRole("article", { name: /房间 409/ })).toBeVisible()
  for (const text of ["房间总数 8", "已入住 3", "空房 5", "可入住 4", "不可入住 4", "问题房 1", "待核对 2", "总床位 12", "当前入住 3", "剩余床位 9", "入住率 25.00%", "单人间 4", "双人间 2", "三人间 1", "自定义房型 0", "功能间 1"]) {
    expect(screen.getByText(text)).toBeVisible()
  }
  expect(screen.getByText("数据截至 2026-08-04")).toBeVisible()
  expect(screen.getByText("筛选结果 1 间")).toBeVisible()
  expect(screen.getByRole("list", { name: "房间列表" })).toBeVisible()
  expect(screen.getByRole("article", { name: "房间 409：空闲，可入住" })).toBeVisible()
})

it.each([
  ["已入住", "按已入住筛选房间", { occupancy: "occupied" }],
  ["空房", "按空房筛选房间", { occupancy: "unoccupied" }],
  ["可入住", "按可入住筛选房间", { assignability: "assignable" }],
  ["不可入住", "按不可入住筛选房间", { assignability: "unassignable" }],
  ["问题房", "按问题房筛选房间", { serviceStatuses: ["problem"] }],
  ["待核对", "按待核对筛选房间", { verificationStatuses: ["pending_verification"] }],
  ["当前入住", "按已入住筛选房间（当前入住）", { occupancy: "occupied" }],
  ["剩余床位", "按可入住筛选房间（剩余床位）", { assignability: "assignable" }],
  ["单人间", "按单人间筛选房间", { roomTypes: ["single"] }],
  ["双人间", "按双人间筛选房间", { roomTypes: ["double"] }],
  ["三人间", "按三人间筛选房间", { roomTypes: ["triple"] }],
  ["自定义房型", "按自定义房型筛选房间", { roomTypes: ["custom"] }],
  ["功能间", "按功能间筛选房间", { roomTypes: ["functional"] }],
] as const)("filters the room list when the %s summary is clicked", async (_label, actionName, expected) => {
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: actionName }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith(
    expect.objectContaining({ ...expected, page: 1, pageSize: 20 }),
  ))
})

it("supports keyboard activation and clears an existing filter from the total-room summary", async () => {
  const user = userEvent.setup()
  renderPage()

  await user.selectOptions(await screen.findByLabelText("入住状态"), "occupied")
  await user.click(screen.getByRole("button", { name: "应用筛选" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith(
    expect.objectContaining({ occupancy: "occupied", page: 1, pageSize: 20 }),
  ))

  const totalRooms = screen.getByRole("button", { name: "查看全部房间" })
  totalRooms.focus()
  await user.keyboard("{Enter}")
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 }))
})

it("opens the selected room detail through the route-owned navigation callback", async () => {
  const onViewRoom = vi.fn()
  const user = userEvent.setup()
  renderPage(undefined, { onViewRoom })

  await user.click(await screen.findByRole("button", { name: "查看房间 409 详情" }))

  expect(onViewRoom).toHaveBeenCalledWith("room-409")
})

it("mounts catalog management behind its button only when a server action exists", async () => {
  catalogMock.mockResolvedValue({
    ...catalog,
    buildings: catalog.buildings.map((building, index) => index === 0 ? { ...building, allowed_actions: ["edit"] } : building),
  })
  overviewMock.mockResolvedValue(overview({ rooms: [room({ allowed_actions: { ...noLifecycleActions, edit: true, delete: false, verify: false } })] }))
  const user = userEvent.setup()
  renderPage()

  const trigger = await screen.findByRole("button", { name: "目录管理" })
  expect(screen.queryByRole("heading", { name: "目录管理", level: 2 })).not.toBeInTheDocument()
  await user.click(trigger)
  expect(screen.getByRole("heading", { name: "目录管理", level: 2 })).toBeVisible()
  expect(screen.getByRole("button", { name: "编辑楼栋 一号楼" })).toBeVisible()
})

it("opens a prefilled room editor from the authorized room card and performs the update", async () => {
  const managedRoom = room({
    version: 12,
    notes: "靠窗",
    allowed_actions: { ...noLifecycleActions, edit: true, delete: false, verify: false },
  })
  overviewMock.mockResolvedValue(overview({ rooms: [managedRoom] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("button", { name: "编辑房间 409" }))
  const dialog = screen.getByRole("dialog", { name: "编辑房间 409" })
  expect(within(dialog).getByLabelText("房间号")).toHaveValue("409")
  expect(within(dialog).getByLabelText("备注")).toHaveValue("靠窗")
  await user.clear(within(dialog).getByLabelText("备注"))
  await user.type(within(dialog).getByLabelText("备注"), "新备注")
  await user.click(within(dialog).getByRole("button", { name: "保存房间" }))

  expect(updateRoomMock).toHaveBeenCalledWith("room-409", expect.objectContaining({ notes: "新备注", version: 12 }))
  await waitFor(() => expect(invalidate.mock.calls.map(([input]) => input?.queryKey)).toEqual([
    housingKeys.catalog(accountId), housingKeys.overviews(accountId, catalog.access_revision),
  ]))
})

it("does not move a room-card target to a floor whose latest create action was revoked", async () => {
  const editable = room({ allowed_actions: { ...noLifecycleActions, edit: true, delete: false, verify: false } })
  const alternate: HousingFloor = { ...catalog.floors[1], allowed_actions: ["edit", "delete", "create_room"] }
  const managedCatalog: HousingCatalog = { ...catalog, floors: catalog.floors.map((floor) => floor.id === alternate.id ? alternate : floor) }
  catalogMock.mockResolvedValue(managedCatalog)
  overviewMock.mockResolvedValue(overview({ rooms: [editable] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("button", { name: "编辑房间 409" }))
  await user.selectOptions(screen.getByLabelText("所属楼层"), alternate.id)
  client.setQueryData(housingKeys.catalog(accountId), {
    ...managedCatalog,
    access_revision: "revision-b",
    floors: managedCatalog.floors.map((floor) => floor.id === alternate.id ? { ...floor, allowed_actions: [] } : floor),
  })

  await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑房间 409" })).not.toBeInTheDocument())
  expect(updateRoomMock).not.toHaveBeenCalled()
})

it("keeps the room-card draft version across refetch and resets it only after reopen", async () => {
  const stale = new ProblemDetailsError({
    type: "about:blank", title: "Conflict", status: 409, detail: "版本冲突",
    code: "housing_stale_version", request_id: "card-edit",
  })
  const editable = room({ version: 12, notes: "原备注", allowed_actions: { ...noLifecycleActions, edit: true, delete: false, verify: false } })
  const refetched = { ...editable, room_number: "499", notes: "服务端新值", version: 15 }
  overviewMock.mockResolvedValueOnce(overview({ rooms: [editable] })).mockResolvedValue(overview({ rooms: [refetched] }))
  updateRoomMock.mockRejectedValueOnce(stale).mockRejectedValueOnce(stale).mockResolvedValueOnce({ ...editable, version: 16 })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, "invalidateQueries")
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("button", { name: "编辑房间 409" }))
  await user.clear(screen.getByLabelText("备注"))
  await user.type(screen.getByLabelText("备注"), "保留的草稿")
  await act(async () => {
    await client.invalidateQueries({ queryKey: housingKeys.overviews(accountId, catalog.access_revision) })
  })
  await waitFor(() => expect(overviewMock).toHaveBeenCalledTimes(2))

  expect(screen.getByLabelText("房间号")).toHaveValue("409")
  expect(screen.getByLabelText("备注")).toHaveValue("保留的草稿")
  await user.click(screen.getByRole("button", { name: "保存房间" }))

  expect(updateRoomMock).toHaveBeenLastCalledWith("room-409", expect.objectContaining({ notes: "保留的草稿", version: 12 }))
  expect(await screen.findByText("数据已更新，请查看当前数据后再重试。")).toBeVisible()
  expect(screen.getByLabelText("备注")).toHaveValue("保留的草稿")
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(updateRoomMock).toHaveBeenLastCalledWith("room-409", expect.objectContaining({ notes: "保留的草稿", version: 12 }))
  expect(await screen.findByText("数据已更新，请查看当前数据后再重试。")).toBeVisible()

  await user.click(screen.getByRole("button", { name: /关闭编辑房间/ }))
  await user.click(screen.getByRole("button", { name: "编辑房间 499" }))
  expect(screen.getByLabelText("房间号")).toHaveValue("499")
  expect(screen.getByLabelText("备注")).toHaveValue("服务端新值")
  await user.click(screen.getByRole("button", { name: "保存房间" }))

  expect(updateRoomMock).toHaveBeenLastCalledWith("room-409", expect.objectContaining({ notes: "服务端新值", version: 15 }))
  await waitFor(() => expect(invalidate.mock.calls.map(([input]) => input?.queryKey)).toEqual([
    housingKeys.overviews(accountId, catalog.access_revision),
    housingKeys.catalog(accountId), housingKeys.overviews(accountId, catalog.access_revision),
    housingKeys.catalog(accountId), housingKeys.overviews(accountId, catalog.access_revision),
    housingKeys.catalog(accountId), housingKeys.overviews(accountId, catalog.access_revision),
  ]))
})

it("verifies an authorized room card once with its latest server version", async () => {
  overviewMock.mockResolvedValue(overview({ rooms: [room({
    version: 14,
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })] }))
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "核对房间 409" }))

  await waitFor(() => expect(verifyRoomMock).toHaveBeenCalledTimes(1))
  expect(verifyRoomMock).toHaveBeenCalledWith("room-409", 14)
})

it("serializes a catalog mutation against a room-card mutation and releases the shared lock", async () => {
  let finish: ((value: HousingRoom) => void) | undefined
  const selectable = room({
    version: 14,
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  overviewMock.mockResolvedValue(overview({ rooms: [selectable] }))
  verifyRoomMock.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("button", { name: "目录管理" }))
  const panel = screen.getByRole("heading", { name: "目录管理", level: 2 }).closest("section")!
  const card = screen.getByRole("article", { name: /房间 409/ })
  const panelVerify = within(panel).getByRole("button", { name: "核对房间 409" })
  const cardVerify = within(card).getByRole("button", { name: "核对房间 409" })

  act(() => {
    panelVerify.click()
    cardVerify.click()
  })
  expect(verifyRoomMock).toHaveBeenCalledTimes(1)
  expect(cardVerify).toBeDisabled()

  finish?.(selectable)
  await waitFor(() => expect(cardVerify).toBeEnabled())
  await user.click(cardVerify)
  expect(verifyRoomMock).toHaveBeenCalledTimes(2)
})

it("serializes a room-card mutation against a catalog mutation", async () => {
  let finish: ((value: HousingRoom) => void) | undefined
  const selectable = room({
    version: 14,
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  overviewMock.mockResolvedValue(overview({ rooms: [selectable] }))
  verifyRoomMock.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("button", { name: "目录管理" }))
  const panel = screen.getByRole("heading", { name: "目录管理", level: 2 }).closest("section")!
  const card = screen.getByRole("article", { name: /房间 409/ })
  const cardVerify = within(card).getByRole("button", { name: "核对房间 409" })
  const panelVerify = within(panel).getByRole("button", { name: "核对房间 409" })

  act(() => {
    cardVerify.click()
    panelVerify.click()
  })
  expect(verifyRoomMock).toHaveBeenCalledTimes(1)
  expect(panelVerify).toBeDisabled()

  finish?.(selectable)
  await waitFor(() => expect(panelVerify).toBeEnabled())
})

it("settles a deferred shared mutation safely after the page unmounts", async () => {
  let finish: ((value: HousingRoom) => void) | undefined
  const selectable = room({
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  overviewMock.mockResolvedValue(overview({ rooms: [selectable] }))
  verifyRoomMock.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, "invalidateQueries")
  const view = renderPage(client)

  fireEvent.click(await screen.findByRole("button", { name: "核对房间 409" }))
  expect(verifyRoomMock).toHaveBeenCalledTimes(1)
  view.unmount()

  await act(async () => {
    finish?.(selectable)
    await Promise.resolve()
  })
  expect(verifyRoomMock).toHaveBeenCalledTimes(1)
  expect(invalidate).not.toHaveBeenCalled()
})

it("keeps management state and selection when catalog data changes under the same access revision", async () => {
  const managedCatalog: HousingCatalog = {
    ...catalog,
    buildings: catalog.buildings.map((building) => building.id === "a" ? { ...building, allowed_actions: ["edit"] } : building),
  }
  const selectable = room({
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: true, delete: false, verify: true },
  })
  catalogMock.mockResolvedValue(managedCatalog)
  overviewMock.mockResolvedValue(overview({ rooms: [selectable] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 409进行核对" }))
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  const panel = screen.getByRole("heading", { name: "目录管理", level: 2 }).closest("section")!
  await user.click(within(panel).getByRole("button", { name: "编辑房间 409" }))

  catalogMock.mockResolvedValueOnce({
    ...managedCatalog,
    buildings: [
      ...managedCatalog.buildings,
      { ...catalog.buildings[1], id: "c", code: "C", name: "三号楼" },
    ],
  })
  await client.refetchQueries({ queryKey: housingKeys.catalog(accountId), exact: true })

  expect(screen.getByRole("heading", { name: "目录管理", level: 2 })).toBeVisible()
  expect(screen.getByRole("dialog", { name: "编辑房间 409" })).toBeVisible()
  expect(screen.getByText("已选择 1 间待核对房间")).toBeVisible()
})

it("hides revision A rooms as soon as catalog revision B arrives", async () => {
  const catalogB: HousingCatalog = { ...catalog, access_revision: "revision-b" }
  catalogMock.mockResolvedValueOnce(catalog).mockResolvedValueOnce(catalogB)
  overviewMock.mockResolvedValueOnce(overview()).mockImplementation(() => new Promise(() => undefined))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  renderPage(client)

  expect(await screen.findByRole("article", { name: /房间 409/ })).toBeVisible()
  await act(async () => {
    await client.refetchQueries({ queryKey: housingKeys.catalog(accountId), exact: true })
  })

  await waitFor(() => expect(screen.queryByRole("article", { name: /房间 409/ })).not.toBeInTheDocument())
  expect(screen.getByText("正在读取房态数据")).toBeVisible()
})

it("normalizes stale catalog filters before requesting a new access revision", async () => {
  let revision = "revision-a"
  const catalogB: HousingCatalog = {
    ...catalog,
    access_revision: "revision-b",
    buildings: [catalog.buildings[1]],
    floors: catalog.floors.filter((floor) => floor.building_id === "b"),
  }
  overviewMock.mockImplementation(async () => overview({ access_revision: revision }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  renderPage(client)

  await screen.findByRole("article", { name: /房间 409/ })
  await user.selectOptions(screen.getByLabelText("楼栋"), "a")
  await user.selectOptions(screen.getByLabelText("楼层"), "a4")
  await user.click(screen.getByRole("button", { name: "应用筛选" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith(expect.objectContaining({ buildingId: "a", floorId: "a4" })))

  revision = "revision-b"
  catalogMock.mockResolvedValueOnce(catalogB)
  await client.refetchQueries({ queryKey: housingKeys.catalog(accountId), exact: true })

  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 }))
  expect(screen.getByLabelText("楼栋")).toHaveValue("")
  expect(screen.getByLabelText("楼层")).toHaveValue("")
})

it("isolates a mismatched overview revision from both the DOM and query cache", async () => {
  const catalogB: HousingCatalog = { ...catalog, access_revision: "revision-b" }
  catalogMock.mockResolvedValue(catalogB)
  overviewMock.mockResolvedValueOnce(overview({ access_revision: "revision-a" })).mockImplementation(() => new Promise(() => undefined))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  renderPage(client)

  await waitFor(() => expect(overviewMock).toHaveBeenCalled())
  await waitFor(() => expect(screen.queryByRole("article", { name: /房间 409/ })).not.toBeInTheDocument())
  const cached = client.getQueryCache().findAll({ queryKey: housingKeys.overviews(accountId, "revision-b") })
  expect(cached.every((query) => {
    const data = query.state.data as HousingOverview | null | undefined
    return data == null || data.access_revision === "revision-b"
  })).toBe(true)
})

it("does not loop or retain stale data when the coordinated mismatch retry also mismatches", async () => {
  const catalogB: HousingCatalog = { ...catalog, access_revision: "revision-b" }
  catalogMock.mockResolvedValue(catalogB)
  overviewMock.mockResolvedValue(overview({ access_revision: "revision-a" }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  renderPage(client)

  await waitFor(() => expect(overviewMock).toHaveBeenCalledTimes(2))
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })

  expect(overviewMock).toHaveBeenCalledTimes(2)
  expect(screen.queryByRole("article", { name: /房间 409/ })).not.toBeInTheDocument()
  const cached = client.getQueryCache().findAll({ queryKey: housingKeys.overviews(accountId, "revision-b") })
  expect(cached.every((query) => {
    const data = query.state.data as HousingOverview | null | undefined
    return data == null || data.access_revision === "revision-b"
  })).toBe(true)
})

it("purges a mismatched overview cache after unmounting during cancellation", async () => {
  let finishCancellation: (() => void) | undefined
  const catalogB: HousingCatalog = { ...catalog, access_revision: "revision-b" }
  catalogMock.mockResolvedValue(catalogB)
  overviewMock.mockResolvedValue(overview({ access_revision: "revision-a" }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(client, "cancelQueries").mockImplementationOnce(() => new Promise((resolve) => {
    finishCancellation = resolve
  }))
  const view = renderPage(client)

  await waitFor(() => expect(finishCancellation).toBeDefined())
  const mismatchedKey = housingKeys.overview(accountId, "revision-b", { page: 1, pageSize: 20 })
  view.unmount()
  await act(async () => {
    finishCancellation?.()
    await Promise.resolve()
  })

  expect(client.getQueryData(mismatchedKey)).toBeUndefined()
})

it("confirms the exact authorized room-card deletion and supports cancellation", async () => {
  overviewMock.mockResolvedValue(overview({ rooms: [
    room({ id: "room-409", room_number: "409", allowed_actions: { ...noLifecycleActions, edit: false, delete: true, verify: false } }),
    room({ id: "room-410", room_number: "410", allowed_actions: { ...noLifecycleActions, edit: false, delete: true, verify: false } }),
  ] }))
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "删除房间 410" }))
  const dialog = screen.getByRole("dialog", { name: "删除房间 410" })
  expect(dialog).toHaveTextContent("410")
  await user.click(within(dialog).getByRole("button", { name: "取消" }))
  expect(deleteRoomMock).not.toHaveBeenCalled()

  await user.click(screen.getByRole("button", { name: "删除房间 410" }))
  await user.click(screen.getByRole("button", { name: "确认删除房间 410" }))
  expect(deleteRoomMock).toHaveBeenCalledWith("room-410")
})

it("removes a successfully deleted room from the cross-page batch selection", async () => {
  const selectedRoom = room({
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: true, verify: true },
  })
  overviewMock.mockResolvedValue(overview({ rooms: [selectedRoom] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(client, "invalidateQueries").mockResolvedValue()
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 409进行核对" }))
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  expect(screen.getByText("已选择 1 间待核对房间")).toBeVisible()

  await user.click(within(screen.getByRole("article", { name: /房间 409/ })).getByRole("button", { name: "删除房间 409" }))
  await user.click(screen.getByRole("button", { name: "确认删除房间 409" }))

  await waitFor(() => expect(deleteRoomMock).toHaveBeenCalledWith("room-409"))
  expect(screen.queryByText("已选择 1 间待核对房间")).not.toBeInTheDocument()
})

it("keeps batch selections across pagination loading and submits latest versions from each seen page", async () => {
  let resolveSecondPage: ((value: HousingOverview) => void) | undefined
  const first = room({
    id: "room-page-1", room_number: "401", version: 3,
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  const second = room({
    id: "room-page-2", room_number: "402", version: 5,
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  overviewMock.mockImplementation((filters = {}) => {
    if ((filters.page ?? 1) === 1) return Promise.resolve(overview({ rooms: [first], page: 1, total: 40 }))
    return new Promise((resolve) => { resolveSecondPage = resolve })
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 401进行核对" }))
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  expect(screen.getByText("已选择 1 间待核对房间")).toBeVisible()

  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(await screen.findByText("正在读取房态数据")).toBeVisible()
  resolveSecondPage?.(overview({ rooms: [second], page: 2, total: 40 }))
  const roomList = await screen.findByRole("list", { name: "房间列表" })
  await user.click(within(roomList).getByRole("checkbox", { name: "选择房间 402进行核对" }))
  expect(screen.getByText("已选择 2 间待核对房间")).toBeVisible()

  overviewMock.mockResolvedValueOnce(overview({ rooms: [{ ...second, version: 9 }], page: 2, total: 40 }))
  await client.refetchQueries({ queryKey: housingKeys.overview(accountId, "revision-a", { page: 2, pageSize: 20 }), exact: true })
  await waitFor(() => expect(within(screen.getByRole("list", { name: "房间列表" })).getByRole("checkbox", { name: "选择房间 402进行核对" })).toBeChecked())
  await user.click(screen.getByRole("button", { name: "批量核对 2 间房" }))

  expect(verifyRoomsMock).toHaveBeenCalledWith([
    { room_id: "room-page-1", version: 3 },
    { room_id: "room-page-2", version: 9 },
  ])
})

it("revokes off-page selections and old overview caches when the authorized catalog scope changes", async () => {
  let scope: "a" | "b" = "a"
  const catalogA: HousingCatalog = {
    access_revision: "revision-a",
    can_create_building: false,
    buildings: [{ ...catalog.buildings[0], allowed_actions: ["edit"] }],
    floors: catalog.floors.filter((floor) => floor.building_id === "a"),
  }
  const catalogB: HousingCatalog = {
    access_revision: "revision-b",
    can_create_building: false,
    buildings: [{ ...catalog.buildings[1], allowed_actions: ["edit"] }],
    floors: catalog.floors.filter((floor) => floor.building_id === "b"),
  }
  const roomA = room({
    id: "room-a-401", room_number: "401", version: 3,
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  const roomASecondPage = room({
    id: "room-a-402", room_number: "402", version: 4,
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  const roomB = room({
    id: "room-b-201", building_id: "b", floor_id: "b2", building_code: "B", building_name: "二号楼",
    floor_number: 2, floor_name: "二层", room_number: "201", version: 7,
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  catalogMock.mockImplementation(async () => scope === "a" ? catalogA : catalogB)
  overviewMock.mockImplementation(async (filters = {}) => {
    if (scope === "b") return overview({ access_revision: "revision-b", rooms: [roomB], page: filters.page ?? 1, total: 40 })
    return overview({ rooms: [(filters.page ?? 1) === 1 ? roomA : roomASecondPage], page: filters.page ?? 1, total: 40 })
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const oldDetailKey = housingKeys.detail(accountId, "revision-a", "room-old-detail")
  const oldCohortKey = housingKeys.cohortOptions(accountId, "revision-a", "room-old-detail")
  const oldTransferKey = housingKeys.transferOptions(accountId, "revision-a", "stay-old")
  const newDetailKey = housingKeys.detail(accountId, "revision-b", "room-new-detail")
  const otherAccountKey = housingKeys.detail("account-other", "revision-a", "room-other")
  client.setQueryData(oldDetailKey, { private_name: "旧详情住户" })
  client.setQueryData(oldCohortKey, { private_name: "旧营期候选" })
  client.setQueryData(oldTransferKey, { private_name: "旧调房候选" })
  client.setQueryData(newDetailKey, { private_name: "新修订详情" })
  client.setQueryData(otherAccountKey, { private_name: "其他账号详情" })
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 401进行核对" }))
  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(await screen.findByRole("article", { name: /房间 402/ })).toBeVisible()
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  expect(screen.getByText("已选择 1 间待核对房间")).toBeVisible()

  scope = "b"
  await act(async () => {
    await Promise.all([
      client.refetchQueries({ queryKey: housingKeys.catalog(accountId), exact: true }),
      client.refetchQueries({ queryKey: housingKeys.overviews(accountId, "revision-a") }),
    ])
  })

  expect(await screen.findByRole("article", { name: /房间 201/ })).toBeVisible()
  await waitFor(() => expect(screen.queryByText("已选择 1 间待核对房间")).not.toBeInTheDocument())
  expect(screen.queryByRole("heading", { name: "目录管理", level: 2 })).not.toBeInTheDocument()
  expect(client.getQueryData(housingKeys.overview(accountId, "revision-a", { page: 1, pageSize: 20 }))).toBeUndefined()
  expect(client.getQueryData(oldDetailKey)).toBeUndefined()
  expect(client.getQueryData(oldCohortKey)).toBeUndefined()
  expect(client.getQueryData(oldTransferKey)).toBeUndefined()
  expect(client.getQueryData(newDetailKey)).toEqual({ private_name: "新修订详情" })
  expect(client.getQueryData(otherAccountKey)).toEqual({ private_name: "其他账号详情" })
  expect(verifyRoomsMock).not.toHaveBeenCalled()

  await user.click(screen.getByRole("checkbox", { name: "选择房间 201进行核对" }))
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  await user.click(screen.getByRole("button", { name: "批量核对 1 间房" }))
  expect(verifyRoomsMock).toHaveBeenCalledWith([{ room_id: "room-b-201", version: 7 }])
})

it("ignores a stale room mutation settlement after the authorized catalog scope changes", async () => {
  let scope: "a" | "b" = "a"
  let finishVerification: ((value: HousingRoom) => void) | undefined
  const catalogA: HousingCatalog = {
    ...catalog,
    buildings: [catalog.buildings[0]],
    floors: catalog.floors.filter((floor) => floor.building_id === "a"),
  }
  const catalogB: HousingCatalog = {
    ...catalog,
    access_revision: "revision-b",
    buildings: [catalog.buildings[1]],
    floors: catalog.floors.filter((floor) => floor.building_id === "b"),
  }
  const roomA = room({
    id: "room-stale-mutation", room_number: "401",
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  const roomB = room({
    id: "room-current-scope", building_id: "b", floor_id: "b2", building_code: "B", building_name: "二号楼",
    floor_number: 2, floor_name: "二层", room_number: "201",
  })
  catalogMock.mockImplementation(async () => scope === "a" ? catalogA : catalogB)
  overviewMock.mockImplementation(async () => overview({ access_revision: scope === "a" ? "revision-a" : "revision-b", rooms: [scope === "a" ? roomA : roomB] }))
  verifyRoomMock.mockImplementationOnce(() => new Promise((resolve) => { finishVerification = resolve }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, "invalidateQueries")
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("button", { name: "核对房间 401" }))
  scope = "b"
  await act(async () => {
    await Promise.all([
      client.refetchQueries({ queryKey: housingKeys.catalog(accountId), exact: true }),
      client.refetchQueries({ queryKey: housingKeys.overviews(accountId, "revision-a") }),
    ])
  })
  expect(await screen.findByRole("article", { name: /房间 201/ })).toBeVisible()

  await act(async () => {
    finishVerification?.(roomA)
    await Promise.resolve()
  })
  expect(invalidate).not.toHaveBeenCalled()
})

it("ignores a stale catalog mutation settlement after the shared authorization epoch changes", async () => {
  let scope: "a" | "b" = "a"
  let finishVerification: ((value: HousingRoom) => void) | undefined
  const catalogA: HousingCatalog = {
    ...catalog,
    buildings: [catalog.buildings[0]],
    floors: catalog.floors.filter((floor) => floor.building_id === "a"),
  }
  const catalogB: HousingCatalog = {
    ...catalog,
    access_revision: "revision-b",
    buildings: [{ ...catalog.buildings[1], allowed_actions: ["edit"] }],
    floors: catalog.floors.filter((floor) => floor.building_id === "b"),
  }
  const roomA = room({
    id: "room-stale-catalog-mutation", room_number: "401",
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  const roomB = room({
    id: "room-current-catalog-scope", building_id: "b", floor_id: "b2", building_code: "B", building_name: "二号楼",
    floor_number: 2, floor_name: "二层", room_number: "201",
  })
  catalogMock.mockImplementation(async () => scope === "a" ? catalogA : catalogB)
  overviewMock.mockImplementation(async () => overview({ access_revision: scope === "a" ? "revision-a" : "revision-b", rooms: [scope === "a" ? roomA : roomB] }))
  verifyRoomMock.mockImplementationOnce(() => new Promise((resolve) => { finishVerification = resolve }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, "invalidateQueries")
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("button", { name: "目录管理" }))
  const panel = screen.getByRole("heading", { name: "目录管理", level: 2 }).closest("section")!
  await user.click(within(panel).getByRole("button", { name: "核对房间 401" }))
  expect(verifyRoomMock).toHaveBeenCalledTimes(1)

  scope = "b"
  await act(async () => {
    await Promise.all([
      client.refetchQueries({ queryKey: housingKeys.catalog(accountId), exact: true }),
      client.refetchQueries({ queryKey: housingKeys.overviews(accountId, "revision-a") }),
    ])
  })
  expect(await screen.findByRole("article", { name: /房间 201/ })).toBeVisible()
  await waitFor(() => expect(screen.queryByRole("heading", { name: "目录管理", level: 2 })).not.toBeInTheDocument())
  expect(screen.getByRole("button", { name: "目录管理" })).toBeDisabled()

  await act(async () => {
    finishVerification?.(roomA)
    await Promise.resolve()
  })

  expect(invalidate).not.toHaveBeenCalled()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(screen.queryByText(/已选择/)).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "目录管理" })).toBeEnabled()
  expect(verifyRoomMock).toHaveBeenCalledTimes(1)
})

it("clears cross-page selection when applying or resetting business filters", async () => {
  const selectable = room({
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  overviewMock.mockResolvedValue(overview({ rooms: [selectable], total: 40 }))
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 409进行核对" }))
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  expect(screen.getByText("已选择 1 间待核对房间")).toBeVisible()
  await user.type(screen.getByLabelText("搜索房间"), "409")
  await user.click(screen.getByRole("button", { name: "应用筛选" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith(expect.objectContaining({ query: "409", page: 1 })))
  expect(screen.queryByText("已选择 1 间待核对房间")).not.toBeInTheDocument()
  expect(screen.getAllByRole("checkbox", { name: "选择房间 409进行核对" })[0]).not.toBeChecked()

  await user.click(screen.getAllByRole("checkbox", { name: "选择房间 409进行核对" })[0])
  expect(screen.getByText("已选择 1 间待核对房间")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "重置筛选" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 }))
  expect(screen.queryByText("已选择 1 间待核对房间")).not.toBeInTheDocument()
  expect(screen.getAllByRole("checkbox", { name: "选择房间 409进行核对" })[0]).not.toBeChecked()
})

it.each([
  ["overview", 403, "permission_denied"],
  ["catalog", 404, "housing_resource_not_found"],
] as const)("clears cross-page batch selection after a private %s refetch", async (target, status, code) => {
  const selectable = room({
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  const staleFilteredRoom = room({ id: "room-stale-filter", room_number: "498" })
  overviewMock.mockResolvedValue(overview({ rooms: [selectable] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 409进行核对" }))
  client.setQueryData(
    housingKeys.overview(accountId, "revision-a", { buildingId: "a", page: 1, pageSize: 20 }),
    overview({ rooms: [staleFilteredRoom] }),
  )
  client.setQueryData(
    housingKeys.overview(accountId, "revision-a", { query: "498", page: 1, pageSize: 20 }),
    overview({ rooms: [staleFilteredRoom] }),
  )
  const privateError = new ProblemDetailsError({
    type: "about:blank", title: "Private", status, detail: "没有权限读取房态",
    code, request_id: "selection-private",
  })
  const targetMock = target === "overview" ? overviewMock : catalogMock
  targetMock.mockRejectedValueOnce(privateError as never)
  await client.refetchQueries({
    queryKey: target === "overview"
      ? housingKeys.overviews(accountId, catalog.access_revision)
      : housingKeys.catalog(accountId),
  })
  expect(await screen.findByText("房态数据不可访问")).toBeVisible()
  await waitFor(() => {
    expect(client.getQueryData(housingKeys.catalog(accountId))).toBeUndefined()
    expect(client.getQueriesData({ queryKey: housingKeys.overviews(accountId, catalog.access_revision) }).every(([, data]) => data === undefined)).toBe(true)
  })
  expect(client.getQueryData(housingKeys.overview(accountId, "revision-a", { buildingId: "a", page: 1, pageSize: 20 }))).toBeUndefined()
  expect(client.getQueryData(housingKeys.overview(accountId, "revision-a", { query: "498", page: 1, pageSize: 20 }))).toBeUndefined()
  expect(screen.queryByRole("article", { name: /房间 409/ })).not.toBeInTheDocument()
  expect(screen.queryByRole("article", { name: /房间 498/ })).not.toBeInTheDocument()
  expect(screen.queryByLabelText("搜索房间")).not.toBeInTheDocument()

  if (target === "overview") overviewMock.mockResolvedValueOnce(overview({ rooms: [selectable] }))
  else catalogMock.mockResolvedValueOnce(catalog)
  await user.click(screen.getByRole("button", { name: "重试" }))
  expect(await screen.findByRole("checkbox", { name: "选择房间 409进行核对" })).not.toBeChecked()
})

it.each([
  [403, "permission_denied"],
  [404, "housing_resource_not_found"],
] as const)("purges only the current account housing tree when batch verification returns %s", async (status, code) => {
  const first = room({
    id: "room-private-page-1", room_number: "401", version: 3,
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  const second = room({
    id: "room-private-page-2", room_number: "402", version: 5,
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  })
  overviewMock.mockImplementation(async (filters = {}) => overview({
    rooms: [(filters.page ?? 1) === 1 ? first : second],
    page: filters.page ?? 1,
    total: 40,
  }))
  verifyRoomsMock.mockRejectedValueOnce(new ProblemDetailsError({
    type: "about:blank", title: "Private", status, detail: "私有资源详情",
    code, request_id: `cross-page-${status}`,
  }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const otherAccountKey = housingKeys.detail("account-other", "revision-other", "room-other")
  client.setQueryData(otherAccountKey, { occupant: "其他账号住户" })
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 401进行核对" }))
  await user.click(screen.getByRole("button", { name: "下一页" }))
  await user.click(await screen.findByRole("checkbox", { name: "选择房间 402进行核对" }))
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  await user.click(screen.getByRole("button", { name: "批量核对 2 间房" }))

  expect(await screen.findByText("房态数据不可访问")).toBeVisible()
  expect(screen.queryByText("已选择 2 间待核对房间")).not.toBeInTheDocument()
  expect(screen.queryByRole("checkbox", { name: /选择房间/ })).not.toBeInTheDocument()
  await waitFor(() => {
    expect(client.getQueriesData({ queryKey: housingKeys.root(accountId) }).every(([, data]) => data === undefined)).toBe(true)
  })
  expect(client.getQueryData(otherAccountKey)).toEqual({ occupant: "其他账号住户" })
})

it.each([
  ["target removal", overview({ rooms: [], total: 0 }), null],
  ["edit revocation", overview({ rooms: [room({ allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: false } })] }), null],
  ["private 403", null, new ProblemDetailsError({ type: "about:blank", title: "Private", status: 403, detail: "权限已撤销", code: "permission_denied", request_id: "dialog-403" })],
  ["private 404", null, new ProblemDetailsError({ type: "about:blank", title: "Private", status: 404, detail: "资源不可见", code: "housing_resource_not_found", request_id: "dialog-404" })],
] as const)("unmounts a room-card edit dialog after %s and cannot submit its stale draft", async (_label, nextOverview, nextError) => {
  overviewMock.mockResolvedValue(overview({ rooms: [room({ allowed_actions: { ...noLifecycleActions, edit: true, delete: false, verify: false } })] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("button", { name: "编辑房间 409" }))
  await user.type(screen.getByLabelText("备注"), "不应提交")
  if (nextError) overviewMock.mockRejectedValueOnce(nextError)
  else overviewMock.mockResolvedValueOnce(nextOverview!)
  await client.refetchQueries({ queryKey: housingKeys.overviews(accountId, catalog.access_revision) })

  await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑房间 409" })).not.toBeInTheDocument())
  expect(updateRoomMock).not.toHaveBeenCalled()
})

it("unmounts a room-card delete dialog when the latest delete action is revoked", async () => {
  const deletable = room({ allowed_actions: { ...noLifecycleActions, edit: false, delete: true, verify: false } })
  overviewMock.mockResolvedValue(overview({ rooms: [deletable] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  renderPage(client)

  await user.click(await screen.findByRole("button", { name: "删除房间 409" }))
  overviewMock.mockResolvedValueOnce(overview({ rooms: [{ ...deletable, allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: false } }] }))
  await client.refetchQueries({ queryKey: housingKeys.overviews(accountId, catalog.access_revision) })

  await waitFor(() => expect(screen.queryByRole("dialog", { name: "删除房间 409" })).not.toBeInTheDocument())
  expect(deleteRoomMock).not.toHaveBeenCalled()
})

it("never mounts catalog forms for a fully read-only server response", async () => {
  renderPage()
  await screen.findByRole("article", { name: /房间 409/ })
  expect(screen.queryByRole("button", { name: "目录管理" })).not.toBeInTheDocument()
  expect(screen.queryByRole("heading", { name: "目录管理" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "新建楼栋" })).not.toBeInTheDocument()
})

it("keeps an open catalog dialog mounted while current data refreshes in the background", async () => {
  const managedCatalog: HousingCatalog = {
    ...catalog,
    buildings: catalog.buildings.map((building, index) => index === 0 ? { ...building, allowed_actions: ["edit"] } : building),
  }
  catalogMock.mockResolvedValue(managedCatalog)
  overviewMock.mockResolvedValue(overview({ rooms: [room({ allowed_actions: { ...noLifecycleActions, edit: true, delete: false, verify: false } })] }))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  renderPage(queryClient)
  await user.click(await screen.findByRole("button", { name: "目录管理" }))
  const panel = screen.getByRole("heading", { name: "目录管理", level: 2 }).closest("section")!
  await user.click(within(panel).getByRole("button", { name: "编辑房间 409" }))
  await user.clear(screen.getByLabelText("房间号")); await user.type(screen.getByLabelText("房间号"), "410")

  catalogMock.mockImplementationOnce(() => new Promise(() => undefined))
  void queryClient.invalidateQueries({ queryKey: housingKeys.catalog(accountId), exact: true })
  await waitFor(() => expect(catalogMock).toHaveBeenCalledTimes(2))
  expect(screen.getByRole("dialog", { name: "编辑房间 409" })).toBeVisible()
  expect(screen.getByLabelText("房间号")).toHaveValue("410")
})

it("focuses the stable page owner when a private refetch removes the dialog trigger and catalog panel", async () => {
  const managedCatalog: HousingCatalog = {
    ...catalog,
    buildings: catalog.buildings.map((building, index) => index === 0 ? { ...building, allowed_actions: ["edit"] } : building),
  }
  catalogMock.mockResolvedValue(managedCatalog)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  const { container } = renderPage(queryClient)

  await user.click(await screen.findByRole("button", { name: "目录管理" }))
  await user.click(screen.getByRole("button", { name: "编辑楼栋 一号楼" }))
  const pageOwner = container.querySelector<HTMLElement>(".housingPage")!
  catalogMock.mockRejectedValueOnce(new ProblemDetailsError({
    type: "about:blank", title: "Private", status: 403, detail: "没有权限读取房态",
    code: "permission_denied", request_id: "dialog-focus-private",
  }))

  await queryClient.refetchQueries({ queryKey: housingKeys.catalog(accountId), exact: true })

  expect(await screen.findByText("房态数据不可访问")).toBeVisible()
  expect(screen.queryByRole("button", { name: "目录管理" })).not.toBeInTheDocument()
  expect(screen.queryByRole("heading", { name: "目录管理", level: 2 })).not.toBeInTheDocument()
  await waitFor(() => expect(pageOwner).toHaveFocus())
})

it("clears room-card and catalog selection-limit notices when selection shrinks from the other surface", async () => {
  const selectableRooms = ["500", "501"].map((roomNumber, index) => room({
    id: `room-limit-${roomNumber}`,
    room_number: roomNumber,
    version: index + 1,
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
  }))
  overviewMock.mockResolvedValue(overview({ rooms: selectableRooms, total: 2 }))
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "目录管理" }))
  const roomList = screen.getByRole("list", { name: "房间列表" })
  const panel = screen.getByRole("heading", { name: "目录管理", level: 2 }).closest("section")!
  const selectionAtLimit = new Map<string, number>()
  for (let index = 0; index < 499; index += 1) selectionAtLimit.set(`cross-page-${index}`, index + 1)
  selectionAtLimit.set("room-limit-500", 1)
  updateSelectionMock.mockImplementationOnce(() => ({ next: selectionAtLimit, limited: false }))
  await user.click(within(roomList).getByRole("checkbox", { name: "选择房间 500进行核对" }))
  expect(within(panel).getByText("已选择 500 间待核对房间")).toBeVisible()

  await user.click(within(roomList).getByRole("checkbox", { name: "选择房间 501进行核对" }))
  expect(await screen.findByText("跨页选择最多支持 500 间房，请先取消部分选择。")).toBeVisible()
  await user.click(within(panel).getByRole("button", { name: "清空选择" }))
  await waitFor(() => expect(screen.queryByText("跨页选择最多支持 500 间房，请先取消部分选择。")).not.toBeInTheDocument())

  updateSelectionMock.mockImplementationOnce(() => ({ next: selectionAtLimit, limited: false }))
  await user.click(within(roomList).getByRole("checkbox", { name: "选择房间 500进行核对" }))
  await user.click(within(panel).getByRole("checkbox", { name: "选择房间 501进行核对" }))
  expect(await within(panel).findByText("跨页选择最多支持 500 间房，请先取消部分选择。")).toBeVisible()
  await user.click(within(roomList).getByRole("checkbox", { name: "选择房间 500进行核对" }))
  await waitFor(() => expect(within(panel).queryByText("跨页选择最多支持 500 间房，请先取消部分选择。")).not.toBeInTheDocument())
})

it("uses a heading-labelled section instead of nesting another main landmark", async () => {
  const { container } = renderPage()

  await screen.findByRole("article", { name: /房间 409/ })
  const page = container.querySelector(".housingPage")
  expect(page?.tagName).toBe("SECTION")
  expect(page).toHaveAttribute("aria-labelledby", "housing-overview-title")
  expect(screen.getByRole("heading", { name: "住宿房态", level: 1 })).toHaveAttribute(
    "id",
    "housing-overview-title",
  )
  expect(container.querySelector("main")).not.toBeInTheDocument()
})

it("sizes summary and room grids from the housing content container", () => {
  expect(housingStyles).toMatch(
    /\.housingPage__content\s*\{[^}]*container:\s*housing-content\s*\/\s*inline-size;/s,
  )
  expect(housingStyles).toMatch(
    /@container housing-content \(min-width:\s*340px\)\s*\{\s*\.housingSummary\s*\{[^}]*repeat\(4,[^}]*}\s*\.housingRoomGrid\s*\{[^}]*repeat\(2,/s,
  )
  expect(housingStyles).toMatch(
    /@container housing-content \(min-width:\s*640px\)\s*\{\s*\.housingSummary\s*\{[^}]*repeat\(8,[^}]*}\s*\.housingRoomGrid\s*\{[^}]*repeat\(3,/s,
  )
  expect(housingStyles).toMatch(
    /@container housing-content \(min-width:\s*720px\)\s*\{\s*\.housingRoomGrid\s*\{[^}]*repeat\(4,/s,
  )
  expect(housingStyles).not.toMatch(/@media \(min-width:\s*1024px\)[^{]*\{[^}]*\.housing(?:Summary|RoomGrid)/s)
})

it("uses accessible muted text for the 11px room detail labels", () => {
  expect(housingStyles).toMatch(
    /\.housingRoomCard__details dt\s*\{[^}]*color:\s*var\(--color-text-muted\);[^}]*font-size:\s*11px;/s,
  )
})

it("keeps catalog dialogs viewport-bound and catalog controls touch sized", () => {
  expect(housingStyles).toMatch(/\.housingDialogBackdrop\s*\{[^}]*position:\s*fixed;[^}]*inset:\s*0;/s)
  expect(housingStyles).toMatch(/\.housingDialog\s*\{[^}]*max-height:\s*min\(calc\(100dvh - 32px\),\s*760px\);[^}]*overflow-x:\s*hidden;[^}]*overflow-y:\s*auto;/s)
  expect(housingStyles).toMatch(/\.housingCatalog button,[^}]*\.housingDialog button\s*\{[^}]*min-height:\s*44px;/s)
  expect(housingStyles).toMatch(/\.housingDialog__header button\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;[^}]*padding:\s*0;/s)
})

it("narrows floor options by building and clears a now-invalid floor", async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("article", { name: /房间 409/ })

  await user.selectOptions(screen.getByLabelText("楼栋"), "a")
  const floor = screen.getByLabelText("楼层")
  expect(within(floor).getByRole("option", { name: "四层" })).toBeVisible()
  expect(within(floor).queryByRole("option", { name: "二层" })).not.toBeInTheDocument()
  await user.selectOptions(floor, "a4")
  await user.selectOptions(screen.getByLabelText("楼栋"), "b")
  expect(floor).toHaveValue("")
  expect(within(floor).getByRole("option", { name: "二层" })).toBeVisible()
})

it("applies every filter independently and resets pagination on filter changes", async () => {
  overviewMock.mockResolvedValue(overview({ page: 2, total: 41 }))
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("article", { name: /房间 409/ })

  await user.click(screen.getByRole("button", { name: "下一页" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })))
  await user.selectOptions(screen.getByLabelText("楼栋"), "a")
  await user.selectOptions(screen.getByLabelText("楼层"), "a4")
  await user.type(screen.getByLabelText("搜索房间"), "409")
  await user.click(screen.getByRole("checkbox", { name: "单人间" }))
  await user.click(screen.getByRole("checkbox", { name: "双人间" }))
  await user.click(screen.getByRole("checkbox", { name: "已核对" }))
  await user.click(screen.getByRole("checkbox", { name: "问题房" }))
  await user.selectOptions(screen.getByLabelText("当前入住性别"), "unspecified")
  await user.selectOptions(screen.getByLabelText("入住状态"), "occupied")
  await user.selectOptions(screen.getByLabelText("可入住状态"), "assignable")
  await user.click(screen.getByRole("button", { name: "应用筛选" }))

  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({
    buildingId: "a", floorId: "a4", query: "409", roomTypes: ["single", "double"],
    verificationStatuses: ["verified"], serviceStatuses: ["problem"], currentGender: "unspecified",
    occupancy: "occupied", assignability: "assignable", page: 1, pageSize: 20,
  } satisfies HousingFiltersValue))
  expect(screen.getByLabelText("入住状态")).toHaveValue("occupied")
  expect(screen.getByLabelText("可入住状态")).toHaveValue("assignable")
})

it("normalizes reverse-order multi-select clicks before building the query key", async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("article", { name: /房间 409/ })

  for (const label of ["功能间", "单人间", "已核对", "待核对", "已停用", "正常"]) {
    await user.click(screen.getByRole("checkbox", { name: label }))
  }
  await user.click(screen.getByRole("button", { name: "应用筛选" }))

  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({
    roomTypes: ["single", "functional"],
    verificationStatuses: ["pending_verification", "verified"],
    serviceStatuses: ["available", "disabled"],
    page: 1,
    pageSize: 20,
  } satisfies HousingFiltersValue))
})

it("resets all filter controls and requests page one", async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("article", { name: /房间 409/ })
  await user.type(screen.getByLabelText("搜索房间"), "409")
  await user.click(screen.getByRole("button", { name: "重置筛选" }))

  expect(screen.getByLabelText("搜索房间")).toHaveValue("")
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 }))
})

it("hides old cards while a changed query loads and then shows the empty state", async () => {
  let resolveNext: ((value: HousingOverview) => void) | undefined
  overviewMock
    .mockResolvedValueOnce(overview())
    .mockImplementationOnce(() => new Promise((resolve) => { resolveNext = resolve }))
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("article", { name: /房间 409/ })

  await user.type(screen.getByLabelText("搜索房间"), "none")
  await user.click(screen.getByRole("button", { name: "应用筛选" }))
  expect(await screen.findByText("正在读取房态数据")).toBeVisible()
  expect(screen.queryByRole("article", { name: /房间 409/ })).not.toBeInTheDocument()
  resolveNext?.(overview({ rooms: [], total: 0, summary: { ...overview().summary, total_rooms: 0 } }))
  expect(await screen.findByRole("heading", { name: "没有匹配的房间" })).toBeVisible()
})

it("clamps a now-out-of-range page directly to the new last page", async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  overviewMock.mockImplementation(async (filters = {}) => {
    const page = filters.page ?? 1
    return overview({ page, total: 100, rooms: [room({ id: `room-${page}`, room_number: String(page) })] })
  })
  const user = userEvent.setup()
  renderPage(queryClient)
  expect(await screen.findByText("第 1 / 5 页")).toBeVisible()

  for (const page of [2, 3, 4, 5]) {
    await user.click(screen.getByRole("button", { name: "下一页" }))
    expect(await screen.findByText(`第 ${page} / 5 页`)).toBeVisible()
  }

  let resolveNormalizedPage: ((value: HousingOverview) => void) | undefined
  overviewMock
    .mockResolvedValueOnce(overview({
      page: 5,
      total: 1,
      rooms: [room({ id: "stale-room", room_number: "失效页房间" })],
    }))
    .mockImplementationOnce(() => new Promise((resolve) => { resolveNormalizedPage = resolve }))

  await queryClient.refetchQueries({
    queryKey: housingKeys.overview(accountId, "revision-a", { page: 5, pageSize: 20 }),
    exact: true,
  })

  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 }))
  expect(screen.queryByText("第 5 / 1 页")).not.toBeInTheDocument()
  expect(screen.queryByRole("article", { name: /失效页房间/ })).not.toBeInTheDocument()
  expect(screen.getByText("正在读取房态数据")).toBeVisible()

  resolveNormalizedPage?.(overview())
  expect(await screen.findByText("第 1 / 1 页")).toBeVisible()
})

it.each([
  ["overview", 403, "housing_access_denied"],
  ["catalog", 404, "housing_scope_not_found"],
] as const)("hides cached catalog and room data when a %s refetch returns %s", async (target, status, code) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  renderPage(queryClient)

  expect(await screen.findByRole("article", { name: /房间 409/ })).toBeVisible()
  expect(screen.getByLabelText("楼栋")).toBeVisible()

  const privateError = new ProblemDetailsError({
    type: "about:blank", title: "Private", status, detail: "没有权限读取房态", code,
    request_id: "req-housing",
  })
  if (target === "overview") overviewMock.mockRejectedValueOnce(privateError)
  else catalogMock.mockRejectedValueOnce(privateError)

  await queryClient.refetchQueries({
    queryKey: target === "overview"
      ? housingKeys.overviews(accountId, catalog.access_revision)
      : housingKeys.catalog(accountId),
  })

  const alert = await screen.findByRole("alert")
  expect(alert).toHaveTextContent("没有权限读取房态")
  expect(screen.queryByRole("article")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("楼栋")).not.toBeInTheDocument()

  if (target === "overview") overviewMock.mockImplementationOnce(() => new Promise(() => undefined))
  else catalogMock.mockImplementationOnce(() => new Promise(() => undefined))
  await user.click(within(alert).getByRole("button", { name: "重试" }))
  await waitFor(() => {
    expect(target === "overview" ? overviewMock : catalogMock).toHaveBeenCalledTimes(3)
  })
  expect(screen.getByRole("alert")).toBeVisible()
  expect(screen.queryByRole("article")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("楼栋")).not.toBeInTheDocument()
})

it("uses a collapsed modal filter sheet below 720px and restores focus on close", async () => {
  setWidth(360)
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("article", { name: /房间 409/ })
  const trigger = screen.getByRole("button", { name: "打开筛选" })
  expect(screen.queryByRole("dialog", { name: "筛选房间" })).not.toBeInTheDocument()

  await user.click(trigger)
  const dialog = screen.getByRole("dialog", { name: "筛选房间" })
  expect(dialog).toHaveAttribute("aria-modal", "true")
  expect(document.body.style.overflow).toBe("hidden")
  const close = within(dialog).getByRole("button", { name: "关闭筛选" })
  close.focus()
  fireEvent.keyDown(document, { key: "Tab" })
  expect(within(dialog).getByLabelText("楼栋")).toHaveFocus()
  fireEvent.keyDown(document, { key: "Escape" })
  expect(screen.queryByRole("dialog", { name: "筛选房间" })).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
  expect(document.body.style.overflow).toBe("")
})

it.each(["Escape", "关闭筛选"] as const)("discards an unapplied mobile draft after %s and reopen", async (closeAction) => {
  setWidth(360)
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("article", { name: /房间 409/ })

  const trigger = screen.getByRole("button", { name: "打开筛选" })
  await user.click(trigger)
  await user.type(screen.getByLabelText("搜索房间"), "draft-only")
  if (closeAction === "Escape") fireEvent.keyDown(document, { key: "Escape" })
  else await user.click(screen.getByRole("button", { name: closeAction }))

  expect(screen.queryByRole("dialog", { name: "筛选房间" })).not.toBeInTheDocument()
  await user.click(trigger)
  expect(screen.getByLabelText("搜索房间")).toHaveValue("")
  expect(overviewMock).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 })
})

it.each(["first", "second"] as const)("keeps body scroll locked when the %s of two filter sheets closes first", (firstToClose) => {
  setWidth(360)
  document.body.style.overflow = "clip"
  const view = render(<FilterPair firstOpen secondOpen />)
  expect(document.body.style.overflow).toBe("hidden")

  view.rerender(
    <FilterPair
      firstOpen={firstToClose !== "first"}
      secondOpen={firstToClose !== "second"}
    />,
  )
  expect(document.body.style.overflow).toBe("hidden")

  view.rerender(<FilterPair firstOpen={false} secondOpen={false} />)
  expect(document.body.style.overflow).toBe("clip")
  view.unmount()
  expect(document.body.style.overflow).toBe("clip")
})

it("closes the mobile filter sheet while reset refetches and restores focus without reopening", async () => {
  setWidth(360)
  let resolveReset: ((value: HousingOverview) => void) | undefined
  overviewMock
    .mockResolvedValueOnce(overview())
    .mockResolvedValueOnce(overview())
    .mockImplementationOnce(() => new Promise((resolve) => { resolveReset = resolve }))
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("article", { name: /房间 409/ })

  await user.click(screen.getByRole("button", { name: "打开筛选" }))
  await user.type(screen.getByLabelText("搜索房间"), "409")
  await user.click(screen.getByRole("button", { name: "应用筛选" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ query: "409", page: 1, pageSize: 20 }))
  expect(await screen.findByRole("article", { name: /房间 409/ })).toBeVisible()

  const trigger = screen.getByRole("button", { name: "打开筛选" })
  await user.click(trigger)
  const dialog = screen.getByRole("dialog", { name: "筛选房间" })
  expect(document.body.style.overflow).toBe("hidden")
  await user.click(within(dialog).getByRole("button", { name: "重置筛选" }))

  expect(overviewMock).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 })
  expect(await screen.findByText("正在读取房态数据")).toBeVisible()
  expect(screen.queryByRole("dialog", { name: "筛选房间" })).not.toBeInTheDocument()
  expect(document.body.style.overflow).toBe("")
  resolveReset?.(overview())

  expect(await screen.findByRole("article", { name: /房间 409/ })).toBeVisible()
  expect(screen.queryByRole("dialog", { name: "筛选房间" })).not.toBeInTheDocument()
  expect(document.body.style.overflow).toBe("")
  expect(trigger).toHaveFocus()
})

it("closes the mobile filter sheet across the 720px breakpoint without focusing its removed trigger", async () => {
  setWidth(360)
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("article", { name: /房间 409/ })
  const trigger = screen.getByRole("button", { name: "打开筛选" })

  await user.click(trigger)
  expect(screen.getByRole("dialog", { name: "筛选房间" })).toBeVisible()
  expect(document.body.style.overflow).toBe("hidden")
  await user.type(screen.getByLabelText("搜索房间"), "draft-only")
  const focusSpy = vi.spyOn(trigger, "focus")

  setWidth(1024)
  await waitFor(() => expect(screen.queryByRole("button", { name: "打开筛选" })).not.toBeInTheDocument())
  expect(screen.queryByRole("dialog", { name: "筛选房间" })).not.toBeInTheDocument()
  expect(document.body.style.overflow).toBe("")
  expect(focusSpy).not.toHaveBeenCalled()
  expect(screen.getByLabelText("搜索房间")).toHaveValue("")

  setWidth(360)
  const reopenedTrigger = await screen.findByRole("button", { name: "打开筛选" })
  expect(screen.queryByRole("dialog", { name: "筛选房间" })).not.toBeInTheDocument()
  expect(document.body.style.overflow).toBe("")
  await user.click(reopenedTrigger)
  expect(screen.getByLabelText("搜索房间")).toHaveValue("")
})
