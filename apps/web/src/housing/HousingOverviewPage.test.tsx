import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { personHousingKeys } from "../people/housingApi"
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
  exportHousingRoomStatus,
  getHousingCatalog,
  listHousingOverview,
  updateHousingRoom,
  updateHousingRoomsServiceStatus,
  verifyHousingRoom,
  verifyHousingRooms,
} from "./api"
import { HousingFilters } from "./HousingFilters"
import { HousingOverviewPage } from "./HousingOverviewPage"
import type { HousingOverviewPageProps } from "./HousingOverviewPage"
import { housingKeys } from "./housingKeys"
import { housingRoomSelection, updateHousingSelection } from "./selection"
import type { HousingRoomSelection } from "./selection"
import { getHousingPropertyCapabilities } from "./propertyApi"

vi.mock(import("./propertyApi"), async (importOriginal) => ({ ...await importOriginal(), getHousingPropertyCapabilities: vi.fn().mockResolvedValue({ can_submit: false, can_review: false, can_export: false, show_basic_export: false }), listHousingPropertyChanges: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 }) }))

const accountId = "account-housing-manager"
const exportAuth = vi.hoisted(() => ({ permissions: ["housing.read"] }))

vi.mock(import("../auth/AuthProvider"), () => ({
  useAuth: () => ({
    status: "authenticated" as const,
    user: {
      account_id: accountId,
      person_id: "person-housing-manager",
      full_name: "房态管理员",
      email: "housing-manager@example.test",
      force_password_change: false,
      permissions: exportAuth.permissions,
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
  exportHousingRoomStatus: vi.fn(),
  getHousingCatalog: vi.fn(),
  listHousingOverview: vi.fn(),
  updateHousingRoom: vi.fn(),
  updateHousingRoomsServiceStatus: vi.fn(),
  verifyHousingRoom: vi.fn(),
  verifyHousingRooms: vi.fn(),
}))

vi.mock(import("./selection"), async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, updateHousingSelection: vi.fn(actual.updateHousingSelection) as typeof actual.updateHousingSelection }
})

const catalogMock = vi.mocked(getHousingCatalog)
const overviewMock = vi.mocked(listHousingOverview)
const deleteRoomMock = vi.mocked(deleteHousingRoom)
const updateRoomMock = vi.mocked(updateHousingRoom)
const verifyRoomMock = vi.mocked(verifyHousingRoom)
const verifyRoomsMock = vi.mocked(verifyHousingRooms)
const updateSelectionMock = vi.mocked(updateHousingSelection)

const housingStyles = readFileSync(resolve(cwd(), "src/housing/housing.css"), "utf8")
const noLifecycleActions = { create_stay: false, report_issue: false, view_detail: false, manage_care: false, complete_cleaning: false, manage_photos: false }

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

it("labels ready as absence of pending cleaning rather than a recorded cleaning event", () => {
  render(<HousingFilters catalog={catalog} value={{}} open onOpenChange={ignoreOpenChange} onApply={ignoreApply} onReset={ignoreReset} />)
  expect(screen.getByRole("checkbox", { name: "无退房待保洁" })).toBeVisible()
  expect(screen.queryByRole("checkbox", { name: "已清洁" })).toBeNull()
})

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
    daily_cleaning_enabled: false, cleaning_status: "ready", cleaning_pending_at: null, cleaning_completed_at: null, photo_count: 0,
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
      pending_cleaning_rooms: 0,
      total_rooms: 8, occupied_rooms: 3, unoccupied_rooms: 5, assignable_rooms: 4, unassignable_rooms: 4, full_rooms: 4,
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
  vi.mocked(getHousingPropertyCapabilities).mockResolvedValue({ can_submit: false, can_review: false, can_export: false, show_basic_export: false })
  exportAuth.permissions = ["housing.read"]
  setWidth(1024)
  catalogMock.mockReset()
  overviewMock.mockReset()
  deleteRoomMock.mockReset().mockResolvedValue()
  updateRoomMock.mockReset().mockResolvedValue(room())
  verifyRoomMock.mockReset().mockResolvedValue(room())
  verifyRoomsMock.mockReset().mockResolvedValue([])
  vi.mocked(updateHousingRoomsServiceStatus).mockReset().mockResolvedValue([])
  catalogMock.mockResolvedValue(catalog)
  overviewMock.mockResolvedValue(overview())
})

it("keeps the dedicated basic export but no property approvals on the overview", async () => {
  vi.mocked(getHousingPropertyCapabilities).mockResolvedValue({ can_submit: false, can_review: true, can_export: true, show_basic_export: true })
  renderPage()
  expect(await screen.findByRole("region", { name: "物业基础导出" })).toBeVisible()
  expect(screen.queryByRole("region", { name: "物业变更申请" })).toBeNull()
  expect(screen.queryByRole("region", { name: "物业变更审批" })).toBeNull()
  expect(screen.queryByRole("button", { name: "导出" })).not.toBeInTheDocument()
})

it.each([['maintenance', '设为维修'], ['available', '设为可用'], ['disabled', '设为停用']] as const)("submits confirmed %s status from the visible bulk toolbar without opening the catalog", async (status, label) => {
  overviewMock.mockResolvedValue(overview({ rooms: [room({ version: 7, allowed_actions: { ...noLifecycleActions, edit: true, verify: false, delete: false } })] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  renderPage(client)
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByRole('combobox', { name: '批量操作' }), status)
  expect(screen.queryByRole('heading', { name: '目录管理' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('checkbox', { name: `选择房间 409` }))
  await user.click(screen.getByRole('button', { name: `批量${label} 1 间房` }))
  const confirmation = screen.getByRole('dialog', { name: '确认批量操作' })
  expect(confirmation).toHaveTextContent(label)
  expect(confirmation).toHaveTextContent('1 间')
  expect(confirmation).toHaveTextContent('整批不修改')
  expect(updateHousingRoomsServiceStatus).not.toHaveBeenCalled()
  await user.click(within(confirmation).getByRole('button', { name: '取消' }))
  expect(screen.getByRole('checkbox', { name: `选择房间 409` })).toBeChecked()
  await user.click(screen.getByRole('button', { name: `批量${label} 1 间房` }))
  await user.click(screen.getByRole('button', { name: '确认执行' }))
  await waitFor(() => expect(updateHousingRoomsServiceStatus).toHaveBeenCalledWith([{ room_id: 'room-409', version: 7 }], status))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.queryByText(/已选择 1 间/)).not.toBeInTheDocument()
  expect(invalidate).toHaveBeenCalledWith({ queryKey: housingKeys.catalog(accountId), exact: true })
  expect(invalidate).toHaveBeenCalledWith({ queryKey: housingKeys.overviews(accountId, 'revision-a') })
  expect(invalidate).toHaveBeenCalledWith({ queryKey: personHousingKeys.root(accountId) })
})

it('keeps verify-only room selection when switching actions but blocks incompatible status actions', async () => {
  overviewMock.mockResolvedValue(overview({ rooms: [room({ verification_status: 'pending_verification', allowed_actions: { ...noLifecycleActions, edit: false, verify: true, delete: false } })] }))
  renderPage()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('checkbox', { name: '选择房间 409' }))
  await user.selectOptions(screen.getByRole('combobox', { name: '批量操作' }), 'maintenance')
  expect(screen.getByRole('checkbox', { name: '选择房间 409' })).toBeChecked()
  expect(screen.getByRole('button', { name: '批量设为维修 1 间房' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('409')
  expect(screen.getByRole('alert')).toHaveTextContent('无房间编辑权限')
  await user.selectOptions(screen.getByRole('combobox', { name: '批量操作' }), 'verify')
  expect(screen.getByRole('checkbox', { name: '选择房间 409' })).toBeChecked()
  expect(screen.getByRole('button', { name: '批量核对 1 间房' })).toBeEnabled()
})

it('selects an already verified available room before choosing a compatible bulk action', async () => {
  overviewMock.mockResolvedValue(overview({ rooms: [room({ allowed_actions: { ...noLifecycleActions, edit: true, verify: false, delete: false } })] }))
  renderPage()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('checkbox', { name: '选择房间 409' }))
  expect(screen.getByRole('button', { name: '批量核对 1 间房' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('409：已核对，请选择维修/可用/停用或取消该房间')
  await user.selectOptions(screen.getByRole('combobox', { name: '批量操作' }), 'maintenance')
  expect(screen.getByRole('checkbox', { name: '选择房间 409' })).toBeChecked()
  await user.click(screen.getByRole('button', { name: '批量设为维修 1 间房' }))
  await user.click(screen.getByRole('button', { name: '确认执行' }))
  await waitFor(() => expect(updateHousingRoomsServiceStatus).toHaveBeenCalledWith([{ room_id: 'room-409', version: 1 }], 'maintenance'))
  expect(verifyHousingRooms).not.toHaveBeenCalled()
})

it('validates off-page selected room eligibility without silently dropping incompatible selections', async () => {
  const first = room({ allowed_actions: { ...noLifecycleActions, edit: true, verify: false, delete: false } })
  const second = room({ id: 'verify-only', room_number: '410', verification_status: 'pending_verification', allowed_actions: { ...noLifecycleActions, edit: false, verify: true, delete: false } })
  overviewMock.mockImplementation(async (filters) => overview({ rooms: [(filters?.page ?? 1) === 1 ? first : second], page: filters?.page ?? 1, total: 40 }))
  renderPage()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('checkbox', { name: '选择房间 409' }))
  await user.click(screen.getByRole('button', { name: '下一页' }))
  await user.click(await screen.findByRole('checkbox', { name: '选择房间 410' }))
  expect(screen.getByRole('button', { name: '批量核对 2 间房' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('409')
  await user.selectOptions(screen.getByRole('combobox', { name: '批量操作' }), 'maintenance')
  expect(screen.getByRole('button', { name: '批量设为维修 2 间房' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('410：无房间编辑权限')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(updateHousingRoomsServiceStatus).not.toHaveBeenCalled()
  await user.click(screen.getByRole('checkbox', { name: '选择房间 410' }))
  expect(screen.getByRole('button', { name: '批量设为维修 1 间房' })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: '上一页' }))
  expect(await screen.findByRole('checkbox', { name: '选择房间 409' })).toBeChecked()
})

it('blocks submission when refresh changes selected room action eligibility inside confirmation', async () => {
  const selectable = room({ verification_status: 'pending_verification', allowed_actions: { ...noLifecycleActions, edit: true, verify: true, delete: false } })
  overviewMock.mockResolvedValue(overview({ rooms: [selectable] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  renderPage(client)
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByRole('combobox', { name: '批量操作' }), 'maintenance')
  await user.click(screen.getByRole('checkbox', { name: '选择房间 409' }))
  await user.click(screen.getByRole('button', { name: '批量设为维修 1 间房' }))
  overviewMock.mockResolvedValue(overview({ rooms: [{ ...selectable, version: 2, allowed_actions: { ...selectable.allowed_actions, edit: false } }] }))
  await act(() => client.refetchQueries({ queryKey: housingKeys.overviews(accountId, 'revision-a') }))
  expect(await screen.findByRole('button', { name: '确认执行' })).toBeDisabled()
  expect(within(screen.getByRole('dialog')).getByText(/409：无房间编辑权限/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '确认执行' }))
  expect(updateHousingRoomsServiceStatus).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '取消' }))
  expect(screen.getByRole('checkbox', { name: '选择房间 409' })).toBeChecked()
  await user.selectOptions(screen.getByRole('combobox', { name: '批量操作' }), 'verify')
  await user.click(screen.getByRole('button', { name: '批量核对 1 间房' }))
  await user.click(screen.getByRole('button', { name: '确认执行' }))
  await waitFor(() => expect(verifyHousingRooms).toHaveBeenCalledWith([{ room_id: 'room-409', version: 2 }]))
})

it('closes confirmation after a selected room loses all selection authority and requires review of the reduced batch', async () => {
  const first = room({ allowed_actions: { ...noLifecycleActions, edit: true, verify: false, delete: false } })
  const second = room({ id: 'room-410', room_number: '410', allowed_actions: first.allowed_actions })
  overviewMock.mockResolvedValue(overview({ rooms: [first, second], total: 2 }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  renderPage(client)
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByRole('combobox', { name: '批量操作' }), 'maintenance')
  await user.click(screen.getByRole('checkbox', { name: '选择房间 409' }))
  await user.click(screen.getByRole('checkbox', { name: '选择房间 410' }))
  await user.click(screen.getByRole('button', { name: '批量设为维修 2 间房' }))
  const oldConfirm = screen.getByRole('button', { name: '确认执行' })
  overviewMock.mockResolvedValue(overview({ rooms: [{ ...first, allowed_actions: { ...first.allowed_actions, edit: false } }, second], total: 2 }))
  await act(() => client.refetchQueries({ queryKey: housingKeys.overviews(accountId, 'revision-a') }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.queryByRole('checkbox', { name: '选择房间 409' })).not.toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: '选择房间 410' })).toBeChecked()
  expect(screen.getByText('已选择 1 间房间')).toBeVisible()
  const notice = screen.getByRole('alert')
  expect(notice).toHaveTextContent('请重新核对所选房间后确认')
  expect(notice).not.toHaveTextContent('409')
  fireEvent.click(oldConfirm)
  expect(updateHousingRoomsServiceStatus).not.toHaveBeenCalled()
})

it('clears status selection and confirmation when local permissions change even before an access revision refresh', async () => {
  overviewMock.mockResolvedValue(overview({ rooms: [room({ allowed_actions: { ...noLifecycleActions, edit: true, verify: false, delete: false } })] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const { rerender } = renderPage(client)
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByRole('combobox', { name: '批量操作' }), 'maintenance')
  await user.click(screen.getByRole('checkbox', { name: '选择房间 409' }))
  await user.click(screen.getByRole('button', { name: '批量设为维修 1 间房' }))
  exportAuth.permissions = []
  rerender(<QueryClientProvider client={client}><HousingOverviewPage /></QueryClientProvider>)
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.queryByText(/已选择/)).not.toBeInTheDocument()
})

it('disables export until initial catalog and revision-matching overview are ready', async () => {
  exportAuth.permissions = ['housing.read', 'housing.reports.export_sensitive']
  let resolveCatalog!: (value: HousingCatalog) => void
  let resolveOverview!: (value: HousingOverview) => void
  catalogMock.mockReturnValue(new Promise((resolve) => { resolveCatalog = resolve }))
  overviewMock.mockReturnValue(new Promise((resolve) => { resolveOverview = resolve }))
  renderPage()
  expect(screen.getByRole('button', { name: '导出' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '导出' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  await act(async () => resolveCatalog(catalog))
  expect(screen.getByRole('button', { name: '导出' })).toBeDisabled()
  await act(async () => resolveOverview(overview()))
  await waitFor(() => expect(screen.getByRole('button', { name: '导出' })).toBeEnabled())
  await userEvent.click(screen.getByRole('button', { name: '导出' }))
  expect(screen.getByRole('dialog')).toBeVisible()
})

it('does not expose bulk mutations for read-only room access', async () => {
  renderPage()
  await screen.findByRole('article')
  expect(screen.queryByRole('combobox', { name: '批量操作' })).not.toBeInTheDocument()
  expect(screen.queryByRole('checkbox', { name: /选择房间/ })).not.toBeInTheDocument()
})

it('preserves latest cross-page status versions and enforces the 500-room ceiling', async () => {
  const first = room({ id: 'room-1', version: 3, allowed_actions: { ...noLifecycleActions, edit: true, verify: false, delete: false } })
  const second = room({ id: 'room-2', room_number: '410', version: 4, allowed_actions: first.allowed_actions })
  overviewMock.mockImplementation(async (filters) => overview({ rooms: [(filters?.page ?? 1) === 1 ? first : second], page: filters?.page ?? 1, total: 40 }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  renderPage(client)
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByRole('combobox', { name: '批量操作' }), 'disabled')
  await user.click(screen.getByRole('checkbox', { name: '选择房间 409' }))
  await user.click(screen.getByRole('button', { name: '下一页' }))
  await user.click(await screen.findByRole('checkbox', { name: '选择房间 410' }))
  overviewMock.mockResolvedValueOnce(overview({ rooms: [{ ...second, version: 9 }], page: 2, total: 40 }))
  await act(() => client.refetchQueries({ queryKey: housingKeys.overview(accountId, 'revision-a', { page: 2, pageSize: 20 }), exact: true }))
  await user.click(screen.getByRole('button', { name: '批量设为停用 2 间房' }))
  await user.click(screen.getByRole('button', { name: '确认执行' }))
  await waitFor(() => expect(updateHousingRoomsServiceStatus).toHaveBeenCalledWith([{ room_id: 'room-1', version: 3 }, { room_id: 'room-2', version: 9 }], 'disabled'))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const atLimit = new Map(Array.from({ length: 500 }, (_, index) => [`off-page-${index}`, housingRoomSelection({ ...second, id: `off-page-${index}` })]))
  updateSelectionMock.mockImplementationOnce(() => ({ next: atLimit, limited: false }))
  await user.click(await screen.findByRole('checkbox', { name: '选择房间 410' }))
  await user.click(screen.getByRole('checkbox', { name: '选择房间 410' }))
  expect(await screen.findByText('跨页选择最多支持 500 间房，请先取消部分选择。')).toBeVisible()
  expect(screen.getByLabelText('跨页已选总数 500 间')).toBeVisible()
})

it('retains failed batch selection and renders per-room conflict reasons without reporting success', async () => {
  overviewMock.mockResolvedValue(overview({ rooms: [room({ allowed_actions: { ...noLifecycleActions, edit: true, verify: false, delete: false } })] }))
  vi.mocked(updateHousingRoomsServiceStatus).mockRejectedValue(new ProblemDetailsError({ type: 'about:blank', title: 'Conflict', status: 409, code: 'housing_batch_conflict', detail: '整批未修改', request_id: 'batch-conflict', errors: [{ field: 'rooms.room-409', message: '409：已入住，不能设为维修' }] }))
  renderPage()
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByRole('combobox', { name: '批量操作' }), 'maintenance')
  await user.click(screen.getByRole('checkbox', { name: '选择房间 409' }))
  await user.click(screen.getByRole('button', { name: '批量设为维修 1 间房' }))
  await user.click(screen.getByRole('button', { name: '确认执行' }))
  expect(await screen.findByText('409：已入住，不能设为维修')).toBeVisible()
  expect(screen.getByRole('alert')).toHaveTextContent('整批未修改')
  expect(updateHousingRoomsServiceStatus).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('dialog')).toBeVisible()
  expect(screen.getByRole('checkbox', { name: '选择房间 409' })).toBeChecked()
  expect(screen.queryByText('批量操作已完成')).not.toBeInTheDocument()
})

it('removes an earlier success notice before a later batch fails', async () => {
  overviewMock.mockResolvedValue(overview({ rooms: [room({ allowed_actions: { ...noLifecycleActions, edit: true, verify: false, delete: false } })] }))
  renderPage()
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByRole('combobox', { name: '批量操作' }), 'maintenance')
  await user.click(screen.getByRole('checkbox', { name: '选择房间 409' }))
  await user.click(screen.getByRole('button', { name: '批量设为维修 1 间房' }))
  await user.click(screen.getByRole('button', { name: '确认执行' }))
  expect(await screen.findByText('批量操作已完成')).toBeVisible()
  vi.mocked(updateHousingRoomsServiceStatus).mockRejectedValueOnce(new Error('network failure'))
  await user.click(screen.getByRole('checkbox', { name: '选择房间 409' }))
  await user.click(screen.getByRole('button', { name: '批量设为维修 1 间房' }))
  await user.click(screen.getByRole('button', { name: '确认执行' }))
  expect(await screen.findByText('操作暂时失败，请稍后重试')).toBeVisible()
  expect(screen.queryByText('批量操作已完成')).not.toBeInTheDocument()
  expect(updateHousingRoomsServiceStatus).toHaveBeenCalledTimes(2)
})

it.each([403, 404])('purges private selected rooms after a batch status %s', async (status) => {
  overviewMock.mockResolvedValue(overview({ rooms: [room({ allowed_actions: { ...noLifecycleActions, edit: true, verify: false, delete: false } })] }))
  vi.mocked(updateHousingRoomsServiceStatus).mockRejectedValue(new ProblemDetailsError({ type: 'about:blank', title: 'Private', status, code: 'housing_access_denied', detail: '目标不可用', request_id: 'batch-private', errors: [{ field: 'rooms.private', message: '不得显示的房间信息' }] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  renderPage(client)
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByRole('combobox', { name: '批量操作' }), 'disabled')
  await user.click(screen.getByRole('checkbox', { name: '选择房间 409' }))
  await user.click(screen.getByRole('button', { name: '批量设为停用 1 间房' }))
  await user.click(screen.getByRole('button', { name: '确认执行' }))
  expect(await screen.findByText('房态数据不可访问')).toBeVisible()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByText(/已选择/)).not.toBeInTheDocument()
  expect(screen.queryByText('不得显示的房间信息')).not.toBeInTheDocument()
  expect(client.getQueriesData({ queryKey: housingKeys.root(accountId) }).every(([, value]) => value === undefined)).toBe(true)
})

it.each([["housing.read"], ["housing.read", "housing.reports.export_sensitive", "housing.rooms.manage"]])("discards a late snapshot download after access changes to %j", async (...permissions) => {
  exportAuth.permissions = ["housing.read", "housing.reports.export_sensitive"]
  let finish!: (value: { blob: Blob; filename: string }) => void
  vi.mocked(exportHousingRoomStatus).mockReturnValue(new Promise((resolve) => { finish = resolve }))
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined)
  const { rerender } = renderPage()
  await userEvent.click(await screen.findByRole("button", { name: "导出" }))
  await userEvent.type(screen.getByLabelText("导出用途（必填）"), "房态核对")
  await userEvent.click(screen.getByRole("button", { name: "开始导出" }))
  await waitFor(() => expect(exportHousingRoomStatus).toHaveBeenCalled())
  exportAuth.permissions = permissions
  rerender(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><HousingOverviewPage /></QueryClientProvider>)
  await act(async () => finish({ blob: new Blob(["private"]), filename: "housing-room-status-20261023.csv" }))
  expect(click).not.toHaveBeenCalled()
  click.mockRestore()
})

it("applies turnover cleaning and weekly frequency filters and a summary shortcut", async () => {
  renderPage()
  await screen.findByRole("article")
  const user = userEvent.setup()
  await user.click(screen.getByLabelText("退房待保洁"))
  await user.selectOptions(screen.getByLabelText("保洁频率"), "weekly")
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith(expect.objectContaining({ cleaningStatuses: ["pending"], cleaningFrequencies: ["weekly"] })))
  await screen.findByRole("article")
  await user.click(screen.getByRole("button", { name: "按退房待保洁筛选房间" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith(expect.objectContaining({ cleaningStatuses: ["pending"] })))
})

afterEach(() => {
  document.body.style.overflow = ""
  vi.clearAllMocks()
})

it("renders every summary, filtered count, semantic grid, and Shanghai as-of time", async () => {
  renderPage()

  expect(await screen.findByRole("article", { name: /房间 409/ })).toBeVisible()
  for (const text of ["房间总数 8", "已用房间数 3", "空房 5", "可入住 4", "满房 4", "问题房 1", "待核对 2", "总床位 12", "当前入住 3", "剩余床位 9", "入住率 25.00%", "单人间 4", "双人间 2", "三人间 1", "自定义房型 0", "功能间 1"]) {
    expect(screen.getByRole("button", { description: text })).toBeVisible()
  }
  expect(screen.getByText("数据截至 2026-08-04")).toBeVisible()
  expect(screen.getByText("筛选结果 1 间")).toBeVisible()
  expect(screen.getByRole("list", { name: "房间列表" })).toBeVisible()
  expect(screen.getByRole("article", { name: "房间 409：空闲，可入住" })).toBeVisible()
})

it("uses the returned vacant count instead of subtracting occupied rooms from all rooms", async () => {
  overviewMock.mockResolvedValue(overview({ summary: { ...overview().summary, total_rooms: 8, occupied_rooms: 3, unoccupied_rooms: 4 } }))
  renderPage()
  expect(await screen.findByRole("button", { name: "按空房筛选房间", description: "空房 4" })).toBeVisible()
  expect(screen.getByRole("button", { name: "查看全部房间", description: "房间总数 8" })).toBeVisible()
})

it("counts occupied rooms separately from people and keeps unique summary descriptions", async () => {
  overviewMock.mockResolvedValue(overview({ summary: { ...overview().summary, occupied_rooms: 2, current_occupants: 7 } }))
  renderPage()
  expect(await screen.findByRole("button", { name: "按已用房间数筛选房间", description: "已用房间数 2" })).toBeVisible()
  expect(screen.getByRole("button", { name: "按已入住筛选房间（当前入住）", description: "当前入住 7" })).toBeVisible()
  const ids = Array.from(document.querySelectorAll('[id^="housing-summary-"]'), (element) => element.id)
  expect(new Set(ids).size).toBe(ids.length)
})

it.each([
  ["每日保洁", "cleaning_daily", "daily_cleaning_rooms", 2],
  ["每周保洁", "cleaning_weekly", "weekly_cleaning_rooms", 3],
  ["按需保洁", "cleaning_on_demand", "on_demand_cleaning_rooms", 1],
] as const)("keeps building and summary scope when applying the %s shortcut", async (label, quickFilter, key, count) => {
  const actionLabel = label === "按需保洁" ? "按需保洁筛选房间" : `按${label}筛选房间`
  const data = overview()
  overviewMock.mockResolvedValue({ ...data, summary: { ...data.summary, [key]: count } })
  renderPage()
  const user = userEvent.setup()
  await screen.findByRole("article")
  await user.selectOptions(screen.getByLabelText("楼栋"), "a")
  const button = await screen.findByRole("button", { name: actionLabel, description: `${label} ${count}` })
  await user.click(button)
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ buildingId: "a", quickFilter, page: 1, pageSize: 20 }))
  expect(await screen.findByRole("button", { name: actionLabel })).toHaveAccessibleDescription(`${label} ${count}`)
  await user.selectOptions(screen.getByLabelText("保洁频率"), "weekly")
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ buildingId: "a", cleaningFrequencies: ["weekly"], page: 1, pageSize: 20 }))
})

it("withholds room actions and blocks an open bulk confirmation during a delayed same-scope refresh", async () => {
  overviewMock.mockResolvedValue(overview({ rooms: [room({ verification_status: "pending_verification", allowed_actions: { ...noLifecycleActions, edit: true, delete: false, verify: true } })] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  renderPage(client)
  await user.click(await screen.findByRole("button", { name: "目录管理" }))
  await user.click(within(screen.getByRole("list", { name: "房间列表" })).getByRole("checkbox", { name: "选择房间 409" }))
  await user.click(screen.getByRole("button", { name: "批量核对 1 间房" }))
  let finish!: (value: HousingOverview) => void
  overviewMock.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  let refresh!: Promise<void>
  act(() => { refresh = client.refetchQueries({ queryKey: housingKeys.overviews(accountId, "revision-a") }) })
  await waitFor(() => expect(screen.getByRole("button", { name: "确认执行" })).toBeDisabled())
  expect(screen.queryByRole("article", { name: /房间 409/ })).not.toBeInTheDocument()
  expect(screen.getByLabelText("搜索房间")).toBeVisible()
  fireEvent.click(screen.getByRole("button", { name: "确认执行" }))
  expect(verifyRoomsMock).not.toHaveBeenCalled()
  await act(async () => { finish(overview()); await refresh })
})

it("replaces shortcuts without changing sidebar predicates or summaries and clears them on a left change", async () => {
  let finish!: (value: HousingOverview) => void
  const user = userEvent.setup()
  renderPage()
  await user.selectOptions(await screen.findByLabelText("楼栋"), "a")
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ buildingId: "a", page: 1, pageSize: 20 }))
  await user.click(screen.getByRole("checkbox", { name: "双人间" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ buildingId: "a", roomTypes: ["double"], page: 1, pageSize: 20 }))
  overviewMock.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  await user.click(screen.getByRole("button", { name: "按空房筛选房间" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ buildingId: "a", roomTypes: ["double"], quickFilter: "vacant", page: 1, pageSize: 20 }))
  expect(screen.getByRole("button", { name: "查看全部房间", description: "房间总数 8" })).toBeVisible()
  expect(screen.queryByRole("article")).not.toBeInTheDocument()
  expect(screen.getByLabelText("楼栋")).toHaveValue("a")
  await act(async () => finish(overview()))
  await user.click(screen.getByRole("button", { name: "按可入住筛选房间" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ buildingId: "a", roomTypes: ["double"], quickFilter: "assignable", page: 1, pageSize: 20 }))
  expect(screen.getByLabelText("可入住状态")).toHaveValue("")
  await user.selectOptions(screen.getByLabelText("可入住状态"), "full")
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ buildingId: "a", roomTypes: ["double"], assignability: "full", page: 1, pageSize: 20 }))
})

it.each(["林晨", "星河Ace"])("searches current resident %s and preserves building, floor and shortcuts", async (query) => {
  const user = userEvent.setup()
  renderPage()
  await user.selectOptions(await screen.findByLabelText("楼栋"), "a")
  await user.selectOptions(screen.getByLabelText("楼层"), "a4")
  const input = screen.getByRole("textbox", { name: "搜索房间" })
  expect(input).toHaveAttribute("placeholder", "房号、楼栋、楼层、入住人姓名或昵称")
  overviewMock.mockResolvedValue(overview({ rooms: [room({ room_number: "A-名字命中" })] }))
  await user.type(input, ` ${query} `)
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({
    buildingId: "a", floorId: "a4", query, page: 1, pageSize: 20,
  }))
  expect(await screen.findByRole("heading", { name: "A-名字命中" })).toBeVisible()
  await user.click(screen.getByRole("button", { name: "按已用房间数筛选房间" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({
    buildingId: "a", floorId: "a4", query, quickFilter: "occupied", page: 1, pageSize: 20,
  }))
  expect(screen.getByLabelText("楼栋")).toHaveValue("a")
  expect(screen.getByLabelText("楼层")).toHaveValue("a4")
  expect(input).toHaveValue(` ${query} `)
})

it("keeps the actual search node focused through delayed requests and debounces raw search after IME", async () => {
  const pending: Array<(value: HousingOverview) => void> = []
  renderPage()
  const input = await screen.findByLabelText("搜索房间")
  overviewMock.mockImplementation(() => new Promise((resolve) => pending.push(resolve)))
  const user = userEvent.setup()
  await user.type(input, " 4 ")
  expect(input).toHaveValue(" 4 ")
  expect(overviewMock).toHaveBeenCalledTimes(1)
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ query: "4", page: 1, pageSize: 20 }))
  expect(screen.getByLabelText("搜索房间")).toBe(input)
  expect(input).toHaveFocus()
  await user.type(input, "09")
  expect(input).toHaveValue(" 4 09")
  await act(async () => pending[0](overview()))
  expect(screen.getByLabelText("搜索房间")).toBe(input)
  expect(input).toHaveFocus()
  fireEvent.compositionStart(input)
  fireEvent.change(input, { target: { value: "  中文 " } })
  const calls = overviewMock.mock.calls.length
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)) })
  expect(overviewMock).toHaveBeenCalledTimes(calls)
  fireEvent.compositionEnd(input)
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ query: "中文", page: 1, pageSize: 20 }))
  expect(input).toHaveValue("  中文 ")
  expect(input).toHaveFocus()
})

it("ignores a late response from the previous base scope while successive selects remain mounted", async () => {
  const pending = new Map<string, (value: HousingOverview) => void>()
  renderPage()
  const building = await screen.findByLabelText("楼栋")
  overviewMock.mockImplementation((filters) => new Promise((resolve) => pending.set(filters!.buildingId!, resolve)))
  const user = userEvent.setup()
  await user.selectOptions(building, "a")
  expect(screen.getByLabelText("楼栋")).toBe(building)
  expect(building).toHaveFocus()
  await user.selectOptions(building, "b")
  expect(screen.getByLabelText("楼栋")).toBe(building)
  await act(async () => pending.get("b")!(overview({ summary: { ...overview().summary, total_rooms: 2 } })))
  expect(await screen.findByRole("button", { name: "查看全部房间", description: "房间总数 2" })).toBeVisible()
  await act(async () => pending.get("a")!(overview({ summary: { ...overview().summary, total_rooms: 99 } })))
  expect(screen.getByRole("button", { name: "查看全部房间", description: "房间总数 2" })).toBeVisible()
  expect(building).toHaveValue("b")
  expect(building).toHaveFocus()
})

it.each([
  ["已用房间数", "按已用房间数筛选房间", { quickFilter: "occupied" }],
  ["空房", "按空房筛选房间", { quickFilter: "vacant" }],
  ["可入住", "按可入住筛选房间", { quickFilter: "assignable" }],
  ["满房", "按满房筛选房间", { quickFilter: "full" }],
  ["问题房", "按问题房筛选房间", { quickFilter: "problem" }],
  ["待核对", "按待核对筛选房间", { quickFilter: "pending_verification" }],
  ["退房待保洁", "按退房待保洁筛选房间", { quickFilter: "pending_cleaning" }],
  ["总床位", "按有效住宿床位房间筛选（总床位统计）", { quickFilter: "usable" }],
  ["入住率", "按有效住宿床位房间筛选（入住率统计）", { quickFilter: "usable" }],
  ["当前入住", "按已入住筛选房间（当前入住）", { quickFilter: "occupied" }],
  ["剩余床位", "按有剩余床位房间筛选（剩余床位）", { quickFilter: "remaining" }],
  ["单人间", "按单人间筛选房间", { quickFilter: "single" }],
  ["双人间", "按双人间筛选房间", { quickFilter: "double" }],
  ["三人间", "按三人间筛选房间", { quickFilter: "triple" }],
  ["自定义房型", "按自定义房型筛选房间", { quickFilter: "custom" }],
  ["功能间", "按功能间筛选房间", { quickFilter: "functional" }],
] as const)("filters the room list when the %s summary is clicked", async (_label, actionName, expected) => {
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: actionName }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith(
    expect.objectContaining({ ...expected, page: 1, pageSize: 20 }),
  ))
})

it("keeps the building, floor and other predicates when switching summary filters", async () => {
  const user = userEvent.setup()
  renderPage()
  await user.selectOptions(await screen.findByLabelText("楼栋"), "a")
  await user.selectOptions(screen.getByLabelText("楼层"), "a4")
  await user.type(screen.getByLabelText("搜索房间"), "409")
  await user.click(screen.getByRole("checkbox", { name: "双人间" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({
    buildingId: "a", floorId: "a4", query: "409", roomTypes: ["double"], page: 1, pageSize: 20,
  }))

  await user.click(await screen.findByRole("button", { name: "按空房筛选房间" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({
    buildingId: "a", floorId: "a4", query: "409", roomTypes: ["double"], quickFilter: "vacant", page: 1, pageSize: 20,
  }))
  await user.click(await screen.findByRole("button", { name: "按可入住筛选房间" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({
    buildingId: "a", floorId: "a4", query: "409", roomTypes: ["double"], quickFilter: "assignable", page: 1, pageSize: 20,
  }))
  expect(screen.getByLabelText("楼栋")).toHaveValue("a")
  expect(screen.getByLabelText("楼层")).toHaveValue("a4")
  expect(screen.getByLabelText("可入住状态")).toHaveValue("")
  expect(screen.queryByLabelText("入住状态")).not.toBeInTheDocument()
})

it("supports keyboard activation and clears room predicates without leaving the selected building", async () => {
  const user = userEvent.setup()
  renderPage()

  await user.selectOptions(await screen.findByLabelText("楼栋"), "a")
  await user.selectOptions(screen.getByLabelText("楼层"), "a4")
  await user.click(await screen.findByRole("button", { name: "按已用房间数筛选房间" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith(
    { buildingId: "a", floorId: "a4", quickFilter: "occupied", page: 1, pageSize: 20 },
  ))

  const totalRooms = screen.getByRole("button", { name: "查看全部房间" })
  totalRooms.focus()
  await user.keyboard("{Enter}")
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ buildingId: "a", floorId: "a4", page: 1, pageSize: 20 }))
  await user.click(screen.getByRole("button", { name: "重置筛选" }))
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
    personHousingKeys.root(accountId),
  ]))
})

it("invalidates only the current account's person summaries after a room-card rename", async () => {
  overviewMock.mockResolvedValue(overview({ rooms: [room({ allowed_actions: { ...noLifecycleActions, edit: true, delete: false, verify: false } })] }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const own = personHousingKeys.summary(accountId, "resident")
  const other = personHousingKeys.summary("other-account", "resident")
  client.setQueryData(own, { room_name: "409" })
  client.setQueryData(other, { room_name: "其他房间" })
  renderPage(client)
  await userEvent.click(await screen.findByRole("button", { name: "编辑房间 409" }))
  fireEvent.change(screen.getByLabelText("房间号"), { target: { value: "新房间号" } })
  await userEvent.click(screen.getByRole("button", { name: "保存房间" }))
  await waitFor(() => expect(client.getQueryState(own)?.isInvalidated).toBe(true))
  expect(client.getQueryState(other)?.isInvalidated).toBe(false)
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
    personHousingKeys.root(accountId),
    housingKeys.catalog(accountId), housingKeys.overviews(accountId, catalog.access_revision),
    personHousingKeys.root(accountId),
    housingKeys.catalog(accountId), housingKeys.overviews(accountId, catalog.access_revision),
    personHousingKeys.root(accountId),
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

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 409" }))
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
  expect(screen.getByText("已选择 1 间房间")).toBeVisible()
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

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 409" }))
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  expect(screen.getByText("已选择 1 间房间")).toBeVisible()

  await user.click(within(screen.getByRole("article", { name: /房间 409/ })).getByRole("button", { name: "删除房间 409" }))
  await user.click(screen.getByRole("button", { name: "确认删除房间 409" }))

  await waitFor(() => expect(deleteRoomMock).toHaveBeenCalledWith("room-409"))
  expect(screen.queryByText("已选择 1 间房间")).not.toBeInTheDocument()
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

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 401" }))
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  expect(screen.getByText("已选择 1 间房间")).toBeVisible()

  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(await screen.findByText("正在读取房态数据")).toBeVisible()
  resolveSecondPage?.(overview({ rooms: [second], page: 2, total: 40 }))
  const roomList = await screen.findByRole("list", { name: "房间列表" })
  await user.click(within(roomList).getByRole("checkbox", { name: "选择房间 402" }))
  expect(screen.getByText("已选择 2 间房间")).toBeVisible()

  overviewMock.mockResolvedValueOnce(overview({ rooms: [{ ...second, version: 9 }], page: 2, total: 40 }))
  await client.refetchQueries({ queryKey: housingKeys.overview(accountId, "revision-a", { page: 2, pageSize: 20 }), exact: true })
  await waitFor(() => expect(within(screen.getByRole("list", { name: "房间列表" })).getByRole("checkbox", { name: "选择房间 402" })).toBeChecked())
  await user.click(screen.getByRole("button", { name: "批量核对 2 间房" }))

  await user.click(screen.getByRole("button", { name: "确认执行" }))

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

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 401" }))
  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(await screen.findByRole("article", { name: /房间 402/ })).toBeVisible()
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  expect(screen.getByText("已选择 1 间房间")).toBeVisible()

  scope = "b"
  await act(async () => {
    await Promise.all([
      client.refetchQueries({ queryKey: housingKeys.catalog(accountId), exact: true }),
      client.refetchQueries({ queryKey: housingKeys.overviews(accountId, "revision-a") }),
    ])
  })

  expect(await screen.findByRole("article", { name: /房间 201/ })).toBeVisible()
  await waitFor(() => expect(screen.queryByText("已选择 1 间房间")).not.toBeInTheDocument())
  expect(screen.queryByRole("heading", { name: "目录管理", level: 2 })).not.toBeInTheDocument()
  expect(client.getQueryData(housingKeys.overview(accountId, "revision-a", { page: 1, pageSize: 20 }))).toBeUndefined()
  expect(client.getQueryData(oldDetailKey)).toBeUndefined()
  expect(client.getQueryData(oldCohortKey)).toBeUndefined()
  expect(client.getQueryData(oldTransferKey)).toBeUndefined()
  expect(client.getQueryData(newDetailKey)).toEqual({ private_name: "新修订详情" })
  expect(client.getQueryData(otherAccountKey)).toEqual({ private_name: "其他账号详情" })
  expect(verifyRoomsMock).not.toHaveBeenCalled()

  await user.click(screen.getByRole("checkbox", { name: "选择房间 201" }))
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  await user.click(screen.getByRole("button", { name: "批量核对 1 间房" }))
  await user.click(screen.getByRole("button", { name: "确认执行" }))
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

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 409" }))
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  expect(screen.getByText("已选择 1 间房间")).toBeVisible()
  await user.type(screen.getByLabelText("搜索房间"), "409")
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith(expect.objectContaining({ query: "409", page: 1 })))
  expect(screen.queryByText("已选择 1 间房间")).not.toBeInTheDocument()
  expect(screen.getAllByRole("checkbox", { name: "选择房间 409" })[0]).not.toBeChecked()

  await user.click(screen.getAllByRole("checkbox", { name: "选择房间 409" })[0])
  expect(screen.getByText("已选择 1 间房间")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "重置筛选" }))
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 }))
  expect(screen.queryByText("已选择 1 间房间")).not.toBeInTheDocument()
  expect(screen.getAllByRole("checkbox", { name: "选择房间 409" })[0]).not.toBeChecked()
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

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 409" }))
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
  expect(await screen.findByRole("checkbox", { name: "选择房间 409" })).not.toBeChecked()
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

  await user.click(await screen.findByRole("checkbox", { name: "选择房间 401" }))
  await user.click(screen.getByRole("button", { name: "下一页" }))
  await user.click(await screen.findByRole("checkbox", { name: "选择房间 402" }))
  await user.click(screen.getByRole("button", { name: "目录管理" }))
  await user.click(screen.getByRole("button", { name: "批量核对 2 间房" }))
  await user.click(screen.getByRole("button", { name: "确认执行" }))

  expect(await screen.findByText("房态数据不可访问")).toBeVisible()
  expect(screen.queryByText("已选择 2 间房间")).not.toBeInTheDocument()
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

it("keeps only one shared batch toolbar and clears the selection-limit notice when selection shrinks", async () => {
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
  const selectionAtLimit = new Map<string, HousingRoomSelection>()
  for (let index = 0; index < 499; index += 1) selectionAtLimit.set(`cross-page-${index}`, housingRoomSelection({ ...selectableRooms[0], id: `cross-page-${index}`, version: index + 1 }))
  selectionAtLimit.set("room-limit-500", housingRoomSelection(selectableRooms[0]))
  updateSelectionMock.mockImplementationOnce(() => ({ next: selectionAtLimit, limited: false }))
  await user.click(within(roomList).getByRole("checkbox", { name: "选择房间 500" }))
  expect(screen.getByText("已选择 500 间房间")).toBeVisible()
  expect(within(panel).queryByRole('checkbox', { name: /选择房间/ })).not.toBeInTheDocument()
  expect(screen.getAllByRole('region', { name: '房间批量操作' })).toHaveLength(1)

  await user.click(within(roomList).getByRole("checkbox", { name: "选择房间 501" }))
  expect(await screen.findByText("跨页选择最多支持 500 间房，请先取消部分选择。")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "清空选择" }))
  await waitFor(() => expect(screen.queryByText("跨页选择最多支持 500 间房，请先取消部分选择。")).not.toBeInTheDocument())

  updateSelectionMock.mockImplementationOnce(() => ({ next: selectionAtLimit, limited: false }))
  await user.click(within(roomList).getByRole("checkbox", { name: "选择房间 500" }))
  await user.click(within(roomList).getByRole("checkbox", { name: "选择房间 501" }))
  expect(await screen.findByText("跨页选择最多支持 500 间房，请先取消部分选择。")).toBeVisible()
  await user.click(within(roomList).getByRole("checkbox", { name: "选择房间 500" }))
  await waitFor(() => expect(screen.queryByText("跨页选择最多支持 500 间房，请先取消部分选择。")).not.toBeInTheDocument())
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
  await user.click(screen.getByRole("button", { name: "按已用房间数筛选房间" }))
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
  await user.selectOptions(screen.getByLabelText("可入住状态"), "assignable")

  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({
    buildingId: "a", floorId: "a4", query: "409", roomTypes: ["single", "double"],
    verificationStatuses: ["verified"], serviceStatuses: ["problem"], currentGender: "unspecified",
    assignability: "assignable", page: 1, pageSize: 20,
  } satisfies HousingFiltersValue))
  expect(screen.queryByLabelText("入住状态")).not.toBeInTheDocument()
  expect(screen.getByLabelText("可入住状态")).toHaveValue("assignable")
})

it("normalizes reverse-order multi-select clicks before building the query key", async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("article", { name: /房间 409/ })

  for (const label of ["功能间", "单人间", "已核对", "待核对", "已停用", "正常"]) {
    await user.click(screen.getByRole("checkbox", { name: label }))
  }

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

it.each(["Escape", "关闭筛选"] as const)("retains immediate mobile search after %s and reopen", async (closeAction) => {
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
  expect(screen.getByLabelText("搜索房间")).toHaveValue("draft-only")
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ query: "draft-only", page: 1, pageSize: 20 }))
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
  await waitFor(() => expect(overviewMock).toHaveBeenLastCalledWith({ query: "409", page: 1, pageSize: 20 }))
  expect(await screen.findByRole("article", { name: /房间 409/ })).toBeVisible()

  const trigger = screen.getByRole("button", { name: "打开筛选" })
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
  expect(screen.getByLabelText("搜索房间")).toHaveValue("draft-only")

  setWidth(360)
  const reopenedTrigger = await screen.findByRole("button", { name: "打开筛选" })
  expect(screen.queryByRole("dialog", { name: "筛选房间" })).not.toBeInTheDocument()
  expect(document.body.style.overflow).toBe("")
  await user.click(reopenedTrigger)
  expect(screen.getByLabelText("搜索房间")).toHaveValue("draft-only")
})
