import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { beforeEach, expect, it, vi } from "vitest"
import type { AuthUser } from "../auth/AuthProvider"
import { ProblemDetailsError } from "../api/client"
import { getHousingCatalog } from "./api"
import type { HousingBuilding, HousingRoomDetail } from "./api"
import type * as HousingApiModule from "./api"
import { HousingPropertyChangesPanel } from "./HousingPropertyChangesPanel"
import { HousingPropertyExportsPanel } from "./HousingPropertyExportsPanel"
import { housingKeys } from "./housingKeys"
import * as api from "./propertyApi"

const auth = vi.hoisted(() => ({ user: { account_id: "a", permissions: ["housing.read", "housing.changes.submit"], scoped_permissions: [] } as unknown as AuthUser }))
vi.mock("../auth/AuthProvider", () => ({ useAuth: () => ({ user: auth.user, status: "authenticated" }) }))
vi.mock("./api", async (original) => ({ ...await original<typeof HousingApiModule>(), getHousingCatalog: vi.fn() }))
vi.mock("./propertyApi", async (original) => ({ ...await original<typeof api>(), getHousingPropertyCapabilities: vi.fn(), listHousingPropertyChanges: vi.fn(), submitHousingPropertyChange: vi.fn(), reviewHousingPropertyChange: vi.fn(), withdrawHousingPropertyChange: vi.fn(), exportHousingProperty: vi.fn(), saveHousingPropertyExport: vi.fn() }))

const request: api.HousingPropertyChange = { id: "r1", room_id: "room1", room_number: "101", building_name: "一号楼", base_version: 8, changes: { notes: "修水管" }, before: { notes: "原备注" }, reason: "漏水", status: "pending", submitted_by_name: "物业甲", submitted_at: "2026-11-05T00:00:00Z", reviewed_at: null, review_note: null, can_review: false, can_withdraw: true }
const detail = { access_revision: "rev1", room: { id: "room1", room_number: "101", version: 8, service_status: "available", room_type: "double", capacity: 2, notes: "原备注" } } as HousingRoomDetail
function building(id: string, name: string): HousingBuilding { return { id, name, code: id, display_order: 0, is_active: true, notes: null, allowed_actions: [], created_at: "", updated_at: "" } }

beforeEach(() => {
  vi.clearAllMocks()
  auth.user = { ...auth.user, account_id: "a", permissions: ["housing.read", "housing.changes.submit"], scoped_permissions: [] }
  vi.mocked(getHousingCatalog).mockResolvedValue({ access_revision: "rev1", buildings: [building("b1", "一号楼")], floors: [], can_create_building: false })
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: true, can_review: false, can_export: true, show_basic_export: true, can_create_request: true })
  vi.mocked(api.listHousingPropertyChanges).mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  vi.mocked(api.submitHousingPropertyChange).mockResolvedValue(request)
  vi.mocked(api.withdrawHousingPropertyChange).mockResolvedValue({ ...request, status: "withdrawn", can_withdraw: false })
})

function mount(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return { client, ...render(<QueryClientProvider client={client}>{node}</QueryClientProvider>) }
}

it.each([false, undefined])("hides the dedicated export entry when show_basic_export is %s despite export authority", async (show) => {
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: false, can_review: true, can_export: true, ...(show === undefined ? {} : { show_basic_export: show }) })
  const view = mount(<HousingPropertyExportsPanel />)
  await waitFor(() => expect(view.client.isFetching()).toBe(0))
  expect(screen.queryByRole("region", { name: "物业基础导出" })).toBeNull()
  expect(api.exportHousingProperty).not.toHaveBeenCalled()
})

it("separates a mixed-role owner's submitted view from review and never offers room approval", async () => {
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: true, can_review: true, can_export: true, show_basic_export: true })
  vi.mocked(api.listHousingPropertyChanges).mockResolvedValue({ items: [{ ...request, can_review: true }], total: 1, page: 1, page_size: 20 })
  mount(<HousingPropertyChangesPanel mode="submit" room={detail} />)
  expect(await screen.findByText("原备注 → 修水管")).toBeVisible()
  expect(api.listHousingPropertyChanges).toHaveBeenCalledWith(expect.objectContaining({ view: "submitted", room_id: "room1" }), expect.anything())
  expect(screen.getByRole("button", { name: "撤回申请" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "通过申请" })).toBeNull()
  expect(screen.queryByRole("button", { name: "驳回申请" })).toBeNull()
})

it("uses the review view and never shows owner withdrawal or submission in the workspace", async () => {
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: true, can_review: true, can_export: true, show_basic_export: true })
  vi.mocked(api.listHousingPropertyChanges).mockResolvedValue({ items: [request], total: 1, page: 1, page_size: 20 })
  mount(<HousingPropertyChangesPanel mode="review" />)
  expect(await screen.findByRole("region", { name: "物业变更审批" })).toBeVisible()
  expect(await screen.findByText("原备注 → 修水管")).toBeVisible()
  expect(api.listHousingPropertyChanges).toHaveBeenCalledWith(expect.objectContaining({ view: "review" }), expect.anything())
  expect(screen.queryByRole("button", { name: "撤回申请" })).toBeNull()
  expect(screen.queryByRole("button", { name: "提交待审申请" })).toBeNull()
  expect(screen.queryByRole("button", { name: "通过申请" })).toBeNull()
})

it.each(["submit", "review"] as const)("does not read private %s rows without its server capability", async (mode) => {
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: mode !== "submit", can_review: mode !== "review", can_export: false, show_basic_export: false })
  const view = mount(<HousingPropertyChangesPanel mode={mode} room={detail} />)
  await waitFor(() => expect(view.client.isFetching()).toBe(0))
  expect(api.listHousingPropertyChanges).not.toHaveBeenCalled()
  expect(screen.queryByText("物业甲")).toBeNull()
})

it.each([false, undefined])("keeps historical owner status/withdrawal without a new form when can_create_request is %s", async (create) => {
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: true, can_review: true, can_export: true, show_basic_export: false, ...(create === undefined ? {} : { can_create_request: create }) })
  vi.mocked(api.listHousingPropertyChanges).mockResolvedValue({ items: [request], total: 1, page: 1, page_size: 20 })
  mount(<HousingPropertyChangesPanel mode="submit" room={detail} />)
  expect(await screen.findByText("原备注 → 修水管")).toBeVisible()
  expect(screen.queryByRole("button", { name: "提交待审申请" })).toBeNull()
  expect(screen.queryByLabelText("申请原因")).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "撤回申请" }))
  await waitFor(() => expect(api.withdrawHousingPropertyChange).toHaveBeenCalledWith("r1"))
  expect(api.submitHousingPropertyChange).not.toHaveBeenCalled()
})

it("hides cached private permissions and errors and makes no requests when the enabled gate closes", async () => {
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: false, can_review: true, can_export: false, show_basic_export: false })
  vi.mocked(api.listHousingPropertyChanges).mockResolvedValue({ items: [request], total: 1, page: 1, page_size: 20 })
  const view = mount(<HousingPropertyChangesPanel mode="review" enabled={false} />)
  expect(getHousingCatalog).not.toHaveBeenCalled()
  expect(api.getHousingPropertyCapabilities).not.toHaveBeenCalled()
  view.rerender(<QueryClientProvider client={view.client}><HousingPropertyChangesPanel mode="review" enabled /></QueryClientProvider>)
  expect(await screen.findByText("物业甲")).toBeVisible()
  view.rerender(<QueryClientProvider client={view.client}><HousingPropertyChangesPanel mode="review" enabled={false} /></QueryClientProvider>)
  expect(screen.queryByText("物业甲")).toBeNull()
  expect(screen.queryByRole("alert")).toBeNull()
  const catalogCalls = vi.mocked(getHousingCatalog).mock.calls.length
  const capabilityCalls = vi.mocked(api.getHousingPropertyCapabilities).mock.calls.length
  await act(async () => { await view.client.invalidateQueries({ queryKey: housingKeys.root("a") }) })
  expect(getHousingCatalog).toHaveBeenCalledTimes(catalogCalls)
  expect(api.getHousingPropertyCapabilities).toHaveBeenCalledTimes(capabilityCalls)
})

it("submits a deliberate notes-only proposal without changing effective room state", async () => {
  const onChanged = vi.fn()
  mount(<HousingPropertyChangesPanel mode="submit" room={detail} onChanged={onChanged} />)
  await screen.findByRole("checkbox", { name: "变更备注 / 问题说明" })
  fireEvent.click(screen.getByRole("checkbox", { name: "变更备注 / 问题说明" }))
  fireEvent.change(screen.getByLabelText("建议备注 / 问题说明"), { target: { value: "修水管" } })
  fireEvent.change(screen.getByLabelText("申请原因"), { target: { value: "漏水" } })
  expect(screen.getByText("原备注 → 修水管")).toBeVisible()
  fireEvent.click(screen.getByRole("button", { name: "提交待审申请" }))
  await waitFor(() => expect(api.submitHousingPropertyChange).toHaveBeenCalledWith("room1", { expected_version: 8, changes: { notes: "修水管" }, reason: "漏水" }))
  expect(detail.room.notes).toBe("原备注")
  expect(onChanged).not.toHaveBeenCalled()
  await screen.findByText("申请已提交，审核通过前当前房间不变。")
})

it("shows the real before/after state and allows only the owner's withdrawal", async () => {
  vi.mocked(api.listHousingPropertyChanges).mockResolvedValue({ items: [request], total: 1, page: 1, page_size: 20 })
  mount(<HousingPropertyChangesPanel mode="submit" room={detail} />)
  expect(await screen.findByText("原备注 → 修水管")).toBeVisible()
  expect(screen.queryByRole("button", { name: "通过申请" })).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "撤回申请" }))
  await waitFor(() => expect(api.withdrawHousingPropertyChange).toHaveBeenCalledWith("r1"))
})

it("requires a rejection reason and displays the persisted rejected result without review controls", async () => {
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: false, can_review: true, can_export: false, show_basic_export: false })
  vi.mocked(api.listHousingPropertyChanges).mockResolvedValue({ items: [{ ...request, can_review: true, can_withdraw: false }], total: 1, page: 1, page_size: 20 })
  vi.mocked(api.reviewHousingPropertyChange).mockImplementation(async (_id, input) => {
    const result = { ...request, status: "rejected" as const, review_note: input.note ?? null, can_review: false, can_withdraw: false }
    vi.mocked(api.listHousingPropertyChanges).mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
    return result
  })
  const changed = vi.fn()
  mount(<HousingPropertyChangesPanel mode="review" onChanged={changed} />)
  fireEvent.click(await screen.findByRole("button", { name: "驳回申请" }))
  expect(screen.getByRole("button", { name: "确认驳回" })).toBeDisabled()
  fireEvent.change(screen.getByLabelText("审核说明"), { target: { value: "请补充现场情况" } })
  fireEvent.click(screen.getByRole("button", { name: "确认驳回" }))
  await waitFor(() => expect(api.reviewHousingPropertyChange).toHaveBeenCalledWith("r1", { decision: "reject", note: "请补充现场情况" }))
  expect(await screen.findByRole("status")).toHaveTextContent("已驳回（未生效）")
  expect(screen.queryByRole("button", { name: "通过申请" })).toBeNull()
  expect(screen.queryByRole("button", { name: "驳回申请" })).toBeNull()
  expect(changed).not.toHaveBeenCalled()
})

it("requires explicit approval confirmation before effective-room refresh", async () => {
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: false, can_review: true, can_export: false, show_basic_export: false })
  vi.mocked(api.listHousingPropertyChanges).mockResolvedValue({ items: [{ ...request, can_review: true, can_withdraw: false }], total: 1, page: 1, page_size: 20 })
  vi.mocked(api.reviewHousingPropertyChange).mockResolvedValue({ ...request, status: "approved" })
  const changed = vi.fn()
  const view = mount(<HousingPropertyChangesPanel mode="review" onChanged={changed} />)
  const detailKey = housingKeys.detail("a", "rev1", "room1")
  const overviewKey = housingKeys.overview("a", "rev1", { page: 1, pageSize: 20 })
  view.client.setQueryData(detailKey, detail)
  view.client.setQueryData(overviewKey, { rooms: [detail.room] })
  fireEvent.click(await screen.findByRole("button", { name: "通过申请" }))
  expect(api.reviewHousingPropertyChange).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "确认通过并生效" }))
  await waitFor(() => expect(changed).toHaveBeenCalledOnce())
  await waitFor(() => expect(view.client.getQueryState(detailKey)?.isInvalidated).toBe(true))
  expect(view.client.getQueryState(overviewKey)?.isInvalidated).toBe(true)
  await waitFor(() => expect(vi.mocked(api.listHousingPropertyChanges).mock.calls.length).toBeGreaterThan(1))
})

it("preserves an open review draft through same-scope capability checking while hiding private data", async () => {
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: false, can_review: true, can_export: false, show_basic_export: false })
  vi.mocked(api.listHousingPropertyChanges).mockResolvedValue({ items: [{ ...request, can_review: true, can_withdraw: false }], total: 1, page: 1, page_size: 20 })
  const view = mount(<HousingPropertyChangesPanel mode="review" />)
  fireEvent.click(await screen.findByRole("button", { name: "驳回申请" }))
  fireEvent.change(screen.getByLabelText("审核说明"), { target: { value: "保留核验说明" } })
  let finish!: (value: api.HousingPropertyCapabilities) => void
  vi.mocked(api.getHousingPropertyCapabilities).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const key = view.client.getQueryCache().findAll().find((query) => query.queryKey.includes("capabilities"))!.queryKey
  let refresh!: Promise<void>
  act(() => { refresh = view.client.refetchQueries({ queryKey: key, exact: true }) })
  await waitFor(() => expect(screen.queryByRole("region", { name: "物业变更审批" })).toBeNull())
  expect(screen.getByLabelText("审核说明")).not.toBeVisible()
  expect(screen.getByLabelText("审核说明")).toBeDisabled()
  await act(async () => { finish({ can_submit: false, can_review: true, can_export: false, show_basic_export: false }); await refresh })
  await waitFor(() => expect(screen.getByLabelText("审核说明")).toBeVisible())
  expect(screen.getByLabelText("审核说明")).toHaveValue("保留核验说明")
  expect(api.reviewHousingPropertyChange).not.toHaveBeenCalled()
})

it("retains proposal fields and reason through a same-scope capability recheck while hiding private projection", async () => {
  const view = mount(<HousingPropertyChangesPanel mode="submit" room={detail} />)
  fireEvent.click(await screen.findByRole("checkbox", { name: "变更备注 / 问题说明" }))
  fireEvent.change(screen.getByLabelText("建议备注 / 问题说明"), { target: { value: "等待检查" } })
  fireEvent.change(screen.getByLabelText("申请原因"), { target: { value: "保留草稿" } })
  let finish!: (value: api.HousingPropertyCapabilities) => void
  vi.mocked(api.getHousingPropertyCapabilities).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const key = view.client.getQueryCache().findAll().find((query) => query.queryKey.includes("capabilities"))!.queryKey
  let refresh!: Promise<void>
  act(() => { refresh = view.client.refetchQueries({ queryKey: key, exact: true }) })
  await waitFor(() => expect(screen.queryByRole("button", { name: "提交待审申请" })).toBeNull())
  expect(screen.getByLabelText("申请原因")).toBeDisabled()
  expect(screen.getByLabelText("申请原因")).not.toBeVisible()
  await act(async () => { finish({ can_submit: true, can_review: false, can_export: true, show_basic_export: true, can_create_request: true }); await refresh })
  expect(await screen.findByLabelText("申请原因")).toHaveValue("保留草稿")
  expect(screen.getByLabelText("建议备注 / 问题说明")).toHaveValue("等待检查")
  expect(screen.getByRole("checkbox", { name: "变更备注 / 问题说明" })).toBeChecked()
})

it("retains export selection and defers an in-flight download until same-scope reauthorization", async () => {
  let finishExport!: (value: api.HousingPropertyExportFile) => void
  vi.mocked(api.exportHousingProperty).mockImplementation(() => new Promise((resolve) => { finishExport = resolve }))
  const view = mount(<HousingPropertyExportsPanel />)
  fireEvent.change(await screen.findByLabelText("导出用途"), { target: { value: "维修盘点" } })
  fireEvent.change(screen.getByLabelText("清单类型"), { target: { value: "problems" } })
  fireEvent.change(screen.getByLabelText("文件格式"), { target: { value: "csv" } })
  fireEvent.click(screen.getByRole("checkbox", { name: "一号楼" }))
  fireEvent.click(screen.getByRole("button", { name: "导出文件" }))
  let finishCapability!: (value: api.HousingPropertyCapabilities) => void
  vi.mocked(api.getHousingPropertyCapabilities).mockImplementationOnce(() => new Promise((resolve) => { finishCapability = resolve }))
  const key = view.client.getQueryCache().findAll().find((query) => query.queryKey.includes("capabilities"))!.queryKey
  let refresh!: Promise<void>
  act(() => { refresh = view.client.refetchQueries({ queryKey: key, exact: true }) })
  await waitFor(() => expect(screen.queryByRole("button", { name: "导出文件" })).toBeNull())
  expect(screen.getByLabelText("导出用途")).toBeDisabled()
  expect(screen.getByLabelText("导出用途")).not.toBeVisible()
  const file = { blob: new Blob(["safe"]), filename: "housing-problems.csv" }
  await act(async () => finishExport(file))
  expect(api.saveHousingPropertyExport).not.toHaveBeenCalled()
  await act(async () => { finishCapability({ can_submit: true, can_review: false, can_export: true, show_basic_export: true }); await refresh })
  await waitFor(() => expect(api.saveHousingPropertyExport).toHaveBeenCalledWith(file))
  expect(screen.getByLabelText("导出用途")).toHaveValue("维修盘点")
  expect(screen.getByLabelText("清单类型")).toHaveValue("problems")
  expect(screen.getByLabelText("文件格式")).toHaveValue("csv")
  expect(screen.getByRole("checkbox", { name: "一号楼" })).toBeChecked()
  expect(api.exportHousingProperty).toHaveBeenCalledOnce()
})

it("discards preserved export draft and pending bytes on authorization failure", async () => {
  let finishExport!: (value: api.HousingPropertyExportFile) => void
  vi.mocked(api.exportHousingProperty).mockImplementation(() => new Promise((resolve) => { finishExport = resolve }))
  const view = mount(<HousingPropertyExportsPanel />)
  fireEvent.change(await screen.findByLabelText("导出用途"), { target: { value: "私有用途" } })
  fireEvent.click(screen.getByRole("button", { name: "导出文件" }))
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: false, can_review: false, can_export: true, show_basic_export: false })
  const key = view.client.getQueryCache().findAll().find((query) => query.queryKey.includes("capabilities"))!.queryKey
  await act(async () => { await view.client.refetchQueries({ queryKey: key, exact: true }) })
  await waitFor(() => expect(screen.queryByRole("button", { name: "导出文件" })).toBeNull())
  await act(async () => finishExport({ blob: new Blob(["private"]), filename: "housing-occupants.xlsx" }))
  expect(api.saveHousingPropertyExport).not.toHaveBeenCalled()
  expect(screen.queryByLabelText("导出用途")).toBeNull()
})

it("refreshes room/list on version conflict without silently overwriting", async () => {
  vi.mocked(api.submitHousingPropertyChange).mockRejectedValue(new ProblemDetailsError({ type: "about:blank", title: "conflict", status: 409, detail: "版本已变化", code: "housing_stale_version", request_id: "test" }))
  const changed = vi.fn()
  mount(<HousingPropertyChangesPanel mode="submit" room={detail} onChanged={changed} />)
  fireEvent.click(await screen.findByRole("checkbox", { name: "变更备注 / 问题说明" }))
  fireEvent.change(screen.getByLabelText("建议备注 / 问题说明"), { target: { value: "修水管" } })
  fireEvent.change(screen.getByLabelText("申请原因"), { target: { value: "漏水" } })
  fireEvent.click(screen.getByRole("button", { name: "提交待审申请" }))
  await screen.findByRole("alert")
  await waitFor(() => expect(changed).toHaveBeenCalledOnce())
  expect(api.submitHousingPropertyChange).toHaveBeenCalledTimes(1)
})

it.each(["approved", "rejected", "withdrawn"] as const)("filters and displays actual %s result", async (status) => {
  vi.mocked(api.listHousingPropertyChanges).mockImplementation(async (input) => ({ items: [{ ...request, status: input.status, can_withdraw: false, review_note: "实际结果" }], total: 1, page: 1, page_size: 20 }))
  mount(<HousingPropertyChangesPanel mode="submit" room={detail} />)
  fireEvent.change(await screen.findByLabelText("申请状态"), { target: { value: status } })
  await waitFor(() => expect(api.listHousingPropertyChanges).toHaveBeenLastCalledWith(expect.objectContaining({ status }), expect.anything()))
  expect(await screen.findByText("实际结果")).toBeVisible()
  expect(screen.queryByRole("button", { name: "撤回申请" })).toBeNull()
})

it("requires purpose and exports chosen kind/format/buildings through the basic endpoint", async () => {
  const blob = new Blob(["basic"])
  vi.mocked(api.exportHousingProperty).mockResolvedValue({ blob, filename: "housing-problems.csv" })
  mount(<HousingPropertyExportsPanel />)
  const section = await screen.findByRole("region", { name: "物业基础导出" })
  expect(within(section).getByRole("button", { name: "导出文件" })).toBeDisabled()
  fireEvent.change(screen.getByLabelText("清单类型"), { target: { value: "problems" } })
  fireEvent.change(screen.getByLabelText("文件格式"), { target: { value: "csv" } })
  fireEvent.click(screen.getByRole("checkbox", { name: "一号楼" }))
  fireEvent.change(screen.getByLabelText("导出用途"), { target: { value: "维修盘点" } })
  fireEvent.click(screen.getByRole("button", { name: "导出文件" }))
  await waitFor(() => expect(api.exportHousingProperty).toHaveBeenCalledWith({ kind: "problems", format: "csv", purpose: "维修盘点", building_ids: ["b1"] }))
  await waitFor(() => expect(api.saveHousingPropertyExport).toHaveBeenCalledWith({ blob, filename: "housing-problems.csv" }))
})

it("clears private list and drafts immediately when permissions change", async () => {
  vi.mocked(api.listHousingPropertyChanges).mockResolvedValue({ items: [request], total: 1, page: 1, page_size: 20 })
  const view = mount(<HousingPropertyChangesPanel mode="submit" room={detail} />)
  await screen.findByText("物业甲")
  auth.user = { ...auth.user, permissions: [] }
  vi.mocked(api.getHousingPropertyCapabilities).mockResolvedValue({ can_submit: false, can_review: false, can_export: false, show_basic_export: false })
  view.rerender(<QueryClientProvider client={view.client}><HousingPropertyChangesPanel mode="submit" room={detail} /></QueryClientProvider>)
  expect(screen.queryByText("物业甲")).toBeNull()
  await waitFor(() => expect(screen.queryByRole("button", { name: "提交待审申请" })).toBeNull())
})

it("refreshes the authorized building catalog on a same-account permission change", async () => {
  const view = mount(<HousingPropertyExportsPanel />)
  await screen.findByRole("checkbox", { name: "一号楼" })
  vi.mocked(getHousingCatalog).mockResolvedValue({ access_revision: "rev2", buildings: [building("b2", "二号楼")], floors: [], can_create_building: false })
  auth.user = { ...auth.user, scoped_permissions: ["housing.exports.basic"] }
  view.rerender(<QueryClientProvider client={view.client}><HousingPropertyExportsPanel /></QueryClientProvider>)
  await screen.findByRole("checkbox", { name: "二号楼" })
  expect(screen.queryByRole("checkbox", { name: "一号楼" })).toBeNull()
})

it("clears export purpose and building selection after a real catalog revision change", async () => {
  const view = mount(<HousingPropertyExportsPanel />)
  fireEvent.change(await screen.findByLabelText("导出用途"), { target: { value: "旧权限草稿" } })
  fireEvent.click(screen.getByRole("checkbox", { name: "一号楼" }))
  const catalog = { access_revision: "rev2", buildings: [building("b1", "一号楼")], floors: [], can_create_building: false }
  vi.mocked(getHousingCatalog).mockResolvedValue(catalog)
  act(() => { view.client.setQueryData(housingKeys.catalog("a"), catalog) })
  await waitFor(() => expect(screen.getByLabelText("导出用途")).toHaveValue(""))
  expect(screen.getByRole("checkbox", { name: "一号楼" })).not.toBeChecked()
})

it("discards late export response after account changes", async () => {
  let finish!: (value: { blob: Blob; filename: string }) => void
  vi.mocked(api.exportHousingProperty).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const view = mount(<HousingPropertyExportsPanel />)
  fireEvent.change(await screen.findByLabelText("导出用途"), { target: { value: "盘点" } })
  fireEvent.click(screen.getByRole("button", { name: "导出文件" }))
  await waitFor(() => expect(api.exportHousingProperty).toHaveBeenCalledOnce())
  auth.user = { ...auth.user, account_id: "b" }
  view.rerender(<QueryClientProvider client={view.client}><HousingPropertyExportsPanel /></QueryClientProvider>)
  await act(async () => finish({ blob: new Blob(["private"]), filename: "housing-occupants.xlsx" }))
  expect(api.saveHousingPropertyExport).not.toHaveBeenCalled()
})
