import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import type { RevenueShareReconcileResult } from "./api"
import { RevenueShareManagementPage } from "./RevenueShareManagementPage"

const mocks = vi.hoisted(() => ({ configuration: vi.fn(), reconcile: vi.fn() }))
vi.mock("./api", async (original) => ({
  ...(await original()),
  getRevenueShareConfiguration: mocks.configuration,
  reconcileRevenueShare: mocks.reconcile,
}))

beforeEach(() => {
  mocks.configuration.mockReset().mockResolvedValue({ organizations: [], referral_codes: [], rules: [], people: [], projects: [] })
  mocks.reconcile.mockReset()
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><RevenueShareManagementPage /></QueryClientProvider>)
}

function batch(processed: number, allocated: number, next: string | null): RevenueShareReconcileResult {
  return { processed, allocated, pending: processed - allocated, has_more: Boolean(next), next_cursor: next }
}

function deferredBatch() {
  let resolve!: (result: RevenueShareReconcileResult) => void
  const promise = new Promise<RevenueShareReconcileResult>((done) => { resolve = done })
  return { promise, resolve }
}

it("processes batches in order and displays cumulative progress", async () => {
  mocks.reconcile.mockResolvedValueOnce(batch(50, 40, "cursor-1"))
    .mockResolvedValueOnce(batch(30, 25, null))
  const user = userEvent.setup()
  renderPage()
  await user.click(screen.getByRole("button", { name: "处理待重算付款" }))
  expect(await screen.findByText("本轮处理完成")).toBeVisible()
  expect(screen.getByText("累计已处理 80 笔，成功分配 65 笔，仍待分配 15 笔")).toBeVisible()
  expect(mocks.reconcile.mock.calls).toEqual([
    [{ batch_size: 50, cursor: null }], [{ batch_size: 50, cursor: "cursor-1" }],
  ])
})

it("retains successful progress and retries the failed batch cursor", async () => {
  mocks.reconcile.mockResolvedValueOnce(batch(50, 40, "cursor-1"))
    .mockRejectedValueOnce(new TypeError("network lost"))
    .mockResolvedValueOnce(batch(10, 8, null))
  const user = userEvent.setup()
  renderPage()
  await user.click(screen.getByRole("button", { name: "处理待重算付款" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("已完成的进度已保留")
  expect(screen.getByText("累计已处理 50 笔，成功分配 40 笔，仍待分配 10 笔")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "重试增量重算" }))
  expect(await screen.findByText("本轮处理完成")).toBeVisible()
  expect(screen.getByText("累计已处理 60 笔，成功分配 48 笔，仍待分配 12 笔")).toBeVisible()
  expect(mocks.reconcile.mock.calls[2]).toEqual([{ batch_size: 50, cursor: "cursor-1" }])
})

it("stops after the current batch and can continue from its next cursor", async () => {
  const pending = deferredBatch()
  mocks.reconcile.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(batch(10, 10, null))
  const user = userEvent.setup()
  renderPage()
  await user.click(screen.getByRole("button", { name: "处理待重算付款" }))
  await user.click(screen.getByRole("button", { name: "停止后续批次" }))
  expect(screen.getByText("当前批次完成后停止")).toBeVisible()
  await act(async () => pending.resolve(batch(50, 40, "cursor-1")))
  expect(screen.getByText("已暂停后续批次")).toBeVisible()
  expect(mocks.reconcile).toHaveBeenCalledTimes(1)
  await user.click(screen.getByRole("button", { name: "继续增量重算" }))
  expect(await screen.findByText("本轮处理完成")).toBeVisible()
  expect(screen.getByText("累计已处理 60 笔，成功分配 50 笔，仍待分配 10 笔")).toBeVisible()
  expect(mocks.reconcile.mock.calls[1]).toEqual([{ batch_size: 50, cursor: "cursor-1" }])
})

it("does not start duplicate work on double click or send more batches after unmount", async () => {
  const pending = deferredBatch()
  mocks.reconcile.mockReturnValueOnce(pending.promise)
  const user = userEvent.setup()
  const { unmount } = renderPage()
  await user.dblClick(screen.getByRole("button", { name: "处理待重算付款" }))
  expect(mocks.reconcile).toHaveBeenCalledTimes(1)
  unmount()
  await act(async () => pending.resolve(batch(50, 40, "cursor-1")))
  expect(mocks.reconcile).toHaveBeenCalledTimes(1)
})

it("rejects a non-advancing server cursor instead of looping indefinitely", async () => {
  mocks.reconcile.mockResolvedValueOnce(batch(50, 40, "cursor-1"))
    .mockResolvedValueOnce(batch(50, 40, "cursor-1"))
  const user = userEvent.setup()
  renderPage()
  await user.click(screen.getByRole("button", { name: "处理待重算付款" }))
  await waitFor(() => expect(screen.getByRole("button", { name: "重试增量重算" })).toBeEnabled())
  expect(mocks.reconcile).toHaveBeenCalledTimes(2)
  expect(screen.getByText("累计已处理 50 笔，成功分配 40 笔，仍待分配 10 笔")).toBeVisible()
})
