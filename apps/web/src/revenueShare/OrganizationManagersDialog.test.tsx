import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode } from "react"
import { afterEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { OrganizationManagersDialog } from "./OrganizationManagersDialog"

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrfToken(null) })
const organization = { id: "org-a", name: "邀请机构甲", is_active: true }
const candidate = { account_id: "10000000-0000-4000-8000-000000000001", full_name: "同名人员", nickname: "昵称甲" }
const manager = { ...candidate, organization_id: "org-a", granted_at: "2026-10-10T18:00:00Z" }
const json = (value: unknown) => new Response(JSON.stringify(value))
function setup(active = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const props = { accountId: "actor-a", organization: { ...organization, is_active: active }, onClose: vi.fn() }
  const view = render(<QueryClientProvider client={client}><OrganizationManagersDialog {...props} /></QueryClientProvider>)
  return { ...view, client, props, change: (accountId: string, id: string) => view.rerender(<QueryClientProvider client={client}><OrganizationManagersDialog {...props} accountId={accountId} organization={{ ...organization, id, name: "邀请机构乙" }} /></QueryClientProvider>) }
}

it("loads held initial queries under StrictMode and can add then remove through the real dialog", async () => {
  const existing = { ...manager, account_id: "existing-account", full_name: "已有负责人" }
  let assignments = [existing]
  let holdInitialReads = true
  const heldReads: Array<() => void> = []
  let abortedReads = 0
  const writes: string[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "PUT") {
      writes.push("PUT")
      assignments = [existing, manager]
      return json(manager)
    }
    if (init?.method === "DELETE") {
      writes.push("DELETE")
      assignments = [existing]
      return new Response(null, { status: 204 })
    }
    const response = () => {
      init?.signal?.throwIfAborted()
      return String(url).includes("candidates")
        ? json({ items: [candidate], total: 1, page: 1, page_size: 20 })
        : json(assignments)
    }
    if (!holdInitialReads) return response()
    return new Promise<Response>((resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        abortedReads++
        reject(new DOMException("Aborted", "AbortError"))
      }, { once: true })
      heldReads.push(() => {
        if (init?.signal?.aborted) reject(new DOMException("Aborted", "AbortError"))
        else resolve(response())
      })
    })
  }))
  vi.spyOn(window, "confirm").mockReturnValue(true)
  setCsrfToken("csrf")
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: 30000, retry: false, refetchOnWindowFocus: false }, mutations: { retry: false } } })
  render(<StrictMode><QueryClientProvider client={client}><OrganizationManagersDialog accountId="actor-a" organization={organization} onClose={vi.fn()} /></QueryClientProvider></StrictMode>)
  await waitFor(() => expect(heldReads.length).toBeGreaterThanOrEqual(2))
  await act(async () => {
    holdInitialReads = false
    heldReads.forEach((release) => release())
  })
  const selectableAccount = await screen.findByRole("radio", { name: new RegExp(candidate.account_id) })
  expect(abortedReads).toBeGreaterThan(0)
  expect(await screen.findByText("已有负责人")).toBeVisible()
  const user = userEvent.setup()
  await user.click(selectableAccount)
  await user.click(screen.getByRole("button", { name: "添加负责人" }))
  expect(await screen.findByText("负责人已添加")).toBeVisible()
  await user.click(screen.getByRole("button", { name: new RegExp(`移除负责人：.*${candidate.account_id}`) }))
  expect(await screen.findByText("负责人已移除")).toBeVisible()
  expect(screen.queryByRole("button", { name: new RegExp(`移除负责人：.*${candidate.account_id}`) })).not.toBeInTheDocument()
  expect(writes).toEqual(["PUT", "DELETE"])
})
it("selects account UUIDs, displays no contacts and confirms before granting without recalculation", async () => {
  let added = false
  const requests: string[] = []
  const second = { ...candidate, account_id: "10000000-0000-4000-8000-000000000002", nickname: null, phone: "13800138000", email: "private@example.com", identity_number: "private-identity" }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    requests.push(String(url))
    if (init?.method === "PUT") { added = true; return json({ ...manager, account_id: second.account_id }) }
    return String(url).includes("candidates") ? json({ items: [candidate, second], total: 2, page: 1, page_size: 20 }) : json(added ? [{ ...manager, account_id: second.account_id }] : [])
  }))
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
  setCsrfToken("csrf")
  const { client } = setup()
  const invalidation = vi.spyOn(client, "invalidateQueries")
  const user = userEvent.setup()
  await user.click(await screen.findByRole("radio", { name: new RegExp(second.account_id) }))
  await user.click(screen.getByRole("button", { name: "添加负责人" }))
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("邀请机构甲"))
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("同名人员"))
  expect(await screen.findByText("负责人已添加")).toBeVisible()
  expect(invalidation).toHaveBeenCalledExactlyOnceWith({ queryKey: ["revenue-share", "actor-a", "organization-managers", "org-a"], exact: true }, { throwOnError: true })
  expect(requests.some((url) => url.endsWith(`/managers/${second.account_id}`))).toBe(true)
  expect(requests.some((url) => /reconcile|finance|configuration/.test(url))).toBe(false)
  expect(client.getQueryCache().getAll().map((query) => query.queryKey)).toContainEqual(["revenue-share", "actor-a", "organization-managers", "org-a"])
  expect(document.body.textContent).not.toMatch(/手机号|邮箱/)
  expect(document.body.textContent).not.toMatch(/13800138000|private@example.com|private-identity/)
  expect(screen.getByText("2026-10-11")).toBeVisible()
})
it("disabled organizations retain named removal confirmation and block duplicate removal", async () => {
  let finish!: (response: Response) => void
  const pending = new Promise<Response>((resolve) => { finish = resolve })
  let writes = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_url, init) => {
    if (init?.method === "DELETE") { writes++; return pending }
    return json([manager])
  }))
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
  setCsrfToken("csrf")
  setup(false)
  const user = userEvent.setup()
  const remove = await screen.findByRole("button", { name: /移除负责人/ })
  await user.dblClick(remove)
  expect(remove).toBeDisabled()
  expect(writes).toBe(1)
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("邀请机构甲"))
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining(candidate.account_id))
  expect(screen.queryByRole("radio")).not.toBeInTheDocument()
  await act(async () => finish(new Response(null, { status: 204 })))
  expect(await screen.findByText("负责人已移除")).toBeVisible()
})
it("clears candidates on a failed search instead of retaining the old selection", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url) => {
    if (!String(url).includes("candidates")) return json([])
    if (String(url).includes("q=fail")) return new Response(JSON.stringify({ detail: "候选账号加载失败", code: "failed" }), { status: 503 })
    return json({ items: [candidate], total: 1, page: 1, page_size: 20 })
  }))
  setup()
  await screen.findByRole("radio")
  await userEvent.setup().type(screen.getByLabelText("查找负责人账号"), "fail")
  expect(await screen.findByRole("alert")).toHaveTextContent("候选账号加载失败")
  expect(screen.queryByRole("radio")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "添加负责人" })).toBeDisabled()
})
it.each([["actor-b", "org-b"], ["actor-a", "org-b"], ["actor-b", "org-a"]])("isolates late candidates on switching to account %s / organization %s", async (accountId, organizationId) => {
  let finish!: (response: Response) => void
  let oldSignal: AbortSignal | undefined
  let count = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (!String(url).includes("candidates")) return json([])
    if (++count === 1) { oldSignal = init?.signal as AbortSignal; return new Promise<Response>((resolve) => { finish = resolve }) }
    return json({ items: [], total: 0, page: 1, page_size: 20 })
  }))
  const view = setup()
  await waitFor(() => expect(count).toBe(1))
  view.change(accountId, organizationId)
  await waitFor(() => expect(count).toBe(2))
  await act(async () => finish(json({ items: [candidate], total: 1, page: 1, page_size: 20 })))
  expect(oldSignal?.aborted).toBe(true)
  expect(screen.queryByText(candidate.full_name)).not.toBeInTheDocument()
  expect(screen.getByRole("dialog", { name: "邀请机构乙 · 负责人" })).toBeVisible()
})
it.each([["actor-b", "org-b"], ["actor-a", "org-b"], ["actor-b", "org-a"]])("rejects late saves when switching to account %s / organization %s", async (accountId, organizationId) => {
  let finish!: (response: Response) => void
  let writeSignal: AbortSignal | undefined
  let writes = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "PUT") { writes++; writeSignal = init.signal as AbortSignal; return new Promise<Response>((resolve) => { finish = resolve }) }
    return String(url).includes("candidates") ? json({ items: [candidate], total: 1, page: 1, page_size: 20 }) : json([])
  }))
  vi.spyOn(window, "confirm").mockReturnValue(true)
  setCsrfToken("csrf")
  const view = setup()
  const invalidation = vi.spyOn(view.client, "invalidateQueries")
  const user = userEvent.setup()
  await user.click(await screen.findByRole("radio"))
  await user.dblClick(screen.getByRole("button", { name: "添加负责人" }))
  expect(writes).toBe(1)
  expect(screen.getByRole("button", { name: "添加负责人" })).toBeDisabled()
  view.change(accountId, organizationId)
  await screen.findByRole("radio")
  await act(async () => finish(json(manager)))
  expect(writeSignal?.aborted).toBe(true)
  expect(screen.queryByText("负责人已添加")).not.toBeInTheDocument()
  expect(invalidation).not.toHaveBeenCalled()
  expect(within(screen.getByRole("dialog")).getByRole("button", { name: "添加负责人" })).toBeDisabled()
})

it.each(["PUT", "DELETE"])("suppresses a success notice when %s succeeds but the manager refresh fails", async (method) => {
  let completedWrite = false
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === method) {
      completedWrite = true
      return method === "DELETE" ? new Response(null, { status: 204 }) : json(manager)
    }
    if (String(url).includes("candidates")) return json({ items: [candidate], total: 1, page: 1, page_size: 20 })
    if (completedWrite) return new Response(JSON.stringify({ detail: "负责人刷新失败", code: "refresh_failed" }), { status: 503 })
    return json(method === "DELETE" ? [manager] : [])
  }))
  vi.spyOn(window, "confirm").mockReturnValue(true)
  setCsrfToken("csrf")
  setup()
  const user = userEvent.setup()
  if (method === "PUT") {
    await user.click(await screen.findByRole("radio"))
    await user.click(screen.getByRole("button", { name: "添加负责人" }))
  } else {
    await user.click(await screen.findByRole("button", { name: /移除负责人/ }))
  }
  await waitFor(() => expect(screen.getAllByRole("alert").some((alert) => alert.textContent?.includes("负责人刷新失败"))).toBe(true))
  expect(completedWrite).toBe(true)
  expect(screen.queryByText("负责人已添加")).not.toBeInTheDocument()
  expect(screen.queryByText("负责人已移除")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /移除负责人/ })).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "添加负责人" })).toBeDisabled()
})

it("does not send changes when the named confirmation is cancelled", async () => {
  const requests: string[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method) requests.push(init.method)
    return String(url).includes("candidates") ? json({ items: [candidate], total: 1, page: 1, page_size: 20 }) : json([])
  }))
  vi.spyOn(window, "confirm").mockReturnValue(false)
  setup()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("radio"))
  await user.click(screen.getByRole("button", { name: "添加负责人" }))
  expect(requests).toEqual([])
  expect(screen.queryByText("负责人已添加")).not.toBeInTheDocument()
})

it("uses isolated paginated candidate queries and resets page/selection when searching", async () => {
  const urls: string[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url) => {
    urls.push(String(url))
    if (!String(url).includes("candidates")) return json([])
    const page = String(url).includes("page=2") ? 2 : 1
    return json({ items: [{ ...candidate, account_id: page === 2 ? "page-two-account" : candidate.account_id }], total: 21, page, page_size: 20 })
  }))
  const view = setup()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("radio"))
  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(await screen.findByRole("radio", { name: /page-two-account/ })).toBeVisible()
  expect(screen.getByRole("button", { name: "添加负责人" })).toBeDisabled()
  await user.type(screen.getByLabelText("查找负责人账号"), "新")
  await screen.findByRole("radio", { name: new RegExp(candidate.account_id) })
  expect(urls.some((url) => url.endsWith("candidates?q=%E6%96%B0&page=1&page_size=20"))).toBe(true)
  expect(view.client.getQueryCache().getAll().map((query) => query.queryKey)).toContainEqual(["revenue-share", "actor-a", "organization-manager-candidates", "新", 1, 20])
})
