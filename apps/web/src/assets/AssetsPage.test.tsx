import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import * as api from "./api"
import { AssetsPage } from "./AssetsPage"
import { AssetWarrantyDetails } from "./AssetWarrantyDetails"

const auth = vi.hoisted(() => ({ accountId: "asset-test-account", permissions: ["assets.read", "assets.manage"] }))
vi.mock("../auth/AuthProvider", () => ({
  useOptionalAuth: () => ({ status: "authenticated", user: { account_id: auth.accountId, is_super_admin: false, permissions: auth.permissions } }),
}))

const catalog: api.AssetCategoryCatalog = {
  groups: [{ key: "computers", label: "电脑与电竞设备", options: ["台式电脑", "笔记本电脑", "显示器"] }],
  legacy_categories: ["旧训练设备"],
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); auth.accountId = "asset-test-account"; auth.permissions = ["assets.read", "assets.manage"] })

const rental = (id = "asset-1", expires = "2026-01-31"): api.Asset => ({
  id, asset_code: id.toUpperCase(), name: `租用电脑 ${id}`, category: null, brand: null, model: null, serial_number: null,
  status: "available", location: "训练室", purchase_date: null, warranty_expires_on: null,
  source_purchase_id: null, note: null, created_at: "", updated_at: "", photo: null, current_assignment: null,
  ownership_type: "rented", rental_started_on: "2020-01-01", rental_expires_on: expires, rental_returned_on: null, rental_revision: 2,
})

function renderAssets(items: api.Asset[] = [], canManage = true) {
  vi.spyOn(api, "listAssets").mockResolvedValue({ items, page: 1, page_size: 50, total: items.length })
  if (!vi.isMockFunction(api.listAssetCategories)) vi.spyOn(api, "listAssetCategories").mockResolvedValue(catalog)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return { ...render(<QueryClientProvider client={client}><AssetsPage canManage={canManage} /></QueryClientProvider>), client }
}

// Catches status cards that do not query, reset other filters, or retain a later page.
it.each([
  ["资产总数", "", "全部状态结果"],
  ["当前可用", "available", "可用结果"],
  ["使用中", "assigned", "使用中结果"],
] as const)("%s applies visible filters and returns to page one", async (buttonName, nextStatus, resultName) => {
  renderAssets([rental("initial")])
  await screen.findByText("租用电脑 initial")
  const list = vi.mocked(api.listAssets)
  list.mockImplementation(async (filters) => ({
    items: [{ ...rental("filtered"), name: ({ available: "可用结果", assigned: "使用中结果", maintenance: "维修结果", retired: "停用结果" } as const)[filters?.status as api.AssetStatus] ?? "全部状态结果", status: filters?.status || "available" }],
    total: 3, page: filters?.page ?? 1, page_size: 1,
  }))
  const user = userEvent.setup()
  await user.type(screen.getByLabelText("搜索资产"), "原查询")
  await user.selectOptions(screen.getByLabelText("分类"), "显示器")
  await user.selectOptions(screen.getByLabelText("归属筛选"), "rented")
  await user.selectOptions(screen.getByLabelText("状态"), "maintenance")
  await user.click(screen.getByRole("button", { name: "查询" }))
  await screen.findByText("维修结果")
  await user.click(screen.getByRole("button", { name: "下一页资产" }))
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: "原查询", category: "显示器", ownership_type: "rented", status: "maintenance", page: 2 }))
  await user.clear(screen.getByLabelText("搜索资产"))
  await user.type(screen.getByLabelText("搜索资产"), " 新查询 ")
  await user.click(screen.getByRole("button", { name: buttonName }))
  expect(await screen.findByText(resultName)).toBeVisible()
  expect(list).toHaveBeenLastCalledWith({ query: "新查询", category: "显示器", ownership_type: "rented", status: nextStatus, page: 1 })
  expect(screen.getByLabelText("分类")).toHaveValue("显示器")
  expect(screen.getByLabelText("归属筛选")).toHaveValue("rented")
  expect(screen.getByLabelText("状态")).toHaveValue(nextStatus)
  expect(screen.getByRole("button", { name: buttonName })).toHaveAttribute("aria-pressed", "true")
  expect(within(screen.getByRole("region", { name: "资产数量统计" })).getAllByRole("button", { pressed: true })).toHaveLength(1)
  await user.click(screen.getByRole("button", { name: "清空" }))
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: "", category: "", ownership_type: "", status: "", page: 1 }))
  expect(screen.getByRole("button", { name: "资产总数" })).toHaveAttribute("aria-pressed", "true")
})

// Catches zero-count cards being disabled and accidentally requiring management authority.
it("lets read-only viewers activate a zero-count status card with the keyboard", async () => {
  auth.permissions = ["assets.read"]
  const list = vi.spyOn(api, "listAssets").mockImplementation(async (filters) => ({
    items: filters?.status === "available" ? [{ ...rental("available"), name: "可用设备" }] : [{ ...rental("maintenance"), status: "maintenance" as const }],
    total: 1, page: 1, page_size: 50,
  }))
  vi.spyOn(api, "listAssetCategories").mockResolvedValue(catalog)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><AssetsPage canManage={false} canAssign={false} canExport={false} /></QueryClientProvider>)
  await screen.findByText("租用电脑 maintenance")
  const available = screen.getByRole("button", { name: "当前可用" })
  expect(within(available).getByText("0")).toBeVisible()
  expect(within(available).getByText("当前页数量")).toBeVisible()
  expect(available).toHaveAccessibleDescription("0 当前页数量")
  expect(screen.getByRole("button", { name: "资产总数" })).toHaveAccessibleDescription("1 当前筛选总数")
  expect(screen.getByRole("button", { name: "使用中" })).toHaveAccessibleDescription("0 当前页数量")
  expect(available).toBeEnabled()
  available.focus()
  await userEvent.keyboard("{Enter}")
  expect(await screen.findByText("可用设备")).toBeVisible()
  expect(list).toHaveBeenLastCalledWith({ query: "", category: "", ownership_type: "", status: "available", page: 1 })
  expect(screen.queryByRole("button", { name: "新增资产" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "分配" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "编辑" })).not.toBeInTheDocument()
})

// Catches absent batch entry, unsafe retry rotation, and shared serial/photo leakage.
it("freezes an uncertain rental batch and retries the same operation before refreshing once successful", async () => {
  renderAssets()
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "批量新增租用资产" }))
  const dialog = screen.getByRole("dialog", { name: "批量新增租用资产" })
  expect(within(dialog).queryByLabelText("序列号")).not.toBeInTheDocument()
  expect(within(dialog).queryByLabelText("辨识照片（可选）")).not.toBeInTheDocument()
  const batch = vi.spyOn(api, "createAssetBatch").mockRejectedValueOnce(new Error("timeout")).mockResolvedValue({ operation_key: "done", items: [rental("one"), rental("two")], total: 2 })
  await user.type(within(dialog).getByLabelText("资产名称"), "租用显示器")
  fireEvent.change(within(dialog).getByLabelText("批量数量"), { target: { value: "2" } })
  fireEvent.change(within(dialog).getByLabelText("租用开始日期"), { target: { value: "2026-01-01" } })
  fireEvent.change(within(dialog).getByLabelText("租用到期日期"), { target: { value: "2026-12-31" } })
  await user.click(within(dialog).getByRole("button", { name: "创建租用资产" }))
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("结果尚未确认")
  expect(within(dialog).getByLabelText("资产名称")).toBeDisabled()
  expect(within(dialog).getByLabelText("批量数量")).toBeDisabled()
  await user.type(within(dialog).getByLabelText("资产名称"), "不得改变")
  expect(within(dialog).getByLabelText("资产名称")).toHaveValue("租用显示器")
  await user.click(within(dialog).getByRole("button", { name: "关闭" }))
  expect(within(dialog).getByText(/关闭后请先核对台账/)).toBeVisible()
  await user.click(within(dialog).getByRole("button", { name: "保留并继续核对" }))
  const first = batch.mock.calls[0][0]
  expect(first).toEqual({ operation_key: expect.stringMatching(/^[\da-f-]{36}$/), quantity: 2, asset: expect.objectContaining({ name: "租用显示器", ownership_type: "rented", rental_started_on: "2026-01-01", rental_expires_on: "2026-12-31" }) })
  expect(first.asset).not.toHaveProperty("serial_number")
  await user.click(within(dialog).getByRole("button", { name: "重试同一批次" }))
  expect(await within(dialog).findByRole("status")).toHaveTextContent("已创建 2 台")
  expect(batch.mock.calls[1][0]).toEqual(first)
  expect(within(dialog).queryByRole("button", { name: "创建租用资产" })).not.toBeInTheDocument()
  fireEvent.submit(within(dialog).getByLabelText("批量数量").closest("form")!)
  expect(batch).toHaveBeenCalledTimes(2)
  await waitFor(() => expect(api.listAssets).toHaveBeenCalledTimes(2))
})

it("does not expose rental batch creation to read-only asset viewers", async () => {
  renderAssets([], false)
  await screen.findByText(/暂无资产记录/)
  expect(screen.queryByRole("button", { name: "批量新增租用资产" })).not.toBeInTheDocument()
})

it("creates the maximum computer rental batch with shared trimmed fields and no serial or photo", async () => {
  renderAssets()
  const items = Array.from({ length: 100 }, (_, index) => rental(`batch-${index}`))
  const create = vi.spyOn(api, "createAssetBatch").mockResolvedValue({ operation_key: "done", items, total: 100 })
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "批量新增租用资产" }))
  const dialog = screen.getByRole("dialog")
  await waitFor(() => expect(within(screen.getByLabelText("资产分类")).getByRole("option", { name: "台式电脑" })).toBeVisible())
  await user.selectOptions(screen.getByLabelText("资产分类"), "台式电脑")
  fireEvent.change(screen.getByLabelText("批量数量"), { target: { value: "100" } })
  for (const [label, value] of [["资产名称", " 训练电脑 "], ["品牌", " 联想 "], ["型号", " T1 "], ["存放位置", " 训练室 "], ["备注", " 共用配置 "], ["CPU 型号", " R7 "], ["内存容量", "32GB"], ["GPU 型号", "核显"], ["硬盘容量", "1TB SSD + 2TB HDD"]]) await user.type(screen.getByLabelText(label), value)
  fireEvent.change(screen.getByLabelText("租用开始日期"), { target: { value: "2026-01-01" } })
  fireEvent.change(screen.getByLabelText("租用到期日期"), { target: { value: "2026-12-31" } })
  await user.click(within(dialog).getByRole("button", { name: "创建租用资产" }))
  expect(await within(dialog).findByRole("status")).toHaveTextContent("已创建 100 台")
  expect(create.mock.calls[0][0]).toEqual({ operation_key: expect.any(String), quantity: 100, asset: { name: "训练电脑", category: "台式电脑", brand: "联想", model: "T1", location: "训练室", note: "共用配置", ownership_type: "rented", rental_started_on: "2026-01-01", rental_expires_on: "2026-12-31", cpu_model: "R7", memory_capacity: "32GB", gpu_model: "核显", disk_capacity: "1TB SSD + 2TB HDD" } })
})

// Catches missing computer validation and accidental submission of legacy empty hardware.
it.each(["owned", "rented"] as const)("requires full hardware for a new %s computer", async (ownership) => {
  renderAssets()
  const create = vi.spyOn(api, "createAsset").mockResolvedValue(rental())
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "新增资产" }))
  await user.type(screen.getByLabelText("资产名称"), "训练电脑")
  await waitFor(() => expect(within(screen.getByLabelText("资产分类")).getByRole("option", { name: "台式电脑" })).toBeVisible())
  await user.selectOptions(screen.getByLabelText("资产分类"), "台式电脑")
  await user.selectOptions(screen.getByLabelText("资产归属"), ownership)
  if (ownership === "owned") {
    await user.selectOptions(screen.getByLabelText("取得方式"), "purchase")
    await user.selectOptions(screen.getByLabelText("采购渠道"), "offline")
  } else {
    fireEvent.change(screen.getByLabelText("租赁开始日期"), { target: { value: "2026-01-01" } })
    fireEvent.change(screen.getByLabelText("租赁到期日期（含当天）"), { target: { value: "2026-12-31" } })
  }
  expect(screen.getByLabelText("CPU 型号")).toBeRequired()
  fireEvent.submit(screen.getByLabelText("资产名称").closest("form")!)
  expect(create).not.toHaveBeenCalled()
  for (const [label, value] of [["CPU 型号", " R7 "], ["内存容量", "32GB"], ["GPU 型号", "核显"], ["硬盘容量", "1TB SSD + 2TB HDD"]]) await user.type(screen.getByLabelText(label), value)
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ cpu_model: "R7", memory_capacity: "32GB", gpu_model: "核显", disk_capacity: "1TB SSD + 2TB HDD" })))
})

it("lets a legacy computer edit location without resubmitting missing hardware", async () => {
  const legacy = { ...rental(), category: "台式电脑" }
  renderAssets([legacy])
  const update = vi.spyOn(api, "updateAsset").mockResolvedValue(legacy)
  const user = userEvent.setup()
  expect(await screen.findByText("CPU 型号：待补充")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "编辑" }))
  expect(screen.getByLabelText("CPU 型号")).not.toBeRequired()
  expect(within(screen.getByRole("dialog")).getByText("电脑配置（历史缺项待补充）")).toBeVisible()
  expect(within(screen.getByRole("dialog")).getByText("可先修改其他信息；如修改配置，需补齐以下四项。")).toBeVisible()
  await user.clear(screen.getByLabelText("存放位置"))
  await user.type(screen.getByLabelText("存放位置"), "新训练室")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(update).toHaveBeenCalled())
  expect(update.mock.calls[0][1]).not.toHaveProperty("cpu_model")
})

// Catches historical exemption incorrectly surviving a switch between computer categories.
it.each([
  ["台式电脑", "笔记本电脑"],
  ["笔记本电脑", "台式电脑"],
])("requires complete hardware when changing %s to %s", async (from, to) => {
  const original = { ...rental(), category: from }
  renderAssets([original])
  const update = vi.spyOn(api, "updateAsset").mockImplementation(async (_id, input) => ({ ...original, ...input }))
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  const dialog = screen.getByRole("dialog")
  const category = within(dialog).getByLabelText("资产分类")
  await waitFor(() => expect(within(category).getByRole("option", { name: to })).toBeVisible())
  await user.selectOptions(category, to)
  expect(within(dialog).getByLabelText("CPU 型号")).toBeRequired()
  expect(within(dialog).getByLabelText("内存容量")).toBeRequired()
  fireEvent.submit(within(dialog).getByLabelText("资产名称").closest("form")!)
  expect(update).not.toHaveBeenCalled()
  expect(within(dialog).getByRole("alert")).toHaveTextContent("请补齐电脑")
  for (const [label, value] of [["CPU 型号", " R7 "], ["内存容量", "32GB"], ["GPU 型号", "核显"], ["硬盘容量", "1TB SSD"]]) await user.type(within(dialog).getByLabelText(label), value)
  await user.click(within(dialog).getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(update).toHaveBeenCalledWith(original.id, expect.objectContaining({ category: to, cpu_model: "R7", memory_capacity: "32GB", gpu_model: "核显", disk_capacity: "1TB SSD" })))
})

// Catches incomplete-history guidance being shown for a fully configured existing computer.
it("edits a complete unchanged computer without claiming historical hardware is missing", async () => {
  const original = { ...rental(), category: "台式电脑", cpu_model: "R7", memory_capacity: "32GB", gpu_model: "核显", disk_capacity: "1TB SSD" }
  renderAssets([original])
  const update = vi.spyOn(api, "updateAsset").mockResolvedValue(original)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  const dialog = screen.getByRole("dialog")
  expect(within(dialog).queryAllByText(/待补充|补齐以下四项/)).toHaveLength(0)
  expect(within(dialog).getByText("电脑配置")).toBeVisible()
  expect(within(dialog).getByLabelText("CPU 型号")).toHaveValue("R7")
  expect(within(dialog).getByLabelText("CPU 型号")).not.toBeRequired()
  await user.type(within(dialog).getByLabelText("存放位置"), " A区")
  await user.click(within(dialog).getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(update).toHaveBeenCalled())
  expect(update.mock.calls[0][1]).not.toHaveProperty("cpu_model")
})

it.each(["legacy", "complete", "transition"])("does not save an incomplete hardware mutation (%s)", async (mode) => {
  const original = { ...rental(), category: mode === "transition" ? "显示器" : "台式电脑", ...(mode === "complete" ? { cpu_model: "R7", memory_capacity: "32GB", gpu_model: "核显", disk_capacity: "1TB SSD" } : {}) }
  renderAssets([original])
  const update = vi.spyOn(api, "updateAsset").mockResolvedValue(original)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  if (mode === "transition") await user.selectOptions(screen.getByLabelText("资产分类"), "台式电脑")
  if (mode === "complete") await user.clear(screen.getByLabelText("CPU 型号"))
  else await user.type(screen.getByLabelText("CPU 型号"), "R9")
  expect(screen.getByLabelText("内存容量")).toBeRequired()
  fireEvent.submit(screen.getByLabelText("资产名称").closest("form")!)
  expect(update).not.toHaveBeenCalled()
  expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("请补齐电脑")
})

it.each(["0", "101", "1.5"])("rejects batch quantity %s even if browser validation is bypassed", async (quantity) => {
  renderAssets()
  const create = vi.spyOn(api, "createAssetBatch")
  await userEvent.click(screen.getByRole("button", { name: "批量新增租用资产" }))
  fireEvent.change(screen.getByLabelText("批量数量"), { target: { value: quantity } })
  fireEvent.submit(screen.getByLabelText("批量数量").closest("form")!)
  expect(create).not.toHaveBeenCalled()
  expect(screen.getByRole("alert")).toHaveTextContent("1 至 100 的整数")
})

it("submits a selected server category when creating an asset", async () => {
  renderAssets()
  const save = vi.spyOn(api, "createAsset").mockResolvedValue({ id: "created" } as api.Asset)
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "新增资产" }))
  const category = screen.getByLabelText("资产分类")
  expect(category.tagName).toBe("SELECT")
  await waitFor(() => expect(within(category).getByRole("option", { name: "显示器" })).toBeVisible())
  await user.selectOptions(category, "显示器")
  await user.type(screen.getByLabelText("资产名称"), "训练显示器")
  await user.selectOptions(screen.getByLabelText("资产归属"), "owned")
  await user.selectOptions(screen.getByLabelText("取得方式"), "purchase")
  await user.selectOptions(screen.getByLabelText("采购渠道"), "offline")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ category: "显示器" })))
})

it.each([false, true])("retries the photo against the saved asset without losing changed fields or creating a duplicate (change=%s)", async (change) => {
  const saved = { ...rental("created-partial"), name: "新增设备", ownership_type: "owned" as const, acquisition_method: "purchase" as const, acquisition_channel: "offline" as const, rental_started_on: null, rental_expires_on: null }
  renderAssets()
  const create = vi.spyOn(api, "createAsset").mockResolvedValue(saved)
  const update = vi.spyOn(api, "updateAsset").mockImplementation(async (_id, input) => ({ ...saved, ...input }))
  const upload = vi.spyOn(api, "uploadAssetPhoto").mockRejectedValueOnce(new Error("upload unavailable")).mockResolvedValue(saved)
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "新增资产" }))
  await user.type(screen.getByLabelText("资产名称"), "新增设备")
  await user.selectOptions(screen.getByLabelText("资产归属"), "owned")
  await user.selectOptions(screen.getByLabelText("取得方式"), "purchase")
  await user.selectOptions(screen.getByLabelText("采购渠道"), "offline")
  const photo = new File(["photo"], "equipment.png", { type: "image/png" })
  await user.upload(screen.getByLabelText("辨识照片（可选）"), photo)
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  const dialog = await screen.findByRole("dialog", { name: "编辑资产" })
  expect(within(dialog).getByRole("alert")).toHaveTextContent(/资产记录已保存.*照片未保存/)
  expect(screen.getByLabelText("辨识照片（可选）")).toHaveProperty("files", expect.objectContaining({ 0: photo }))
  if (change) {
    await user.clear(screen.getByLabelText("资产名称"))
    await user.type(screen.getByLabelText("资产名称"), "修改后的设备")
    await user.selectOptions(screen.getByLabelText("资产分类"), "显示器")
  }
  await user.click(within(dialog).getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(create).toHaveBeenCalledTimes(1)
  expect(upload).toHaveBeenCalledTimes(2)
  expect(upload).toHaveBeenNthCalledWith(2, saved.id, photo)
  expect(update).toHaveBeenCalledWith(saved.id, expect.objectContaining({ name: change ? "修改后的设备" : "新增设备", ...(change ? { category: "显示器" } : {}) }))
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

it("keeps a partially saved record visible after cancel, reopens it for editing, and clears old errors", async () => {
  const saved = { ...rental("created-reopen"), name: "已保存设备", ownership_type: "owned" as const, acquisition_method: "purchase" as const, acquisition_channel: "offline" as const, rental_started_on: null, rental_expires_on: null }
  renderAssets()
  let created = false
  vi.mocked(api.listAssets).mockImplementation(async () => ({ items: created ? [saved] : [], page: 1, page_size: 50, total: created ? 1 : 0 }))
  const create = vi.spyOn(api, "createAsset").mockImplementation(async () => { created = true; return saved })
  vi.spyOn(api, "uploadAssetPhoto").mockRejectedValue(new Error("photo unavailable"))
  const update = vi.spyOn(api, "updateAsset").mockResolvedValue(saved)
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "新增资产" }))
  await user.type(screen.getByLabelText("资产名称"), saved.name)
  await user.selectOptions(screen.getByLabelText("资产归属"), "owned")
  await user.selectOptions(screen.getByLabelText("取得方式"), "purchase")
  await user.selectOptions(screen.getByLabelText("采购渠道"), "offline")
  await user.upload(screen.getByLabelText("辨识照片（可选）"), new File(["photo"], "equipment.png", { type: "image/png" }))
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await screen.findByRole("dialog", { name: "编辑资产" })
  await user.click(screen.getByRole("button", { name: "取消" }))
  expect(await screen.findByText(saved.name)).toBeVisible()
  await user.click(screen.getByRole("button", { name: "编辑" }))
  expect(screen.getByLabelText("资产名称")).toHaveValue(saved.name)
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(create).toHaveBeenCalledTimes(1)
  expect(update).toHaveBeenCalledWith(saved.id, expect.any(Object))
  await user.click(screen.getByRole("button", { name: "新增资产" }))
  expect(screen.getByLabelText("资产名称")).toHaveValue("")
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

it.each(["upload", "remove"])("recovers an existing asset after a failed photo %s without repeating ownership changes", async (operation) => {
  const original = { ...rental(), photo: { id: "photo-original", mime_type: "image/png" as const, extension: "png" as const, size_bytes: 5, updated_at: "2026-10-26T10:00:00Z", url: "/photo" } }
  const saved = { ...original, ownership_type: "owned" as const, acquisition_method: "purchase" as const, acquisition_channel: "offline" as const, rental_started_on: null, rental_expires_on: null, rental_revision: 3 }
  renderAssets([original])
  const create = vi.spyOn(api, "createAsset")
  const update = vi.spyOn(api, "updateAsset").mockResolvedValue(saved)
  const photoAction = operation === "upload" ? vi.spyOn(api, "uploadAssetPhoto") : vi.spyOn(api, "deleteAssetPhoto")
  photoAction.mockRejectedValueOnce(new Error("photo unavailable")).mockResolvedValue({ ...saved, photo: null })
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  await user.selectOptions(screen.getByLabelText("资产归属"), "owned")
  await user.selectOptions(screen.getByLabelText("取得方式"), "purchase")
  await user.selectOptions(screen.getByLabelText("采购渠道"), "offline")
  if (operation === "upload") await user.upload(screen.getByLabelText("辨识照片（可选）"), new File(["new photo"], "replacement.png", { type: "image/png" }))
  else await user.click(screen.getByLabelText("移除当前照片"))
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("资产记录已保存"))
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(update).toHaveBeenNthCalledWith(1, original.id, expect.objectContaining({ ownership_type: "owned", expected_rental_revision: 2 }))
  expect(update.mock.calls[1][1]).not.toHaveProperty("ownership_type")
  expect(update.mock.calls[1][1]).not.toHaveProperty("expected_rental_revision")
  expect(photoAction).toHaveBeenCalledTimes(2)
  expect(create).not.toHaveBeenCalled()
})

it("closes a normal successful create and photo upload without a partial-save warning", async () => {
  const saved = { ...rental("success"), name: "正常新增设备" }
  renderAssets()
  const create = vi.spyOn(api, "createAsset").mockResolvedValue(saved)
  const upload = vi.spyOn(api, "uploadAssetPhoto").mockResolvedValue(saved)
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "新增资产" }))
  await user.type(screen.getByLabelText("资产名称"), saved.name)
  await user.selectOptions(screen.getByLabelText("资产归属"), "owned")
  await user.selectOptions(screen.getByLabelText("取得方式"), "purchase")
  await user.selectOptions(screen.getByLabelText("采购渠道"), "offline")
  const photo = new File(["photo"], "equipment.png", { type: "image/png" })
  await user.upload(screen.getByLabelText("辨识照片（可选）"), photo)
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(create).toHaveBeenCalledTimes(1)
  expect(upload).toHaveBeenCalledWith(saved.id, photo)
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

it("retains an unknown legacy category on edit and can clear it", async () => {
  const item = { ...rental(), category: "原有专用设备" }
  renderAssets([item])
  const save = vi.spyOn(api, "updateAsset").mockResolvedValue(item)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  const category = screen.getByLabelText("资产分类")
  expect(category).toHaveValue("原有专用设备")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(item.id, expect.any(Object)))
  expect(save.mock.calls[0][1]).not.toHaveProperty("category")
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  await user.selectOptions(screen.getByLabelText("资产分类"), "")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenLastCalledWith(item.id, expect.objectContaining({ category: "" })))
})

it("omits an unchanged whitespace-bearing legacy category from unrelated edits", async () => {
  const item = { ...rental(), category: "  旧教学器材  " }
  renderAssets([item])
  const save = vi.spyOn(api, "updateAsset").mockResolvedValue(item)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  expect(screen.getByLabelText("资产分类")).toHaveValue("  旧教学器材  ")
  await user.clear(screen.getByLabelText("资产名称"))
  await user.type(screen.getByLabelText("资产名称"), "更新后的设备")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(item.id, expect.objectContaining({ name: "更新后的设备" })))
  expect(save.mock.calls[0][1]).not.toHaveProperty("category")
})

it("keeps another edit draft while restoring the original category after preset and empty choices", async () => {
  const item = { ...rental(), category: "  旧教学器材  " }
  const { rerender, client } = renderAssets([item])
  const save = vi.spyOn(api, "updateAsset").mockResolvedValue(item)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  await user.clear(screen.getByLabelText("资产名称"))
  await user.type(screen.getByLabelText("资产名称"), "更新后的设备")
  const category = screen.getByLabelText("资产分类")
  await waitFor(() => expect(within(category).getByRole("option", { name: "显示器" })).toBeVisible())
  await user.selectOptions(category, "显示器")
  rerender(<QueryClientProvider client={client}><AssetsPage /></QueryClientProvider>)
  expect(screen.getByLabelText("资产名称")).toHaveValue("更新后的设备")
  await user.selectOptions(category, "  旧教学器材  ")
  await user.selectOptions(category, "")
  await user.selectOptions(category, "  旧教学器材  ")
  expect(category).toHaveValue("  旧教学器材  ")
  expect(screen.getByLabelText("资产名称")).toHaveValue("更新后的设备")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(item.id, expect.objectContaining({ name: "更新后的设备" })))
  expect(save.mock.calls[0][1]).not.toHaveProperty("category")
})

it("applies an exact historical category to both page search and export", async () => {
  vi.spyOn(api, "listAssetCategories").mockResolvedValue({ ...catalog, legacy_categories: [" 旧分类保留 "] })
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:asset-export")
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined)
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined)
  renderAssets()
  const download = vi.spyOn(api, "exportAssets").mockResolvedValue({ blob: new Blob(["asset report"]), filename: "assets.csv" })
  const user = userEvent.setup()
  const category = screen.getByLabelText("分类")
  await waitFor(() => expect(within(category).getByRole("option", { name: "旧分类保留" })).toBeVisible())
  await user.selectOptions(category, " 旧分类保留 ")
  await user.click(screen.getByRole("button", { name: "查询" }))
  await waitFor(() => expect(api.listAssets).toHaveBeenLastCalledWith(expect.objectContaining({ category: " 旧分类保留 " })))
  await user.click(screen.getByRole("button", { name: "导出" }))
  const dialog = screen.getByRole("dialog", { name: "导出资产台账" })
  await user.type(within(dialog).getByLabelText("导出用途"), "历史资产核对")
  await user.click(within(dialog).getByRole("button", { name: "开始导出" }))
  await waitFor(() => expect(download).toHaveBeenCalledWith(expect.objectContaining({ category: " 旧分类保留 " })))
})

it("drops historical category, applied filter, dialog and cached asset rows when account changes in place", async () => {
  const originalAccount = auth.accountId
  const oldAsset = { ...rental("old-account"), category: "旧训练设备" }
  const newAsset = { ...rental("new-account"), category: "显示器" }
  let resolveNewAssets!: (value: api.AssetPage) => void
  let resolveNewCatalog!: (value: api.AssetCategoryCatalog) => void
  const list = vi.spyOn(api, "listAssets").mockImplementation(async () => auth.accountId === originalAccount
    ? { items: [oldAsset], page: 1, page_size: 50, total: 1 }
    : new Promise<api.AssetPage>((resolve) => { resolveNewAssets = resolve }))
  const directories = vi.spyOn(api, "listAssetCategories").mockImplementation(async () => auth.accountId === originalAccount
    ? catalog
    : new Promise<api.AssetCategoryCatalog>((resolve) => { resolveNewCatalog = resolve }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = <QueryClientProvider client={client}><AssetsPage /></QueryClientProvider>
  const { rerender } = render(view)
  const user = userEvent.setup()
  expect(await screen.findByText("租用电脑 old-account")).toBeVisible()
  const category = screen.getByLabelText("分类")
  await waitFor(() => expect(within(category).getByRole("option", { name: "旧训练设备" })).toBeVisible())
  await user.selectOptions(category, "旧训练设备")
  await user.click(screen.getByRole("button", { name: "查询" }))
  await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ category: "旧训练设备" })))
  await user.click(screen.getByRole("button", { name: "导出" }))
  expect(screen.getByRole("dialog", { name: "导出资产台账" })).toBeVisible()

  rerender(<QueryClientProvider client={client}><AssetsPage /></QueryClientProvider>)
  expect(screen.getByLabelText("分类")).toHaveValue("旧训练设备")
  expect(screen.getByRole("dialog", { name: "导出资产台账" })).toBeVisible()

  auth.accountId = "different-account"
  rerender(<QueryClientProvider client={client}><AssetsPage /></QueryClientProvider>)
  expect(screen.queryByRole("dialog", { name: "导出资产台账" })).not.toBeInTheDocument()
  expect(screen.getByLabelText("分类")).toHaveValue("")
  expect(within(screen.getByLabelText("分类")).queryByRole("option", { name: "旧训练设备" })).not.toBeInTheDocument()
  expect(screen.queryByText("租用电脑 old-account")).not.toBeInTheDocument()
  await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ category: "", page: 1 })))
  await waitFor(() => expect(directories).toHaveBeenCalledTimes(2))
  await act(async () => {
    resolveNewCatalog({ groups: [{ key: "other", label: "其他资产", options: ["其他"] }], legacy_categories: ["新账号旧值"] })
    resolveNewAssets({ items: [newAsset], page: 1, page_size: 50, total: 1 })
  })
  expect(await screen.findByText("租用电脑 new-account")).toBeVisible()
  expect(within(screen.getByLabelText("分类")).getByRole("option", { name: "新账号旧值" })).toBeVisible()
})

it("only selects started rentals and explains why a future lease cannot be returned", async () => {
  renderAssets([rental("started"), { ...rental("future", "9999-12-31"), rental_started_on: "9999-01-01" }])
  expect(await screen.findByLabelText("选择续期 STARTED")).toBeVisible()
  expect(screen.queryByLabelText("选择续期 FUTURE")).not.toBeInTheDocument()
  const future = screen.getByText("租用电脑 future").closest(".assetTableRow") as HTMLElement
  expect(within(future).getByRole("button", { name: "退租" })).toBeDisabled()
  expect(within(future).getByRole("button", { name: "退租" })).toHaveAttribute("title", "租赁尚未开始，暂无有效退租日期")
  await userEvent.click(screen.getByLabelText("选择续期 STARTED"))
  expect(screen.getByRole("button", { name: "批量续期（1）" })).toBeEnabled()
})

it("recovers a now-empty later page using the server page size", async () => {
  const { client } = renderAssets([rental("first")])
  await screen.findByText("租用电脑 first")
  const list = vi.mocked(api.listAssets)
  list.mockResolvedValue({ items: [rental("first")], page: 1, page_size: 1, total: 2 })
  await client.invalidateQueries()
  await userEvent.click(await screen.findByRole("button", { name: "下一页资产" }))
  await waitFor(() => expect(list).toHaveBeenCalledWith(expect.objectContaining({ page: 2 })))
  list.mockImplementation(async (filters) => ({ items: filters?.page === 1 ? [rental("remaining")] : [], page: filters?.page ?? 1, page_size: 1, total: 1 }))
  await client.invalidateQueries()
  expect(await screen.findByText("租用电脑 remaining")).toBeVisible()
  expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 }))
})

it.each(["online", "offline"])("creates an owned purchase with independent %s acquisition channel", async (channel) => {
  renderAssets()
  const save = vi.spyOn(api, "createAsset").mockResolvedValue({ id: "created" } as api.Asset)
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "新增资产" }))
  await user.type(screen.getByLabelText("资产名称"), "显示器")
  await user.selectOptions(screen.getByLabelText("资产归属"), "owned")
  await user.selectOptions(screen.getByLabelText("取得方式"), "purchase")
  await user.selectOptions(screen.getByLabelText("采购渠道"), channel)
  await user.selectOptions(screen.getByLabelText("保修方式"), "offline")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ ownership_type: "owned", acquisition_method: "purchase", acquisition_channel: channel, warranty_channel: "offline" })))
})

it("requires a description for other acquisition and submits it", async () => {
  renderAssets()
  const save = vi.spyOn(api, "createAsset").mockResolvedValue({ id: "created" } as api.Asset)
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "新增资产" }))
  await user.type(screen.getByLabelText("资产名称"), "桌子")
  await user.selectOptions(screen.getByLabelText("资产归属"), "owned")
  await user.selectOptions(screen.getByLabelText("取得方式"), "other")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  expect(save).not.toHaveBeenCalled()
  await user.type(screen.getByLabelText("获得方式说明"), "捐赠")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ acquisition_method: "other", acquisition_note: "捐赠", acquisition_channel: null })))
})

it("requires inclusive rental dates and does not substitute warranty dates", async () => {
  renderAssets()
  const save = vi.spyOn(api, "createAsset").mockResolvedValue({ id: "created" } as api.Asset)
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "新增资产" }))
  await user.type(screen.getByLabelText("资产名称"), "租用电脑")
  await user.selectOptions(screen.getByLabelText("资产归属"), "rented")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  expect(save).not.toHaveBeenCalled()
  const { fireEvent } = await import("@testing-library/react")
  fireEvent.change(screen.getByLabelText("租赁开始日期"), { target: { value: "2026-10-25" } })
  fireEvent.change(screen.getByLabelText("租赁到期日期（含当天）"), { target: { value: "2026-10-25" } })
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ rental_started_on: "2026-10-25", rental_expires_on: "2026-10-25", warranty_expires_on: null })))
})

it("edits location without sending unchanged stale rental snapshots", async () => {
  const asset = rental()
  renderAssets([asset])
  const save = vi.spyOn(api, "updateAsset").mockResolvedValue(asset)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  await user.clear(screen.getByLabelText("存放位置"))
  await user.type(screen.getByLabelText("存放位置"), "办公室")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalled())
  const payload = save.mock.calls[0][1]
  expect(payload).toMatchObject({ location: "办公室" })
  expect(payload).not.toHaveProperty("rental_expires_on")
  expect(payload).not.toHaveProperty("expected_rental_revision")
})

it("versions legacy to owned provenance confirmation even without rental dates", async () => {
  const asset: api.Asset = { ...rental(), ownership_type: null, rental_started_on: null, rental_expires_on: null, rental_revision: 7,
    acquisition_method: null, acquisition_channel: null, acquisition_note: null }
  renderAssets([asset])
  const save = vi.spyOn(api, "updateAsset").mockResolvedValue(asset)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  await user.selectOptions(screen.getByLabelText("资产归属"), "owned")
  await user.selectOptions(screen.getByLabelText("取得方式"), "purchase")
  await user.selectOptions(screen.getByLabelText("采购渠道"), "online")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith("asset-1", expect.objectContaining({ ownership_type: "owned", acquisition_method: "purchase", acquisition_channel: "online", expected_rental_revision: 7 })))
  expect(save.mock.calls[0][1]).not.toHaveProperty("rental_started_on")
  expect(save.mock.calls[0][1]).not.toHaveProperty("rental_expires_on")
})

it("versions owned source correction without sending unchanged ownership or lease dates", async () => {
  const asset: api.Asset = { ...rental(), ownership_type: "owned", rental_started_on: null, rental_expires_on: null, rental_revision: 4,
    acquisition_method: "purchase", acquisition_channel: "online", acquisition_note: null }
  renderAssets([asset])
  const save = vi.spyOn(api, "updateAsset").mockResolvedValue(asset)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  await user.selectOptions(screen.getByLabelText("采购渠道"), "offline")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith("asset-1", expect.objectContaining({ acquisition_channel: "offline", expected_rental_revision: 4 })))
  expect(save.mock.calls[0][1]).not.toHaveProperty("ownership_type")
  expect(save.mock.calls[0][1]).not.toHaveProperty("acquisition_method")
  expect(save.mock.calls[0][1]).not.toHaveProperty("rental_expires_on")
})

it("selects only explicit eligible current-page rentals and previews clamped mixed expiries", async () => {
  const owned = { ...rental("owned"), ownership_type: "owned" as const }
  const returned = { ...rental("returned"), rental_returned_on: "2026-01-01" }
  const retired = { ...rental("retired"), status: "retired" as const }
  renderAssets([rental(), rental("asset-2", "2024-01-31"), owned, returned, retired])
  const user = userEvent.setup()
  await user.click(await screen.findByLabelText("选择续期 ASSET-1"))
  expect(screen.queryByLabelText("选择续期 OWNED")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("选择续期 RETURNED")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("选择续期 RETIRED")).not.toBeInTheDocument()
  await user.click(screen.getByLabelText("选择续期 ASSET-2"))
  await user.click(screen.getByRole("button", { name: "批量续期（2）" }))
  const dialog = within(screen.getByRole("dialog"))
  await user.selectOptions(dialog.getByLabelText("续期周期"), "month")
  expect(dialog.getByText("2026-02-28")).toBeVisible()
  expect(dialog.getByText("2024-02-29")).toBeVisible()
  expect(dialog.getAllByText("续期后仍逾期")).toHaveLength(2)
  expect(dialog.getByRole("button", { name: "确认续期" })).toBeDisabled()
})

it("reuses one operation key after a lost response and invalidates both roots on success", async () => {
  const { client } = renderAssets([rental()])
  client.setQueryData(["assets", "sentinel"], { private: true })
  client.setQueryData(["portal", "account", "rental-reminders"], { private: true })
  const payloads: unknown[] = []
  const network = vi.fn(async (_url: unknown, init?: RequestInit) => {
    payloads.push(JSON.parse(String(init?.body)))
    if (payloads.length === 1) throw new TypeError("network lost")
    return new Response(JSON.stringify({ operation_key: "same", period: "week", replayed: true, items: [] }), { status: 200 })
  })
  vi.stubGlobal("fetch", network)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "续期" }))
  await user.click(screen.getByLabelText("我已核对原到期日和新到期日，确认续期"))
  await user.click(screen.getByRole("button", { name: "确认续期" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("重试")
  await user.click(screen.getByRole("button", { name: "确认续期" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(payloads).toHaveLength(2)
  expect(payloads[1]).toEqual(payloads[0])
  expect(payloads[0]).toMatchObject({ period: "week", assets: [{ asset_id: "asset-1", expected_rental_revision: 2 }], operation_key: expect.stringMatching(/^[\da-f-]{36}$/) })
  expect(client.getQueryState(["assets", "sentinel"])?.isInvalidated).toBe(true)
  expect(client.getQueryState(["portal", "account", "rental-reminders"])?.isInvalidated).toBe(true)
})

it("clears bulk selection on filters and permission changes", async () => {
  const { client, rerender } = renderAssets([rental()])
  const user = userEvent.setup()
  await user.click(await screen.findByLabelText("选择续期 ASSET-1"))
  expect(screen.getByRole("button", { name: "批量续期（1）" })).toBeEnabled()
  await user.selectOptions(screen.getByLabelText("归属筛选"), "rented")
  await user.click(screen.getByRole("button", { name: "查询" }))
  expect(await screen.findByRole("button", { name: "批量续期（0）" })).toBeDisabled()
  await user.click(screen.getByLabelText("选择续期 ASSET-1"))
  rerender(<QueryClientProvider client={client}><AssetsPage canManage={false} /></QueryClientProvider>)
  expect(screen.queryByRole("button", { name: /^批量续期/ })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "续期" })).not.toBeInTheDocument()
  rerender(<QueryClientProvider client={client}><AssetsPage /></QueryClientProvider>)
  expect(screen.getByRole("button", { name: "批量续期（0）" })).toBeDisabled()
})

it("includes revision only with changed rental facts", async () => {
  const asset = rental()
  renderAssets([asset])
  const save = vi.spyOn(api, "updateAsset").mockResolvedValue(asset)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  fireEvent.change(screen.getByLabelText("租赁开始日期"), { target: { value: "2025-01-02" } })
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalled())
  expect(save.mock.calls[0][1]).toMatchObject({ rental_started_on: "2025-01-02", expected_rental_revision: 2 })
  expect(save.mock.calls[0][1]).not.toHaveProperty("rental_expires_on")
})

it("shows legacy ownership as unconfirmed without requiring an invented source", async () => {
  const asset = { ...rental(), ownership_type: null, rental_started_on: null, rental_expires_on: null }
  renderAssets([asset])
  const save = vi.spyOn(api, "updateAsset").mockResolvedValue(asset)
  const user = userEvent.setup()
  expect(await screen.findByText("归属：待确认")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "编辑" }))
  expect(screen.getByLabelText("资产归属")).toHaveValue("")
  await user.click(screen.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalled())
  expect(save.mock.calls[0][1]).not.toHaveProperty("ownership_type")
})

it("blocks stale renewal on 409 and refreshes before reselection", async () => {
  renderAssets([rental()])
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "版本冲突", status: 409 }), { status: 409 })))
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "续期" }))
  await user.click(screen.getByLabelText("我已核对原到期日和新到期日，确认续期"))
  await user.click(screen.getByRole("button", { name: "确认续期" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("重新选择")
  expect(screen.getByRole("button", { name: "确认续期" })).toBeDisabled()
  await waitFor(() => expect(api.listAssets).toHaveBeenCalledTimes(2))
})

it("requires separate rental return confirmation and never invokes custody return", async () => {
  renderAssets([rental()])
  const custodyReturn = vi.spyOn(api, "returnAsset")
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify(rental()), { status: 200 }))
  vi.stubGlobal("fetch", request)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "退租" }))
  expect(screen.getByRole("button", { name: "确认退租" })).toBeDisabled()
  fireEvent.change(screen.getByLabelText("退租日期"), { target: { value: "2026-10-25" } })
  await user.click(screen.getByLabelText("已确认使用人归还，结束租赁"))
  await user.click(screen.getByRole("button", { name: "确认退租" }))
  await waitFor(() => expect(request).toHaveBeenCalled())
  expect(String(request.mock.calls[0][0])).toContain("/assets/asset-1/rental-return")
  expect(custodyReturn).not.toHaveBeenCalled()
})

it("requires custody return before rental return while preserving renewal", async () => {
  const asset = { ...rental(), status: "assigned" as const, current_assignment: { id: "assignment", person_name: "使用人", assigned_at: "2026-01-01" } as api.AssetAssignment }
  renderAssets([asset])
  expect(await screen.findByRole("button", { name: "退租" })).toBeDisabled()
  expect(screen.getByRole("button", { name: "归还" })).toBeEnabled()
  expect(screen.getByRole("button", { name: "续期" })).toBeEnabled()
})

it("allows a retired rental to record supplier return but never to renew", async () => {
  renderAssets([{ ...rental(), status: "retired" }])
  expect(await screen.findByRole("button", { name: "退租" })).toBeEnabled()
  expect(screen.queryByRole("button", { name: "续期" })).not.toBeInTheDocument()
  expect(screen.queryByLabelText("选择续期 ASSET-1")).not.toBeInTheDocument()
  await userEvent.setup().click(screen.getByRole("button", { name: "退租" }))
  expect(screen.getByRole("dialog", { name: "退租确认" })).toBeVisible()
  expect(screen.getByRole("button", { name: "确认退租" })).toBeDisabled()
})

it("loads audited renewal history with old/new dates and China operation time", async () => {
  renderAssets([rental()])
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{ id: "renewal", period: "month", previous_expires_on: "2026-01-31", rental_expires_on: "2026-02-28", actor_name: "管理员", created_at: "2026-01-01T01:02:03Z" }], total: 1, page: 1, page_size: 20 }), { status: 200 })))
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "续期历史" }))
  expect(await screen.findByText("2026-01-31 → 2026-02-28")).toBeVisible()
  expect(screen.getByText(/管理员 · .*09:02:03/)).toBeVisible()
  expect(screen.getByRole("button", { name: "下一页历史" })).toBeDisabled()
})

it("saves the selected warranty channel and reloads its fields when editing", async () => {
  const asset: api.Asset = {
    id: "asset-1", asset_code: "AST-1", name: "训练电脑", category: null,
    brand: null, model: null, serial_number: null, status: "available", location: null,
    purchase_date: null, warranty_expires_on: "2099-12-31", source_purchase_id: null,
    note: null, created_at: "", updated_at: "", current_assignment: null, photo: null,
    warranty_channel: "offline", warranty_service_location: "维修中心",
    warranty_contact_name: "张先生", warranty_contact_phone: "400-123-4567",
  }
  vi.spyOn(api, "listAssets").mockResolvedValue({ items: [asset], page: 1, page_size: 50, total: 1 })
  const save = vi.spyOn(api, "updateAsset").mockResolvedValue(asset)
  render(<QueryClientProvider client={new QueryClient()}>
    <AssetsPage />
  </QueryClientProvider>)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  const dialog = within(screen.getByRole("dialog"))
  expect(dialog.getByLabelText("售后联系人")).toHaveValue("张先生")
  expect(dialog.getByLabelText("售后电话")).toHaveValue("400-123-4567")
  await user.selectOptions(dialog.getByLabelText("保修方式"), "online")
  expect(dialog.queryByLabelText("售后联系人")).not.toBeInTheDocument()
  await user.type(dialog.getByLabelText("产品链接"), "https://shop.example.com/item/1")
  await user.type(dialog.getByLabelText("订单号"), "00012345678901234567890")
  await user.click(dialog.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith("asset-1", expect.objectContaining({
    warranty_channel: "online", warranty_contact_name: null, warranty_contact_phone: null,
    warranty_service_location: null, warranty_product_url: "https://shop.example.com/item/1",
    warranty_order_number: "00012345678901234567890",
  })))
})

it("retains expired warranty information and links to the online product", async () => {
  render(<AssetWarrantyDetails asset={{
    warranty_expires_on: "2000-01-01", warranty_channel: "online",
    warranty_product_url: "https://shop.example.com/item/1", warranty_order_number: "000123",
  }} />)
  await userEvent.setup().click(screen.getByText("已过保 · 查看售后信息"))
  expect(screen.getByText("000123")).toBeVisible()
  expect(screen.getByRole("link", { name: "打开商品详情" })).toHaveAttribute("href", "https://shop.example.com/item/1")
})
