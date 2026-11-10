import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { AuthProvider, useAuth } from "../auth/AuthProvider"
import type { AuthUser } from "../auth/AuthProvider"
import * as api from "./api"
import type { OperationsRequestError, OperationsRequestErrors } from "./api"
import { OperationsRequestErrorsPanel } from "./OperationsRequestErrorsPanel"

const accountId = "11111111-1111-4111-8111-111111111111"
const deletedAccountId = "22222222-2222-4222-8222-222222222222"
const administrator: AuthUser = {
  account_id: accountId,
  person_id: "44444444-4444-4444-8444-444444444444",
  full_name: "平台管理员",
  email: null,
  force_password_change: false,
  is_super_admin: true,
  permissions: ["operations.monitor.read"],
}
let activeUser = administrator
const knownError: OperationsRequestError = {
  id: "33333333-3333-4333-8333-333333333333",
  occurred_at: "2026-10-25T04:06:07Z",
  account_id: accountId,
  account_name: "张三",
  source_ip: "2001:db8::2",
  request_id: "request-abc",
  method: "GET",
  route: "/api/v1/people/{person_id}",
  status_code: 404,
  reason: "请求资源不存在",
}

function response(items: OperationsRequestError[], total = items.length, page = 1): OperationsRequestErrors {
  return { items, total, page, page_size: 20, retention_days: 30 }
}

function AuthActions() {
  const { logout, refreshUser } = useAuth()
  return <>
    <button type="button" onClick={() => void logout()}>测试退出</button>
    <button type="button" onClick={() => void refreshUser()}>测试刷新权限</button>
  </>
}

function renderPanel({ withAuth = true, actions = false }: { withAuth?: boolean; actions?: boolean } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const panel = <>{actions ? <AuthActions /> : null}<OperationsRequestErrorsPanel /></>
  const view = render(<QueryClientProvider client={client}>
    {withAuth ? <AuthProvider>{panel}</AuthProvider> : <OperationsRequestErrorsPanel />}
  </QueryClientProvider>)
  return { client, ...view }
}

beforeEach(() => {
  activeUser = administrator
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) return new Response(JSON.stringify(activeUser), { status: 200 })
    if (url.endsWith("/auth/csrf")) return new Response(JSON.stringify({ csrf_token: "test-token" }), { status: 200 })
    if (url.endsWith("/auth/logout")) return new Response(null, { status: 204 })
    throw new Error(`Unexpected request: ${url}`)
  }))
  vi.spyOn(api, "getOperationsRequestErrors").mockResolvedValue(response([]))
})

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it("does not render or fetch sensitive details without an auth provider", async () => {
  const { client } = renderPanel({ withAuth: false })

  expect(screen.queryByRole("region", { name: "请求错误明细" })).not.toBeInTheDocument()
  expect(api.getOperationsRequestErrors).not.toHaveBeenCalled()
  expect(client.getQueriesData({ queryKey: api.operationsKeys.requestErrorsRoot() }).every(([, data]) => data === undefined)).toBe(true)
})

it("shows account name and UUID, anonymous and deleted-account fallbacks, IP, Chinese reason, and correlation ID", async () => {
  vi.mocked(api.getOperationsRequestErrors).mockResolvedValue(response([
    knownError,
    { ...knownError, id: "anonymous", account_id: null, account_name: null, source_ip: null, request_id: null, status_code: 401 },
    { ...knownError, id: "deleted", account_id: deletedAccountId, account_name: null, source_ip: "192.0.2.7", status_code: 500 },
  ]))
  renderPanel()

  const panel = await screen.findByRole("region", { name: "请求错误明细" })
  expect(await within(panel).findByText("张三")).toBeVisible()
  expect(within(panel).getByText(accountId)).toBeVisible()
  expect(within(panel).getByText("2001:db8::2")).toBeVisible()
  expect(within(panel).getAllByText("2026-10-25 12:06:07")).toHaveLength(3)
  expect(within(panel).getAllByText("GET /api/v1/people/{person_id}")).toHaveLength(3)
  expect(within(panel).getByText("HTTP 404")).toBeVisible()
  expect(within(panel).getAllByText("请求资源不存在")).toHaveLength(3)
  expect(within(panel).getAllByText("request-abc")).toHaveLength(2)
  expect(within(panel).getByText("未登录／无法确认账号")).toBeVisible()
  expect(within(panel).getByText(deletedAccountId)).toBeVisible()
  expect(within(panel).getByText("仅记录功能启用后的错误，最多保留 30 天；后台批量写入可能短暂延迟。")).toBeVisible()
  expect(api.getOperationsRequestErrors).toHaveBeenCalledWith({
    days: 1, page: 1, accountId: "", sourceIp: "", statusCode: null,
  })
})

it("submits range and filters, clears previous-filter rows while loading, pages, and resets to defaults", async () => {
  const user = userEvent.setup()
  let resolveFiltered!: (value: OperationsRequestErrors) => void
  const filtered = new Promise<OperationsRequestErrors>((resolve) => { resolveFiltered = resolve })
  vi.mocked(api.getOperationsRequestErrors).mockImplementation((filters) => {
    if (filters.days === 7 && filters.page === 1) return filtered
    if (filters.days === 7 && filters.page === 2) return Promise.resolve(response([{ ...knownError, id: "page-2", account_name: "第二页" }], 21, 2))
    return Promise.resolve(response([{ ...knownError, account_name: "旧错误" }], 1))
  })
  renderPanel()
  await screen.findByText("旧错误")

  await user.selectOptions(screen.getByLabelText("错误范围"), "7")
  await user.type(screen.getByLabelText("账号 UUID"), accountId)
  await user.type(screen.getByLabelText("来源 IP"), "2001:db8::2")
  await user.type(screen.getByLabelText("HTTP 状态码"), "503")
  expect(api.getOperationsRequestErrors).toHaveBeenCalledTimes(1)
  await user.click(screen.getByRole("button", { name: "查询错误" }))
  await waitFor(() => expect(api.getOperationsRequestErrors).toHaveBeenLastCalledWith({
    days: 7, page: 1, accountId, sourceIp: "2001:db8::2", statusCode: 503,
  }))
  expect(screen.queryByText("旧错误")).not.toBeInTheDocument()
  expect(screen.getByText("正在读取错误记录…")).toBeVisible()

  resolveFiltered(response([{ ...knownError, id: "filtered", account_name: "筛选结果" }], 21))
  await screen.findByText("筛选结果")
  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(await screen.findByText("第二页")).toBeVisible()
  expect(api.getOperationsRequestErrors).toHaveBeenLastCalledWith({
    days: 7, page: 2, accountId, sourceIp: "2001:db8::2", statusCode: 503,
  })

  await user.click(screen.getByRole("button", { name: "重置筛选" }))
  expect(screen.getByLabelText("错误范围")).toHaveValue("1")
  expect(screen.getByLabelText("账号 UUID")).toHaveValue("")
  expect(screen.getByLabelText("来源 IP")).toHaveValue("")
  expect(screen.getByLabelText("HTTP 状态码")).toHaveValue(null)
  await waitFor(() => expect(api.getOperationsRequestErrors).toHaveBeenLastCalledWith({
    days: 1, page: 1, accountId: "", sourceIp: "", statusCode: null,
  }))
})

it("hides details and purges request-error cache when the server revokes permission", async () => {
  const user = userEvent.setup()
  vi.mocked(api.getOperationsRequestErrors).mockImplementation((filters) => {
    if (filters.days === 1) return Promise.resolve(response([knownError]))
    return Promise.reject(new ProblemDetailsError({
      type: "about:blank", title: "Forbidden", status: 403,
      detail: "无权读取", code: "forbidden", request_id: "denied-request",
    }))
  })
  const { client } = renderPanel()
  await screen.findByText("张三")
  await user.selectOptions(screen.getByLabelText("错误范围"), "7")
  await user.click(screen.getByRole("button", { name: "查询错误" }))

  expect(await screen.findByText("无权读取请求错误明细，请重新登录或联系管理员。" )).toBeVisible()
  expect(screen.queryByText("张三")).not.toBeInTheDocument()
  await waitFor(() => expect(client.getQueriesData({ queryKey: api.operationsKeys.requestErrorsRoot() }).every(([, data]) => data === undefined)).toBe(true))
})

it.each(["测试退出", "测试刷新权限"])("hides and purges cached details after %s", async (action) => {
  const user = userEvent.setup()
  vi.mocked(api.getOperationsRequestErrors).mockResolvedValue(response([knownError]))
  const { client } = renderPanel({ actions: true })
  await screen.findByText("张三")

  if (action === "测试刷新权限") activeUser = { ...administrator, is_super_admin: false }
  await user.click(screen.getByRole("button", { name: action }))

  await waitFor(() => expect(screen.queryByRole("region", { name: "请求错误明细" })).not.toBeInTheDocument())
  await waitFor(() => expect(client.getQueriesData({ queryKey: api.operationsKeys.requestErrorsRoot() }).every(([, data]) => data === undefined)).toBe(true))
})
