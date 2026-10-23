import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, within, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"
import * as api from "./cloudBackupApi"
import * as authorization from "./cloudAuthorizationApi"
import { OperationsCloudBackupPanel } from "./OperationsCloudBackupPanel"

const target = { provider: "baidu" as const, enabled: false, configured: true, remote_path: "/百度/ERP", username: "backup", credential_present: true, revision: 1, connection_status: "untested" as const, last_tested_at: null, last_success_at: null, last_backup_id: null, last_error_code: null }
let hasAuth = false
vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => hasAuth ? { status: "authenticated", user: { account_id: "owner", is_super_admin: true, permissions: ["operations.monitor.read"] } } : null }))
function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><OperationsCloudBackupPanel autoRefresh={false} /></QueryClientProvider>)
  return client
}
afterEach(() => { hasAuth = false; vi.restoreAllMocks() })
it("preserves stored credentials on empty password and gates enabling on a verified connection", async () => {
  vi.spyOn(api, "getCloudBackups").mockResolvedValue({ targets: [target], jobs: [], worker_last_seen_at: null })
  const save = vi.spyOn(api, "saveCloudBackup").mockResolvedValue(target)
  const client = setup()
  const user = userEvent.setup()
  const card = await screen.findByRole("group", { name: "百度网盘" })
  expect(within(card).getByRole("button", { name: "启用自动复制" })).toBeDisabled()
  await user.click(within(card).getByRole("button", { name: "保存配置" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith("baidu", { remote_path: "/百度/ERP", username: "backup", enabled: false }))
  await user.type(within(card).getByLabelText("连接器密码"), "private-secret")
  await user.click(within(card).getByRole("button", { name: "保存配置" }))
  await waitFor(() => expect(within(card).getByLabelText("连接器密码")).toHaveValue(""))
  expect(JSON.stringify(client.getQueryCache().getAll().map(q => q.state.data))).not.toContain("private-secret")
  expect(within(card).getByText("暂无已验证云备份")).toBeVisible()
})
it("reports loading failure safely without exposing external exception text", async () => {
  vi.spyOn(api, "getCloudBackups").mockRejectedValue(new Error("private-secret"))
  setup()
  expect(await screen.findByRole("alert")).toHaveTextContent("云备份状态读取失败")
  expect(screen.queryByText(/private-secret/)).not.toBeInTheDocument()
})
it("enqueues connection tests and copies only a saved enabled tested target and shows safe job results", async () => {
  const verified = { ...target, connection_status: "ok" as const, enabled: true }
  const job: api.CloudJob = { id: "j1", provider: "baidu", kind: "copy", status: "failed", created_at: "2026-10-02T00:00:00Z", started_at: null, completed_at: "2026-10-02T00:01:00Z", backup_id: "backup-1", error_code: "secret-provider-error" }
  vi.spyOn(api, "getCloudBackups").mockResolvedValue({ targets: [verified], jobs: [job], worker_last_seen_at: null })
  const enqueue = vi.spyOn(api, "enqueueCloudBackup").mockResolvedValue({ ...job, status: "queued" })
  setup()
  const user = userEvent.setup()
  const card = await screen.findByRole("group", { name: "百度网盘" })
  await user.click(within(card).getByRole("button", { name: "写入/读回测试" }))
  await waitFor(() => expect(enqueue).toHaveBeenCalledWith("baidu", "test"))
  expect(await within(card).findByRole("status")).toHaveTextContent("尚未完成")
  await user.click(within(card).getByRole("button", { name: "重试复制最新完整备份" }))
  await waitFor(() => expect(enqueue).toHaveBeenCalledWith("baidu", "copy"))
  expect(await within(card).findByRole("status")).toHaveTextContent("尚未验证成功")
  expect(screen.getByText("备份：backup-1")).toBeVisible()
  expect(screen.queryByText(/secret-provider-error/)).not.toBeInTheDocument()
})
it("rejects dirty unsaved configuration actions", async () => {
  vi.spyOn(api, "getCloudBackups").mockResolvedValue({ targets: [{ ...target, connection_status: "ok", enabled: true }], jobs: [], worker_last_seen_at: null })
  setup()
  const user = userEvent.setup()
  const card = await screen.findByRole("group", { name: "百度网盘" })
  await user.type(within(card).getByLabelText("远端逻辑路径"), "/changed")
  expect(within(card).getByRole("button", { name: "写入/读回测试" })).toBeDisabled()
  expect(within(card).getByRole("button", { name: "停用自动复制" })).toBeDisabled()
  expect(within(card).getByRole("button", { name: "重试复制最新完整备份" })).toBeDisabled()
})
it("locks target actions while a job is active and accepts nullable unconfigured inputs", async () => {
  vi.spyOn(api, "getCloudBackups").mockResolvedValue({ targets: [{ ...target, remote_path: null, username: null, configured: false, credential_present: false }], jobs: [{ id: "test1", provider: "baidu", kind: "test", status: "queued", created_at: "2026-10-02T00:00:00Z", started_at: null, completed_at: null, backup_id: null, error_code: null }], worker_last_seen_at: null })
  setup()
  const card = await screen.findByRole("group", { name: "百度网盘" })
  expect(within(card).getByLabelText("远端逻辑路径")).toHaveValue("")
  expect(within(card).getByRole("button", { name: "保存配置" })).toBeDisabled()
  expect(within(card).getByRole("status")).toHaveTextContent("排队")
})
it("enables a tested unchanged target without overwriting the stored password", async () => {
  const verified = { ...target, connection_status: "ok" as const }
  vi.spyOn(api, "getCloudBackups").mockResolvedValue({ targets: [verified], jobs: [], worker_last_seen_at: null })
  const save = vi.spyOn(api, "saveCloudBackup").mockResolvedValue({ ...verified, enabled: true })
  setup()
  const user = userEvent.setup()
  const card = await screen.findByRole("group", { name: "百度网盘" })
  await user.click(within(card).getByRole("button", { name: "启用自动复制" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith("baidu", { remote_path: "/百度/ERP", username: "backup", enabled: true }))
})
it("gates native authorization on prerequisites, retains manual configuration and never offers Quark QR", async () => {
  hasAuth = true
  vi.spyOn(api, "getCloudBackups").mockResolvedValue({ targets: [target, { ...target, provider: "quark" }], jobs: [], worker_last_seen_at: null })
  vi.spyOn(authorization, "getCloudApplication").mockResolvedValue({ provider: "baidu", client_id: "app", secret_present: true, revision: 1, configured: true, prerequisites: ["cloud_connector_unavailable"] })
  setup()
  const card = await screen.findByRole("group", { name: "百度网盘" })
  await userEvent.setup().click(within(card).getByRole("button", { name: "高级选项：自有应用" }))
  expect(await within(card).findByRole("button", { name: "扫码绑定" })).toBeDisabled()
  expect(await within(card).findByText(/连接器未安装或不可达/)).toBeVisible()
  expect(within(card).getByLabelText("连接器密码")).toBeVisible()
  const quark = screen.getByRole("group", { name: "夸克网盘（实验性）" })
  expect(within(quark).queryByRole("button", { name: "扫码绑定" })).not.toBeInTheDocument()
  expect(within(quark).getByText(/本期不开放扫码/)).toBeVisible()
})
it("preserves pasted tokens across focus and target refresh", async () => {
  hasAuth = true
  vi.spyOn(api, "getCloudBackups").mockResolvedValue({ targets: [target], jobs: [], worker_last_seen_at: null })
  vi.spyOn(authorization, "getCloudApplication").mockResolvedValue({ provider: "baidu", client_id: null, secret_present: false, revision: 0, configured: false, prerequisites: ["cloud_app_unconfigured"] })
  const session: authorization.CloudAuthSessionView = { id: "import", provider: "baidu", mode: "openlist_builtin_import", state: "awaiting_import", revision: 1, expires_at: "2099-01-01T00:00:00Z", account_label: null, selected_directory: null, error_code: null, next_poll_after_seconds: 1 }
  vi.spyOn(authorization, "createCloudAuthorization").mockResolvedValue(session)
  const client = setup()
  await userEvent.setup().click(await screen.findByRole("button", { name: "免填应用信息绑定" }))
  const access = await screen.findByLabelText("访问令牌（Access Token）")
  fireEvent.change(access, { target: { value: "synthetic-access" } })
  fireEvent.change(screen.getByLabelText("刷新令牌（Refresh Token）"), { target: { value: "synthetic-refresh" } })
  fireEvent(window, new Event("focus"))
  await act(async () => { client.setQueryData(["operations", "cloud-backups"], { targets: [{ ...target, revision: 2 }], jobs: [], worker_last_seen_at: null }) })
  expect(screen.getByLabelText("访问令牌（Access Token）")).toBe(access)
  expect(access).toHaveValue("synthetic-access")
  expect(screen.getByLabelText("刷新令牌（Refresh Token）")).toHaveValue("synthetic-refresh")
  expect(authorization.createCloudAuthorization).toHaveBeenCalledOnce()
  vi.mocked(api.getCloudBackups).mockRejectedValue(new Error("synthetic-status-error"))
  await act(async () => { await client.refetchQueries({ queryKey: ["operations", "cloud-backups"] }) })
  expect(screen.getByLabelText("访问令牌（Access Token）")).toBe(access)
  expect(access).toHaveValue("synthetic-access")
  expect(screen.queryByText(/synthetic-status-error/)).not.toBeInTheDocument()
})
it.each([["own_app", "自有应用：直接扫码"], ["openlist_builtin_import", "免填应用信息：外部授权后导入"], [null, "授权来源未知"]] as const)("displays actual target source %s without changing it to the new default", async (authorization_mode, label) => {
  vi.spyOn(api, "getCloudBackups").mockResolvedValue({ targets: [{ ...target, managed: true, authorization_mode }], jobs: [], worker_last_seen_at: null })
  setup()
  expect(await screen.findByText(`当前授权来源：${label}`)).toBeVisible()
})
it("blocks managed automatic enable pending real restore acceptance but permits an explicit acceptance copy while disabled", async () => {
  vi.spyOn(api, "getCloudBackups").mockResolvedValue({ targets: [{ ...target, managed: true, authorization_status: "valid", selected_directory: "/中文 网盘备份", remote_path: "/", connection_status: "ok" }], jobs: [], worker_last_seen_at: null })
  const enqueue = vi.spyOn(api, "enqueueCloudBackup").mockResolvedValue({ id: "copy", provider: "baidu", kind: "copy", status: "queued", created_at: "2026-10-06T00:00:00Z", started_at: null, completed_at: null, backup_id: null, error_code: null })
  setup()
  const card = await screen.findByRole("group", { name: "百度网盘" })
  expect(within(card).getByRole("button", { name: "启用自动复制" })).toBeDisabled()
  expect(within(card).getByText(/待真实网盘.*隔离恢复验收/)).toBeVisible()
  expect(within(card).getByLabelText("远端逻辑路径")).toHaveValue("/中文 网盘备份")
  expect(within(card).getByLabelText("远端逻辑路径")).toBeDisabled()
  expect(within(card).getByRole("button", { name: "保存配置" })).toBeDisabled()
  const copy = within(card).getByRole("button", { name: "手动复制完整备份用于验收" })
  expect(copy).toBeEnabled()
  await userEvent.setup().click(copy)
  await waitFor(() => expect(enqueue).toHaveBeenCalledWith("baidu", "copy"))
})
it("stops managed manual copying when authorization is invalid without deleting historical backup facts", async () => {
  vi.spyOn(api, "getCloudBackups").mockResolvedValue({ targets: [{ ...target, managed: true, authorization_status: "reauthorization_required", selected_directory: "/ERP", remote_path: "/", connection_status: "ok", last_success_at: "2026-10-05T00:00:00Z", last_backup_id: "old-backup" }], jobs: [], worker_last_seen_at: null })
  setup()
  const card = await screen.findByRole("group", { name: "百度网盘" })
  expect(within(card).getByRole("button", { name: "手动复制完整备份用于验收" })).toBeDisabled()
  expect(within(card).getByText(/授权已失效.*本地备份/)).toBeVisible()
  expect(within(card).getByText(/历史云备份记录.*old-backup/)).toBeVisible()
  expect(within(card).queryByText(/^已验证云备份/)).not.toBeInTheDocument()
})
