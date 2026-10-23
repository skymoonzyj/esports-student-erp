import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import * as api from "./api"
import * as cloudApi from "./cloudBackupApi"
import { OperationsHistoryPanel } from "./OperationsHistoryPanel"

const alert: api.OperationsAlert = {
  id: "alert-1", key: "backup_failed", severity: "critical", title: "自动备份失败",
  detail: "最近一次备份未成功，请检查存储空间。", status: "active",
  first_seen_at: "2026-09-11T00:00:00Z", last_seen_at: "2026-09-11T00:05:00Z",
  resolved_at: null, acknowledged_at: null, acknowledged_by: null,
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><OperationsHistoryPanel autoRefresh={false} /></QueryClientProvider>)
}

beforeEach(() => {
  vi.spyOn(cloudApi, "getCloudBackups").mockResolvedValue({ targets: [], jobs: [], worker_last_seen_at: null })
  vi.spyOn(api, "getOperationsBackupStatus").mockResolvedValue({ status: "ok", completed_at: "2026-09-11T00:00:00Z", last_success_at: "2026-09-11T00:00:00Z", started_at: "2026-09-10T23:58:00Z", verification_at: "2026-09-11T00:00:00Z", verification_status: "verified", size_bytes: 1024 * 1024, error_code: null, reason: "最近一次备份成功", secondary_copy: { configured: false, status: "not_configured", last_success_at: null } })
  vi.spyOn(api, "getOperationsAlerts").mockResolvedValue({ items: [alert], total: 1, page: 1, page_size: 10 })
  vi.spyOn(api, "getOperationsHistory").mockResolvedValue({ items: [{ starts_at: "2026-09-11T00:00:00Z", request_count: 42, client_error_count: 2, server_error_count: 1, average_latency_ms: 12.5, max_latency_ms: 120 }], total: 21, page: 1, page_size: 20, retention_days: 30 })
})

afterEach(() => vi.restoreAllMocks())

it("acknowledges an alert without pretending that its problem is resolved", async () => {
  const user = userEvent.setup()
  const acknowledged = { ...alert, acknowledged_at: "2026-09-11T00:06:00Z", acknowledged_by: "user-1" }
  const action = vi.spyOn(api, "acknowledgeOperationsAlert").mockResolvedValue(acknowledged)
  renderPanel()
  await screen.findByText("自动备份失败")
  const backup = screen.getByRole("region", { name: "自动备份与恢复" })
  expect(within(backup).getByText("已通过")).toBeVisible()
  expect(within(backup).getByText("未配置")).toBeVisible()
  vi.mocked(api.getOperationsAlerts).mockResolvedValue({ items: [acknowledged], total: 1, page: 1, page_size: 10 })
  await user.click(screen.getByRole("button", { name: "确认已知晓" }))
  await waitFor(() => expect(action).toHaveBeenCalledWith("alert-1", expect.anything()))
  expect(await screen.findByText(/已确认 ·/)).toBeVisible()
  expect(screen.getByText("异常", { selector: "span" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "确认已知晓" })).not.toBeInTheDocument()
})

it("queries seven-day history, pages results, and independently filters alerts", async () => {
  const user = userEvent.setup()
  renderPanel()
  await screen.findByText("42")
  await user.selectOptions(screen.getByLabelText("历史范围"), "7")
  await waitFor(() => {
    const args = vi.mocked(api.getOperationsHistory).mock.lastCall!
    expect(new Date(args[1]).getTime() - new Date(args[0]).getTime()).toBe(7 * 86_400_000)
    expect(args[2]).toBe(1)
  })
  const history = screen.getByRole("region", { name: "历史请求记录" })
  await user.click(within(history).getByRole("button", { name: "下一页" }))
  await waitFor(() => expect(vi.mocked(api.getOperationsHistory).mock.lastCall?.[2]).toBe(2))
  await user.selectOptions(screen.getByLabelText("告警状态"), "resolved")
  await waitFor(() => expect(api.getOperationsAlerts).toHaveBeenCalledWith("resolved", 1))
})

it("retains the acknowledge action when the server rejects it", async () => {
  vi.spyOn(api, "acknowledgeOperationsAlert").mockRejectedValue(new Error("网络错误"))
  const user = userEvent.setup()
  renderPanel()
  await user.click(await screen.findByRole("button", { name: "确认已知晓" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("确认失败")
  expect(screen.getByRole("button", { name: "确认已知晓" })).toBeEnabled()
})
