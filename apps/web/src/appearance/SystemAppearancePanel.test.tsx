import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import * as operationsApi from "../operations/api"
import { OperationsMonitorPage } from "../operations/OperationsMonitorPage"
import { AppearanceProvider } from "./AppearanceProvider"
import { createAppearanceStore } from "./store"
import { SystemAppearancePanel } from "./SystemAppearancePanel"

let saved = { theme: "clear", revision: 1 }
let writeStatus = 200
let readFails = false
let superAdmin = true
let managePermission = true
let submitted: { theme: string; expected_revision: number } | null = null
let csrf = ""
let holdSave = false
let releaseSave: (() => void) | null = null
let publicReadFails = false

beforeEach(() => {
  localStorage.clear()
  saved = { theme: "clear", revision: 1 }
  writeStatus = 200
  readFails = false
  superAdmin = true
  managePermission = true
  submitted = null
  csrf = ""
  holdSave = false
  releaseSave = null
  publicReadFails = false
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) return Response.json({ account_id: "test-account", person_id: "test-person", full_name: "测试超管", email: null, force_password_change: false, is_super_admin: superAdmin, permissions: managePermission ? ["operations.appearance.manage"] : [] })
    if (url.endsWith("/auth/csrf")) return Response.json({ csrf_token: "appearance-csrf" })
    if (url.endsWith("/operations/appearance")) {
      if (init?.method === "PUT") {
        submitted = JSON.parse(String(init.body))
        csrf = new Headers(init.headers).get("X-CSRF-Token") ?? ""
        if (holdSave) await new Promise<void>((resolve) => { releaseSave = resolve })
        if (writeStatus !== 200) return Response.json({ detail: "保存未完成", code: "appearance_conflict" }, { status: writeStatus })
        saved = { theme: submitted!.theme, revision: saved.revision + 1 }
      } else if (readFails) return Response.json({ detail: "外观读取暂不可用" }, { status: 503 })
      return Response.json(saved)
    }
    if (url.endsWith("/appearance")) return publicReadFails ? Response.json({ detail: "不可用" }, { status: 503 }) : Response.json(saved)
    throw new Error(`Unexpected request: ${url}`)
  }))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

function mount(content = <SystemAppearancePanel />) {
  const store = createAppearanceStore()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<AppearanceProvider store={store}><QueryClientProvider client={client}><AuthProvider>{content}</AuthProvider></QueryClientProvider></AppearanceProvider>)
  return store
}

it("keeps a draft local, cancels it, and publishes a saved selection through authenticated CSRF requests", async () => {
  const user = userEvent.setup()
  const store = mount()
  const command = await screen.findByRole("radio", { name: /深蓝效率/ })
  await user.click(command)
  expect(command).toBeChecked()
  expect(store.getSnapshot().theme).toBe("clear")
  await user.click(screen.getByRole("button", { name: "取消" }))
  expect(screen.getByRole("radio", { name: /清爽专业/ })).toBeChecked()
  await user.click(command)
  await user.click(screen.getByRole("button", { name: "保存全局外观" }))
  await screen.findByText(/已保存，全体用户/)
  expect(submitted).toEqual({ theme: "command", expected_revision: 1 })
  expect(csrf).toBe("appearance-csrf")
  expect(store.getSnapshot()).toEqual({ theme: "command", revision: 2 })
  expect(document.documentElement).toHaveAttribute("data-ui-theme", "command")
})

it("requires a fresh configuration after conflict and never applies the rejected draft", async () => {
  writeStatus = 409
  const user = userEvent.setup()
  const store = mount()
  await user.click(await screen.findByRole("radio", { name: /深蓝效率/ }))
  await user.click(screen.getByRole("button", { name: "保存全局外观" }))
  expect(await screen.findByRole("alert")).toHaveTextContent(/已被其他管理员更新/)
  expect(store.getSnapshot().theme).toBe("clear")
  expect(screen.getByRole("button", { name: "保存全局外观" })).toBeDisabled()
  saved = { theme: "clear", revision: 3 }
  await user.click(screen.getByRole("button", { name: "重新读取配置" }))
  await waitFor(() => expect(screen.getByRole("radio", { name: /清爽专业/ })).toBeChecked())
  writeStatus = 200
  await user.click(screen.getByRole("radio", { name: /深蓝效率/ }))
  await user.click(screen.getByRole("button", { name: "保存全局外观" }))
  await screen.findByText(/已保存，全体用户/)
  expect(submitted).toEqual({ theme: "command", expected_revision: 3 })
})

it("isolates read failures with a retry control", async () => {
  readFails = true
  mount()
  expect(await screen.findByRole("alert")).toHaveTextContent("外观读取暂不可用")
  readFails = false
  await userEvent.click(screen.getByRole("button", { name: "重新读取配置" }))
  expect(await screen.findByRole("radio", { name: /清爽专业/ })).toBeChecked()
})

it("preserves the saved theme and the user's draft after a failed save", async () => {
  writeStatus = 503
  const store = mount()
  await userEvent.click(await screen.findByRole("radio", { name: /深蓝效率/ }))
  await userEvent.click(screen.getByRole("button", { name: "保存全局外观" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("保存未完成")
  expect(screen.getByRole("radio", { name: /深蓝效率/ })).toBeChecked()
  expect(store.getSnapshot().theme).toBe("clear")
  expect(screen.getByRole("button", { name: "保存全局外观" })).toBeEnabled()
})

it("disables repeat saves and radio edits until an in-flight save completes", async () => {
  holdSave = true
  const store = mount()
  await userEvent.click(await screen.findByRole("radio", { name: /深蓝效率/ }))
  await userEvent.click(screen.getByRole("button", { name: "保存全局外观" }))
  expect(screen.getByRole("button", { name: "正在保存…" })).toBeDisabled()
  expect(screen.getByRole("radio", { name: /清爽专业/ })).toBeDisabled()
  expect(screen.getByRole("button", { name: "取消" })).toBeDisabled()
  expect(store.getSnapshot().theme).toBe("clear")
  await act(async () => releaseSave?.())
  await screen.findByText(/已保存，全体用户/)
  expect(store.getSnapshot().theme).toBe("command")
})

it.each([[false, true], [true, false]])("does not expose controls without actual superadmin and manage permission (%s, %s)", async (isSuper, hasPermission) => {
  superAdmin = isSuper
  managePermission = hasPermission
  mount()
  await actAuth()
  expect(screen.queryByRole("heading", { name: "系统外观" })).not.toBeInTheDocument()
  expect(submitted).toBeNull()
})

async function actAuth() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)) })
}

it("keeps the selected draft when independent monitor data finishes loading", async () => {
  let resolve!: (value: operationsApi.OperationsOverview) => void
  vi.spyOn(operationsApi, "getOperationsOverview").mockImplementation(() => new Promise((done) => { resolve = done }))
  mount(<OperationsMonitorPage />)
  const command = await screen.findByRole("radio", { name: /深蓝效率/ })
  await userEvent.click(command)
  await act(async () => resolve({
    generated_at: "2026-10-27T12:00:00Z", window_minutes: 60,
    health: { score: 100, status: "healthy" },
    traffic: { request_count: 0, client_error_count: 0, client_error_details: [], server_error_count: 0, error_rate_percent: 0, requests_per_second: 0, average_latency_ms: 0, p50_latency_ms: 0, p95_latency_ms: 0, p99_latency_ms: 0, max_latency_ms: 0, series: [], top_routes: [] },
    database: { status: "healthy", response_time_ms: 1, active_connections: 1, idle_connections: 1, total_connections: 2, max_connections: 100, utilization_percent: 2 },
    runtime: { status: "healthy", cpu_percent: 1, memory_used_mb: 1, memory_limit_mb: 1024, memory_percent: 1, process_memory_mb: 1, uptime_seconds: 60 },
    background_tasks: { status: "healthy", scheduler_status: "healthy", scheduler_last_seen_at: null, scheduler_lag_seconds: 0, scheduler_consecutive_failures: 0, scheduler_last_result_count: 0, total_tasks: 0, queued_tasks: 0, processing_tasks: 0, pending_review_tasks: 0, failed_tasks: 0, needs_attention_tasks: 0 },
  }))
  await screen.findByRole("heading", { name: "运维监控" })
  expect(screen.getByRole("radio", { name: /深蓝效率/ })).toBe(command)
  expect(command).toBeChecked()
})

it("aligns a mounted editor with authoritative external updates, including cancel and later save revision", async () => {
  const store = mount()
  const clear = await screen.findByRole("radio", { name: /清爽专业/ })
  const command = screen.getByRole("radio", { name: /深蓝效率/ })
  saved = { theme: "command", revision: 2 }
  await act(async () => { await store.refresh() })
  expect(screen.getByRole("radio", { name: /深蓝效率.*当前全局/ })).toBeChecked()
  expect(screen.getByRole("status")).toHaveTextContent("当前已保存 · 版本 2")
  expect(screen.getByRole("radio", { name: /清爽专业/ })).toBe(clear)
  await userEvent.click(clear)
  await userEvent.click(screen.getByRole("button", { name: "取消" }))
  expect(command).toBeChecked()
  await userEvent.click(clear)
  await userEvent.click(screen.getByRole("button", { name: "保存全局外观" }))
  await screen.findByText(/已保存，全体用户/)
  expect(submitted).toEqual({ theme: "clear", expected_revision: 2 })
})

it("preserves an unsaved selection while external authoritative revisions and current-global markers advance", async () => {
  const store = mount()
  const command = await screen.findByRole("radio", { name: /深蓝效率/ })
  await userEvent.click(command)
  saved = { theme: "clear", revision: 2 }
  await act(async () => { await store.refresh() })
  expect(command).toBeChecked()
  expect(screen.getByRole("radio", { name: /清爽专业.*当前全局/ })).not.toBeChecked()
  expect(screen.getByRole("status")).toHaveTextContent("尚未保存")
  await userEvent.click(screen.getByRole("button", { name: "保存全局外观" }))
  await screen.findByText(/已保存，全体用户/)
  expect(submitted).toEqual({ theme: "command", expected_revision: 2 })
})

it("uses an authoritative operations read to replace an unverified cache when the public read fails", async () => {
  localStorage.setItem("erp.appearance.v1", JSON.stringify({ version: 1, theme: "command", revision: 9999 }))
  publicReadFails = true
  const store = mount()
  await screen.findByRole("radio", { name: /清爽专业/ })
  expect(store.getSnapshot()).toEqual({ theme: "clear", revision: 1 })
  expect(document.documentElement).toHaveAttribute("data-ui-theme", "clear")
  expect(JSON.parse(localStorage.getItem("erp.appearance.v1")!)).toEqual({ version: 1, theme: "clear", revision: 1 })
  expect(screen.getByRole("radio", { name: /清爽专业.*当前全局/ })).toBeChecked()
  expect(screen.getByRole("status")).toHaveTextContent("当前已保存 · 版本 1")
  await userEvent.click(screen.getByRole("radio", { name: /深蓝效率/ }))
  await userEvent.click(screen.getByRole("button", { name: "保存全局外观" }))
  await screen.findByText(/已保存，全体用户/)
  expect(submitted).toEqual({ theme: "command", expected_revision: 1 })
})

it("does not roll back a newer public theme when an older operations read finishes", async () => {
  const fetcher = vi.mocked(fetch)
  const defaultFetch = fetcher.getMockImplementation()!
  let completeRead!: () => void
  let holdRead = false
  fetcher.mockImplementation(async (input, init) => {
    if (String(input).endsWith("/operations/appearance") && !init?.method && holdRead) {
      await new Promise<void>((resolve) => { completeRead = resolve })
      return Response.json({ theme: "clear", revision: 1 })
    }
    return defaultFetch(input, init)
  })
  const store = mount()
  await userEvent.click(await screen.findByRole("radio", { name: /深蓝效率/ }))
  writeStatus = 409
  await userEvent.click(screen.getByRole("button", { name: "保存全局外观" }))
  await screen.findByRole("alert")
  holdRead = true
  await userEvent.click(screen.getByRole("button", { name: "重新读取配置" }))
  saved = { theme: "command", revision: 3 }
  await act(async () => { await store.refresh() })
  await act(async () => completeRead())
  expect(store.getSnapshot()).toEqual({ theme: "command", revision: 3 })
  expect(screen.getByRole("radio", { name: /深蓝效率.*当前全局/ })).toBeChecked()
  expect(document.documentElement).toHaveAttribute("data-ui-theme", "command")
})
