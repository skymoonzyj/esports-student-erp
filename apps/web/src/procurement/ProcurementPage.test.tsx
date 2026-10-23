import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import type { PurchasePage, PurchaseRecord, SettlementCenter } from "./api"
import { ProcurementPage } from "./ProcurementPage"

const apiMocks = vi.hoisted(() => ({
  addSettlementCenterReserveFund: vi.fn(),
  createPurchase: vi.fn(),
  createSettlementCenter: vi.fn(),
  downloadProcurementInvoice: vi.fn(),
  exportPurchases: vi.fn(),
  findInvoiceMatches: vi.fn(),
  listPurchases: vi.fn(),
  listSettlementCenters: vi.fn(),
  removeProcurementInvoice: vi.fn(),
  updatePurchase: vi.fn(),
  updatePurchaseReimbursement: vi.fn(),
  updatePurchaseSettlement: vi.fn(),
  updateSettlementCenter: vi.fn(),
  uploadProcurementInvoice: vi.fn(),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  ...apiMocks,
}))

const center: SettlementCenter = {
  id: "10000000-0000-4000-8000-000000000001",
  name: "青训营运营中心",
  is_active: true,
  reserve_fund_total_cents: 20_000,
  reserve_fund_used_cents: 5_000,
  reserve_fund_remaining_cents: 15_000,
  created_at: "2026-08-28T09:00:00+08:00",
  updated_at: "2026-08-28T09:00:00+08:00",
}

const record: PurchaseRecord = {
  id: "20000000-0000-4000-8000-000000000001",
  purchase_date: "2026-08-28",
  item_name: "饮用水",
  purchase_channel: "online",
  product_url: "https://shop.example.com/products/water",
  quantity: "2.500",
  unit_price_cents: 1299,
  total_price_cents: 3248,
  settlement_center_id: center.id,
  settlement_center_name: center.name,
  uses_reserve_fund: false,
  is_reimbursed: false,
  is_settled: false,
  settled_on: null,
  has_invoice: false,
  invoice: null,
  note: "日常补货",
  created_at: "2026-08-28T09:00:00+08:00",
  updated_at: "2026-08-28T09:00:00+08:00",
}

function purchasePage(overrides: Partial<PurchasePage> = {}): PurchasePage {
  return {
    items: [record],
    page: 1,
    page_size: 50,
    total: 1,
    total_amount_cents: 3248,
    reimbursed_amount_cents: 0,
    unreimbursed_amount_cents: 3248,
    settled_amount_cents: 0,
    unsettled_amount_cents: 3248,
    ...overrides,
  }
}

function renderPage(
  permissions: {
    canManage: boolean
    canSettle: boolean
    canExport: boolean
    canManageCenters: boolean
    canManageInvoices: boolean
    canCreateAssets?: boolean
  },
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ProcurementPage {...permissions} />
    </QueryClientProvider>,
  )
  return client
}

beforeEach(() => {
  for (const mock of Object.values(apiMocks)) mock.mockReset()
  apiMocks.listSettlementCenters.mockResolvedValue([center])
  apiMocks.listPurchases.mockResolvedValue(purchasePage())
  apiMocks.createPurchase.mockResolvedValue(record)
  apiMocks.addSettlementCenterReserveFund.mockResolvedValue({
    ...center,
    reserve_fund_total_cents: 30_000,
    reserve_fund_remaining_cents: 25_000,
  })
  apiMocks.createSettlementCenter.mockResolvedValue(center)
  apiMocks.updateSettlementCenter.mockResolvedValue(center)
  apiMocks.updatePurchase.mockResolvedValue(record)
  apiMocks.updatePurchaseReimbursement.mockResolvedValue({ ...record, is_reimbursed: true })
  apiMocks.updatePurchaseSettlement.mockResolvedValue(record)
  apiMocks.findInvoiceMatches.mockResolvedValue({
    amount_cents: record.total_price_cents,
    matched_total_cents: record.total_price_cents,
    suggested_purchase_ids: [record.id],
    candidates: [{
      id: record.id,
      purchase_date: record.purchase_date,
      item_name: record.item_name,
      total_price_cents: record.total_price_cents,
      settlement_center_id: center.id,
      settlement_center_name: center.name,
    }],
  })
  apiMocks.uploadProcurementInvoice.mockResolvedValue({ id: "invoice-1" })
  apiMocks.removeProcurementInvoice.mockResolvedValue(undefined)
  apiMocks.downloadProcurementInvoice.mockResolvedValue({
    blob: new Blob(["invoice"]),
    filename: "invoice.pdf",
  })
  apiMocks.exportPurchases.mockResolvedValue({
    blob: new Blob(["purchase report"]),
    filename: "procurement.xlsx",
  })
})

it("keeps a purchase operation key after network failure and renews it for a new dialog", async () => {
  const user = userEvent.setup()
  apiMocks.createPurchase.mockRejectedValueOnce(new Error("网络中断"))
  renderPage({ canManage: true, canSettle: true, canExport: true,
    canManageCenters: true, canManageInvoices: true })
  async function fillNew() {
    await user.click(await screen.findByRole("button", { name: "新增采购" }))
    const dialog = screen.getByRole("dialog", { name: "录入日常采购" })
    await user.type(within(dialog).getByLabelText("物品名称"), "清洁用品")
    await user.type(within(dialog).getByLabelText("单价（元）"), "10")
    await user.selectOptions(within(dialog).getByLabelText("归属结算中心"), center.id)
    return dialog
  }
  const dialog = await fillNew()
  await user.click(within(dialog).getByRole("button", { name: "保存采购记录" }))
  expect(await within(dialog).findByText("采购记录暂时无法保存")).toBeVisible()
  const first = apiMocks.createPurchase.mock.calls[0][0].operation_key
  expect(first).toMatch(/^[0-9a-f-]{36}$/)
  await user.click(within(dialog).getByRole("button", { name: "保存采购记录" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(apiMocks.createPurchase.mock.calls[1][0].operation_key).toBe(first)
  const next = await fillNew()
  await user.click(within(next).getByRole("button", { name: "保存采购记录" }))
  await waitFor(() => expect(apiMocks.createPurchase).toHaveBeenCalledTimes(3))
  expect(apiMocks.createPurchase.mock.calls[2][0].operation_key).not.toBe(first)
})

it("keeps a reserve operation key after failure and renews for the next addition", async () => {
  const user = userEvent.setup()
  apiMocks.addSettlementCenterReserveFund.mockRejectedValueOnce(new Error("网络中断"))
  renderPage({ canManage: true, canSettle: true, canExport: true,
    canManageCenters: true, canManageInvoices: true })
  await user.click(await screen.findByRole("button", { name: "结算中心" }))
  const dialog = screen.getByRole("dialog", { name: "结算中心" })
  async function start() {
    await user.click(within(dialog).getByRole("button", { name: `给${center.name}增加备用金` }))
    await user.type(within(dialog).getByLabelText(`${center.name}备用金金额`), "100")
  }
  await start()
  await user.click(within(dialog).getByRole("button", { name: "确认增加" }))
  expect(await screen.findByText("备用金暂时无法增加")).toBeVisible()
  const first = apiMocks.addSettlementCenterReserveFund.mock.calls[0][1].operation_key
  expect(first).toMatch(/^[0-9a-f-]{36}$/)
  await user.click(within(dialog).getByRole("button", { name: "确认增加" }))
  await waitFor(() => expect(within(dialog).queryByRole("button", { name: "确认增加" })).not.toBeInTheDocument())
  expect(apiMocks.addSettlementCenterReserveFund.mock.calls[1][1].operation_key).toBe(first)
  await start()
  await user.click(within(dialog).getByRole("button", { name: "确认增加" }))
  await waitFor(() => expect(apiMocks.addSettlementCenterReserveFund).toHaveBeenCalledTimes(3))
  expect(apiMocks.addSettlementCenterReserveFund.mock.calls[2][1].operation_key).not.toBe(first)
})

it("keeps finance read, settlement and export access separate from administrative entry", async () => {
  renderPage({
    canManage: false,
    canSettle: true,
    canExport: true,
    canManageCenters: false,
    canManageInvoices: true,
  })

  expect(await screen.findByRole("heading", { name: "日常采购" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "新增采购" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "结算中心" })).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "导出" })).toBeVisible()
  expect(screen.getByRole("button", { name: "上传发票" })).toBeVisible()
  expect(await screen.findByRole("button", { name: "确认结算" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "标记已报销" })).not.toBeInTheDocument()
})

it("shows the complete administrative toolset", async () => {
  renderPage({
    canManage: true,
    canSettle: true,
    canExport: true,
    canManageCenters: true,
    canManageInvoices: true,
  })

  expect(await screen.findByRole("button", { name: "新增采购" })).toBeVisible()
  expect(screen.getByRole("button", { name: "结算中心" })).toBeVisible()
  expect(screen.getByRole("button", { name: "导出" })).toBeVisible()
  expect(await screen.findByRole("button", { name: "编辑饮用水" })).toBeVisible()
  expect(screen.getByRole("button", { name: "标记已报销" })).toBeVisible()
  const productLink = screen.getByRole("link", { name: "打开饮用水商品链接" })
  expect(productLink).toHaveAttribute("href", "https://shop.example.com/products/water")
  expect(productLink).toHaveAttribute("target", "_blank")
  expect(productLink).toHaveAttribute("rel", "noopener noreferrer")
  expect(apiMocks.listSettlementCenters).toHaveBeenCalledWith(true)
})

it("requires reversing reimbursement before editing purchase information", async () => {
  const user = userEvent.setup()
  apiMocks.listPurchases.mockResolvedValue(purchasePage({
    items: [{ ...record, is_reimbursed: true }],
    reimbursed_amount_cents: record.total_price_cents,
    unreimbursed_amount_cents: 0,
  }))
  renderPage({
    canManage: true,
    canSettle: true,
    canExport: true,
    canManageCenters: true,
    canManageInvoices: true,
  })
  expect(await screen.findByText("修改采购信息前请先撤销报销")).toBeVisible()
  expect(screen.queryByRole("button", { name: "编辑饮用水" })).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "撤销报销" }))
  const dialog = screen.getByRole("dialog", { name: "撤销采购报销" })
  expect(within(dialog).getByText(/未结算且未开票的记录可重新编辑/)).toBeVisible()
  apiMocks.updatePurchaseReimbursement.mockResolvedValue(record)
  apiMocks.listPurchases.mockResolvedValue(purchasePage())
  await user.click(within(dialog).getByRole("button", { name: "确认撤销" }))
  await waitFor(() => expect(apiMocks.updatePurchaseReimbursement).toHaveBeenCalledWith(record.id, false))
  expect(await screen.findByRole("button", { name: "编辑饮用水" })).toBeVisible()
})

it("exports the selected reimbursement range as an Excel summary", async () => {
  const user = userEvent.setup()
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:procurement-export")
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined)
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined)
  renderPage({
    canManage: true,
    canSettle: true,
    canExport: true,
    canManageCenters: true,
    canManageInvoices: true,
  })

  await user.type(await screen.findByLabelText("搜索物品"), "饮用水")
  await user.click(screen.getByRole("button", { name: "查询" }))
  await waitFor(() => expect(apiMocks.listPurchases).toHaveBeenLastCalledWith(
    expect.objectContaining({ query: "饮用水" }),
  ))
  await user.click(await screen.findByRole("button", { name: "导出" }))
  const dialog = screen.getByRole("dialog", { name: "导出采购台账" })
  await user.selectOptions(within(dialog).getByLabelText("导出范围"), "unreimbursed")
  await user.selectOptions(within(dialog).getByLabelText("导出结算中心"), center.id)
  expect(within(dialog).getByText(`仅导出归属“${center.name}”的记录。`)).toBeVisible()
  const format = within(dialog).getByLabelText("文件格式")
  expect(format).toHaveValue("xlsx")
  expect(format).toBeDisabled()
  await user.type(within(dialog).getByLabelText("导出用途"), "月度待报销汇总")
  await user.click(within(dialog).getByRole("button", { name: "开始导出" }))

  await waitFor(() => expect(apiMocks.exportPurchases).toHaveBeenCalledWith(
    expect.objectContaining({
      query: "饮用水",
      page: 1,
      pageSize: 50,
      settlementCenterId: center.id,
      scope: "unreimbursed",
      format: "xlsx",
      purpose: "月度待报销汇总",
    }),
  ))
})

it("adds reserve funds to a settlement center and shows its balance", async () => {
  const user = userEvent.setup()
  renderPage({
    canManage: true,
    canSettle: true,
    canExport: true,
    canManageCenters: true,
    canManageInvoices: true,
  })

  await user.click(await screen.findByRole("button", { name: "结算中心" }))
  const dialog = screen.getByRole("dialog", { name: "结算中心" })
  expect(within(dialog).getByText("累计 ¥200.00")).toBeVisible()
  expect(within(dialog).getByText("已使用 ¥50.00")).toBeVisible()
  expect(within(dialog).getByText("剩余 ¥150.00")).toBeVisible()

  await user.click(within(dialog).getByRole("button", { name: `给${center.name}增加备用金` }))
  await user.type(within(dialog).getByLabelText(`${center.name}备用金金额`), "100")
  await user.type(within(dialog).getByLabelText(`${center.name}备用金备注`), "九月备用金")
  await user.click(within(dialog).getByRole("button", { name: "确认增加" }))

  await waitFor(() => expect(apiMocks.addSettlementCenterReserveFund).toHaveBeenCalledWith(
    center.id,
    { operation_key: expect.any(String), amount_cents: 10_000, note: "九月备用金" },
  ))
})

it("keeps administrative reimbursement separate from settlement", async () => {
  const user = userEvent.setup()
  renderPage({
    canManage: true,
    canSettle: true,
    canExport: true,
    canManageCenters: true,
    canManageInvoices: true,
  })

  await user.click(await screen.findByRole("button", { name: "标记已报销" }))
  const dialog = screen.getByRole("dialog", { name: "确认采购已报销" })
  expect(within(dialog).getByText(/不会改变结算中心的结算状态/)).toBeVisible()
  await user.click(within(dialog).getByRole("button", { name: "确认已报销" }))
  await waitFor(() => {
    expect(apiMocks.updatePurchaseReimbursement).toHaveBeenCalledWith(record.id, true)
  })

  await user.selectOptions(screen.getByLabelText("报销状态"), "reimbursed")
  await user.click(screen.getByRole("button", { name: "查询" }))
  await waitFor(() => expect(apiMocks.listPurchases).toHaveBeenLastCalledWith(
    expect.objectContaining({ reimbursementStatus: "reimbursed" }),
  ))
})

it("converts a selected month into its inclusive calendar range", async () => {
  const user = userEvent.setup()
  renderPage({
    canManage: true,
    canSettle: true,
    canExport: true,
    canManageCenters: true,
    canManageInvoices: true,
  })
  await screen.findByRole("heading", { name: "日常采购" })

  await user.click(screen.getByRole("button", { name: "按月" }))
  fireEvent.change(screen.getByLabelText("采购月份筛选"), {
    target: { value: "2024-02" },
  })
  await user.click(screen.getByRole("button", { name: "查询" }))

  await waitFor(() => expect(apiMocks.listPurchases).toHaveBeenLastCalledWith({
    reimbursementStatus: "",
    settlementStatus: "",
    invoiceStatus: "",
    startDate: "2024-02-01",
    endDate: "2024-02-29",
    page: 1,
    pageSize: 50,
  }))
})

it("submits quantity and unit price while leaving total calculation to the server", async () => {
  const user = userEvent.setup()
  renderPage({
    canManage: true,
    canSettle: true,
    canExport: true,
    canManageCenters: true,
    canManageInvoices: true,
  })
  await user.click(await screen.findByRole("button", { name: "新增采购" }))

  const dialog = screen.getByRole("dialog", { name: "录入日常采购" })
  await user.type(within(dialog).getByLabelText("物品名称"), "清洁用品")
  expect(within(dialog).queryByLabelText("商品链接（选填）")).not.toBeInTheDocument()
  await user.click(within(dialog).getByRole("button", { name: "线上" }))
  await user.type(within(dialog).getByLabelText("商品链接（选填）"), "https://old.example.com/item")
  await user.click(within(dialog).getByRole("button", { name: "线下" }))
  expect(within(dialog).queryByLabelText("商品链接（选填）")).not.toBeInTheDocument()
  await user.click(within(dialog).getByRole("button", { name: "线上" }))
  const productUrlInput = within(dialog).getByLabelText("商品链接（选填）")
  expect(productUrlInput).toHaveValue("")
  await user.type(productUrlInput, "https://shop.example.com/products/cleaning")
  await user.clear(within(dialog).getByLabelText("数量"))
  await user.type(within(dialog).getByLabelText("数量"), "2.5")
  await user.type(within(dialog).getByLabelText("单价（元）"), "12.99")
  await user.selectOptions(within(dialog).getByLabelText("归属结算中心"), center.id)
  expect(within(dialog).getByText("本单可用")).toBeVisible()
  expect(within(dialog).getByText("¥150.00")).toBeVisible()
  await user.click(within(dialog).getByLabelText("使用该结算中心备用金支付"))
  await user.click(within(dialog).getByLabelText("录入时标记为已报销"))
  expect(within(dialog).getByText("¥32.48")).toBeVisible()
  await user.click(within(dialog).getByRole("button", { name: "保存采购记录" }))

  await waitFor(() => expect(apiMocks.createPurchase).toHaveBeenCalledWith({
    operation_key: expect.any(String),
    purchase_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
    item_name: "清洁用品",
    purchase_channel: "online",
    product_url: "https://shop.example.com/products/cleaning",
    quantity: "2.5",
    unit_price_cents: 1299,
    settlement_center_id: center.id,
    uses_reserve_fund: true,
    note: null,
    is_reimbursed: true,
    is_settled: false,
    settled_on: null,
    add_to_assets: false,
    asset_category: null,
    asset_warranty_expires_on: null,
  }))
  const submitted = apiMocks.createPurchase.mock.calls[0]?.[0] as Record<string, unknown>
  expect(submitted).not.toHaveProperty("total_price_cents")
})

it("creates one independent asset record per purchased item", async () => {
  const user = userEvent.setup()
  renderPage({
    canManage: true,
    canSettle: true,
    canExport: true,
    canManageCenters: true,
    canManageInvoices: true,
    canCreateAssets: true,
  })
  await user.click(await screen.findByRole("button", { name: "新增采购" }))
  const dialog = screen.getByRole("dialog", { name: "录入日常采购" })
  await user.type(within(dialog).getByLabelText("物品名称"), "训练平板")
  await user.clear(within(dialog).getByLabelText("数量"))
  await user.type(within(dialog).getByLabelText("数量"), "2")
  await user.type(within(dialog).getByLabelText("单价（元）"), "2500")
  await user.selectOptions(within(dialog).getByLabelText("归属结算中心"), center.id)
  await user.click(within(dialog).getByLabelText("同步加入资产管理"))
  await user.type(within(dialog).getByLabelText("资产分类（选填）"), "训练设备")
  fireEvent.change(within(dialog).getByLabelText("保修到期日（选填）"), {
    target: { value: "2028-08-30" },
  })
  await user.click(within(dialog).getByRole("button", { name: "保存采购记录" }))

  await waitFor(() => expect(apiMocks.createPurchase).toHaveBeenCalledWith(
    expect.objectContaining({
      item_name: "训练平板",
      quantity: "2",
      add_to_assets: true,
      asset_category: "训练设备",
      asset_warranty_expires_on: "2028-08-30",
    }),
  ))
})

it("uploads an invoice only after its amount matches selected purchases", async () => {
  const user = userEvent.setup()
  renderPage({
    canManage: true,
    canSettle: true,
    canExport: true,
    canManageCenters: true,
    canManageInvoices: true,
  })
  await user.click(await screen.findByRole("button", { name: "上传发票" }))
  const dialog = screen.getByRole("dialog", { name: "上传并匹配发票" })
  await user.type(within(dialog).getByLabelText("发票金额（元）"), "32.48")
  await user.click(within(dialog).getByRole("button", { name: "自动匹配" }))
  expect(await within(dialog).findByText("¥32.48 / ¥32.48")).toBeVisible()

  const file = new File(["%PDF-1.4\n%%EOF"], "water-invoice.pdf", {
    type: "application/pdf",
  })
  const fileInput = within(dialog).getByLabelText("发票文件") as HTMLInputElement
  await user.upload(fileInput, file)
  expect(fileInput.files).toHaveLength(1)
  const form = fileInput.closest("form") as HTMLFormElement
  const invalidFields = Array.from(form.elements)
    .filter((element) => element instanceof HTMLInputElement && !element.checkValidity())
    .map((element) => (element as HTMLInputElement).outerHTML)
  expect(invalidFields).toEqual([])
  await user.click(within(dialog).getByRole("button", { name: "确认上传发票" }))

  await waitFor(() => expect(apiMocks.uploadProcurementInvoice).toHaveBeenCalledWith({
    file,
    amountCents: 3248,
    invoiceDate: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
    invoiceNumber: undefined,
    note: undefined,
    purchaseIds: [record.id],
  }))
})
