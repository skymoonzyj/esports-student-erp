import { focusManager, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { HousingRoomCardsPanel } from "./HousingRoomCardsPanel"

const auth = vi.hoisted(() => ({ user: { account_id: "account-a", permissions: ["housing.read"], scoped_permissions: [], is_super_admin: false } }))
vi.mock("../auth/AuthProvider", () => ({ useAuth: () => auth }))
const clients: QueryClient[] = []
const item = { stay_id: "stay-a", person_id: "person-a", person_name: "住客甲", nickname: "小甲", issued_on: "2026-11-01", expires_on: "2026-11-08", version: 3, can_edit: true }
const second = { ...item, stay_id: "stay-b", person_id: "person-b", person_name: "住客乙", nickname: null, issued_on: null, expires_on: null, version: 0, can_edit: false }
let saveStatus = 200
let current = item
let writes: unknown[] = []
let reads = 0
let readStatus = 200
let revision = "revision-a"
beforeEach(() => {
  auth.user = { account_id: "account-a", permissions: ["housing.read"], scoped_permissions: [], is_super_admin: false }
  saveStatus = 200; readStatus = 200; revision = "revision-a"; current = item; writes = []; reads = 0; setCsrfToken("test")
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (String(url).endsWith("/housing/catalog")) return Response.json({ access_revision: revision })
    if (init?.method === "PUT") {
      writes.push(JSON.parse(String(init.body)))
      if (saveStatus !== 200) return Response.json({ detail: "保存失败", code: "conflict" }, { status: saveStatus })
      return Response.json({ ...item, ...JSON.parse(String(init.body)), version: 4 })
    }
    reads++
    if (readStatus !== 200) return Response.json({}, { status: readStatus })
    return Response.json({ items: [current, second] })
  }))
})
afterEach(() => { clients.forEach((client) => client.clear()); clients.length = 0; vi.unstubAllGlobals(); setCsrfToken(null); focusManager.setFocused(undefined) })
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client)
  return render(<QueryClientProvider client={client}><HousingRoomCardsPanel roomId="room-a" /></QueryClientProvider>)
}

it("shows per-resident card dates, and edits only the authorized resident with an expected version", async () => {
  mount()
  await screen.findByText("住客甲")
  expect(screen.getByText("住客乙")).toBeVisible()
  expect(screen.getByText("尚未录入房卡日期")).toBeVisible()
  expect(screen.queryByRole("button", { name: "保存住客乙的房卡日期" })).not.toBeInTheDocument()
  expect(screen.getByLabelText("住客甲开卡日")).toHaveAttribute("max", "9999-12-31")
  fireEvent.change(screen.getByLabelText("住客甲到期日"), { target: { value: "2026-11-10" } })
  fireEvent.click(screen.getByRole("button", { name: "保存住客甲的房卡日期" }))
  await screen.findByText("房卡日期已保存")
  expect(writes).toEqual([{ issued_on: "2026-11-01", expires_on: "2026-11-10", expected_version: 3 }])
})

it("rejects incomplete, backwards and five-digit date years before writing", async () => {
  mount(); await screen.findByText("住客甲")
  const expires = screen.getByLabelText("住客甲到期日")
  for (const value of ["", "2026-10-31", "10000-11-01"]) {
    fireEvent.change(expires, { target: { value } })
    fireEvent.click(screen.getByRole("button", { name: "保存住客甲的房卡日期" }))
    expect(await screen.findByRole("alert")).toHaveTextContent(/日期|四位/)
  }
  expect(writes).toEqual([])
})

it("refetches conflicts but preserves the draft and blocks silent overwrites until explicit verification", async () => {
  saveStatus = 409; mount(); await screen.findByText("住客甲")
  fireEvent.change(screen.getByLabelText("住客甲到期日"), { target: { value: "2026-11-10" } })
  current = { ...item, expires_on: "2026-11-12", version: 4 }
  fireEvent.click(screen.getByRole("button", { name: "保存住客甲的房卡日期" }))
  await screen.findByRole("button", { name: "已核对住客甲的最新日期，允许重新提交" })
  await waitFor(() => expect(reads).toBeGreaterThan(1))
  expect(screen.getByLabelText("住客甲到期日")).toHaveValue("2026-11-10")
  expect(screen.getByText(/2026-11-12/)).toBeVisible()
  expect(screen.getByRole("button", { name: "保存住客甲的房卡日期" })).toBeDisabled()
  saveStatus = 200
  fireEvent.click(screen.getByRole("button", { name: "已核对住客甲的最新日期，允许重新提交" }))
  fireEvent.click(screen.getByRole("button", { name: "保存住客甲的房卡日期" }))
  await screen.findByText("房卡日期已保存")
  expect(writes).toEqual([
    { issued_on: "2026-11-01", expires_on: "2026-11-10", expected_version: 3 },
    { issued_on: "2026-11-01", expires_on: "2026-11-10", expected_version: 4 },
  ])
})

it("keeps failed-save inputs and clears previous identity data during account changes", async () => {
  saveStatus = 500; const view = mount(); await screen.findByText("住客甲")
  fireEvent.change(screen.getByLabelText("住客甲到期日"), { target: { value: "2026-11-11" } })
  fireEvent.click(screen.getByRole("button", { name: "保存住客甲的房卡日期" }))
  await screen.findByRole("alert")
  expect(screen.getByLabelText("住客甲到期日")).toHaveValue("2026-11-11")
  auth.user = { ...auth.user, account_id: "account-b" }
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})))
  await act(async () => { view.rerender(<QueryClientProvider client={clients[0]}><HousingRoomCardsPanel roomId="room-a" /></QueryClientProvider>) })
  expect(screen.queryByText("住客甲")).not.toBeInTheDocument()
})

it("retains a conflict draft if rereading fails and cannot approve an unverified version", async () => {
  saveStatus = 409; mount(); await screen.findByText("住客甲")
  fireEvent.change(screen.getByLabelText("住客甲到期日"), { target: { value: "2026-11-13" } })
  readStatus = 500
  fireEvent.click(screen.getByRole("button", { name: "保存住客甲的房卡日期" }))
  await waitFor(() => expect(reads).toBe(2))
  await waitFor(() => expect(screen.getByLabelText("住客甲到期日")).toHaveValue("2026-11-13"))
  expect(screen.getByRole("button", { name: "已核对住客甲的最新日期，允许重新提交" })).toBeDisabled()
  expect(writes).toHaveLength(1)
})

it("does not expose cached resident rows when the same account changes authorization", async () => {
  const view = mount(); await screen.findByText("住客甲")
  auth.user = { ...auth.user, permissions: ["housing.read", "housing.keycards.manage"] }
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})))
  view.rerender(<QueryClientProvider client={clients[0]}><HousingRoomCardsPanel roomId="room-a" /></QueryClientProvider>)
  expect(screen.queryByText("住客甲")).not.toBeInTheDocument()
})

it("joins the parent catalog observer without an extra mount-time catalog fetch", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client)
  client.setQueryData(["housing", "account-a", "catalog"], { access_revision: "parent-revision" })
  render(<QueryClientProvider client={client}><HousingRoomCardsPanel roomId="room-a" /></QueryClientProvider>)
  await screen.findByText("住客甲")
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/housing/catalog"))).toBe(false)
})

it("clears both dates deliberately, but removes all resident data after access revocation", async () => {
  mount(); await screen.findByText("住客甲")
  fireEvent.change(screen.getByLabelText("住客甲开卡日"), { target: { value: "" } })
  fireEvent.change(screen.getByLabelText("住客甲到期日"), { target: { value: "" } })
  fireEvent.click(screen.getByRole("button", { name: "保存住客甲的房卡日期" }))
  await screen.findByText("房卡日期已保存")
  expect(writes).toEqual([{ issued_on: null, expires_on: null, expected_version: 3 }])
  saveStatus = 403
  fireEvent.click(screen.getByRole("button", { name: "保存住客甲的房卡日期" }))
  await waitFor(() => expect(screen.queryByText("住客甲")).not.toBeInTheDocument())
  expect(screen.queryByLabelText("住客甲开卡日")).not.toBeInTheDocument()
  expect(screen.getByRole("alert")).toBeVisible()
})

it.each([500, 409])("retains failed-save %s drafts and conflict acknowledgement across a same-revision focus recheck", async (status) => {
  saveStatus = status; mount(); await screen.findByText("住客甲")
  fireEvent.change(screen.getByLabelText("住客甲到期日"), { target: { value: "2026-11-17" } })
  fireEvent.click(screen.getByRole("button", { name: "保存住客甲的房卡日期" }))
  await screen.findByRole("alert")
  if (status === 409) await waitFor(() => expect(reads).toBe(2))
  let finish!: (response: Response) => void
  const base = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith("/housing/catalog") ? new Promise((resolve) => { finish = resolve }) : base(url, init))
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(finish).toBeDefined())
  await waitFor(() => expect(screen.queryByText("住客甲")).not.toBeInTheDocument())
  expect(screen.queryByLabelText("住客甲到期日")).not.toBeInTheDocument()
  await act(async () => finish(Response.json({ access_revision: "revision-a" })))
  expect(await screen.findByLabelText("住客甲到期日")).toHaveValue("2026-11-17")
  if (status === 409) {
    expect(screen.getByRole("button", { name: "保存住客甲的房卡日期" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "已核对住客甲的最新日期，允许重新提交" })).toBeVisible()
  } else expect(screen.getByRole("alert")).toHaveTextContent("已保留输入")
})

it("discards drafts when a successful focus recheck changes the access revision", async () => {
  saveStatus = 500; mount(); await screen.findByText("住客甲")
  fireEvent.change(screen.getByLabelText("住客甲到期日"), { target: { value: "2026-11-17" } })
  fireEvent.click(screen.getByRole("button", { name: "保存住客甲的房卡日期" }))
  await screen.findByRole("alert")
  revision = "revision-new"
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(reads).toBe(2))
  await waitFor(() => expect(screen.getByLabelText("住客甲到期日")).toHaveValue("2026-11-08"))
})

it("discards drafts after a denied focus check, even if access later returns at the same revision", async () => {
  saveStatus = 500; mount(); await screen.findByText("住客甲")
  fireEvent.change(screen.getByLabelText("住客甲到期日"), { target: { value: "2026-11-17" } })
  fireEvent.click(screen.getByRole("button", { name: "保存住客甲的房卡日期" }))
  await screen.findByRole("alert")
  const base = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith("/housing/catalog") ? Promise.resolve(Response.json({}, { status: 403 })) : base(url, init))
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(screen.queryByText("住客甲")).not.toBeInTheDocument())
  vi.mocked(fetch).mockImplementation(base)
  fireEvent.click(screen.getByRole("button", { name: "重新加载房卡信息" }))
  expect(await screen.findByLabelText("住客甲到期日")).toHaveValue("2026-11-08")
})
