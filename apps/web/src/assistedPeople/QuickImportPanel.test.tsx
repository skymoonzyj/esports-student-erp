import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { QuickImportPanel } from "./QuickImportPanel"

const person = "10000000-0000-4000-8000-000000000001"
const stay = "10000000-0000-4000-8000-000000000002"
const team = "10000000-0000-4000-8000-000000000003"
const text = "4-101\t测试人员\t\t助教\t\t合成证件\t合成手机号"
const row = { row_number: 1, full_name: "测试人员", room_label: "4-101", arrival_date: "2026-11-07", departure_date: null,
  identity_label: "助教", project_label: "", team_options: [], selected_team_id: null, needs_team_choice: false,
  birth_date: "1990-01-01", sex: "male", masked_identity: "110***********0015", masked_phone: "138****0721", errors: [], warnings: ["队伍待划分"], ticket: "encrypted-ticket" }
let writes: Array<{ url: string; body: Record<string, unknown> }>
let previewRows: unknown[]
let rejectFirst: boolean
beforeEach(() => {
  setCsrfToken("test-csrf")
  writes = []; previewRows = [row]; rejectFirst = false
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>
    writes.push({ url, body })
    if (url === "/api/v1/personnel-intake/quick-import/preview") return Response.json({ import_date: "2026-11-07", rows: previewRows })
    if (url !== "/api/v1/personnel-intake/quick-import/commit") return Response.json({ status: 404, detail: "Route not found", code: "route_not_found" }, { status: 404 })
    if (rejectFirst) { rejectFirst = false; throw new TypeError("network lost") }
    return Response.json({ person_id: person, stay_id: stay, stay_status: "checked_in", role_pending: true }, { status: 201 })
  }))
})
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })
async function recognize() {
  fireEvent.change(screen.getByLabelText("粘贴人员表格"), { target: { value: text } })
  fireEvent.click(screen.getByRole("button", { name: "识别并预览" }))
  await screen.findByText("测试人员")
}
it("previews without writing and requires confirmation while invalid rows stay deferred", async () => {
  previewRows = [row, { ...row, row_number: 2, full_name: "待核实人员", errors: ["房间待核对"], ticket: null }]
  render(<QuickImportPanel accountId="account-a" enabled revalidating={false} />)
  await recognize()
  expect(writes.filter(item => item.url.endsWith("/commit"))).toHaveLength(0)
  const submit = screen.getByRole("button", { name: "确认建档并安排入住" })
  expect(submit).toBeDisabled()
  fireEvent.click(screen.getByRole("checkbox", { name: /已核对预览/ }))
  fireEvent.click(submit)
  await screen.findByText(/已建档并入住/)
  expect(writes.filter(item => item.url.endsWith("/commit"))).toHaveLength(1)
  expect(screen.getByText("房间待核对")).toBeInTheDocument()
  expect(screen.getByText(/角色需超管另行批准/)).toBeInTheDocument()
})
it("retains the exact ticket and operation for uncertain network retry", async () => {
  render(<QuickImportPanel accountId="account-a" enabled revalidating={false} />)
  await recognize(); rejectFirst = true
  fireEvent.click(screen.getByRole("checkbox", { name: /已核对预览/ }))
  fireEvent.click(screen.getByRole("button", { name: "确认建档并安排入住" }))
  await screen.findByText(/结果尚未确认/)
  fireEvent.click(screen.getByRole("button", { name: "重试未完成行" }))
  await screen.findByText(/已建档并入住/)
  const commits = writes.filter(item => item.url.endsWith("/commit"))
  expect(commits).toHaveLength(2)
  expect(commits[0].body).toEqual(commits[1].body)
})
it("selecting an ambiguous team gets a fresh preview and never auto-commits", async () => {
  previewRows = [{ ...row, needs_team_choice: true, ticket: null, errors: ["请选择队伍"], team_options: [
    { id: team, project_id: person, project_name: "测试项目", name: "测试队" },
  ] }]
  render(<QuickImportPanel accountId="account-a" enabled revalidating={false} />)
  await recognize()
  previewRows = [{ ...row, selected_team_id: team }]
  fireEvent.change(screen.getByLabelText("第 1 行队伍"), { target: { value: team } })
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1].body.team_choices).toEqual({ "1": team })
  expect(writes[1].body.default_arrival_date).toBe("2026-11-07")
  expect(writes.some(item => item.url.endsWith("/commit"))).toBe(false)
})
it("account switch clears drafts and ignores a late preview", async () => {
  let resolve!: (value: Response) => void
  const pending = new Promise<Response>(done => { resolve = done })
  vi.stubGlobal("fetch", vi.fn(() => pending))
  const view = render(<QuickImportPanel accountId="account-a" enabled revalidating={false} />)
  fireEvent.change(screen.getByLabelText("粘贴人员表格"), { target: { value: text } })
  fireEvent.click(screen.getByRole("button", { name: "识别并预览" }))
  view.rerender(<QuickImportPanel accountId="account-b" enabled revalidating={false} />)
  await act(async () => { resolve(Response.json({ import_date: "2026-11-07", rows: [row] })) })
  expect(screen.getByLabelText("粘贴人员表格")).toHaveValue("")
  expect(screen.queryByText("测试人员")).not.toBeInTheDocument()
})
it("losing authority removes preview and source rather than only disabling submit", async () => {
  const view = render(<QuickImportPanel accountId="account-a" enabled revalidating={false} />)
  await recognize()
  view.rerender(<QuickImportPanel accountId="account-a" enabled={false} revalidating={false} />)
  expect(screen.queryByText("测试人员")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("粘贴人员表格")).not.toBeInTheDocument()
  view.rerender(<QuickImportPanel accountId="account-a" enabled revalidating={false} />)
  expect(screen.getByLabelText("粘贴人员表格")).toHaveValue("")
})

it("server permission loss clears the batch and stops later rows", async () => {
  previewRows = [row, { ...row, row_number: 2, full_name: "另一个隔离人员" }]
  render(<QuickImportPanel accountId="account-a" enabled revalidating={false} />)
  await recognize()
  const commits: unknown[] = []
  vi.stubGlobal("fetch", vi.fn(async (_input, init) => {
    commits.push(init?.body)
    return Response.json({ status: 403, detail: "无权限", code: "permission_denied" }, { status: 403 })
  }))
  fireEvent.click(screen.getByRole("checkbox", { name: /已核对预览/ }))
  fireEvent.click(screen.getByRole("button", { name: "确认建档并安排入住" }))
  await screen.findByText(/导入草稿已清除/)
  expect(screen.queryByText("测试人员")).not.toBeInTheDocument()
  expect(screen.queryByText("另一个隔离人员")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("粘贴人员表格")).not.toBeInTheDocument()
  expect(commits).toHaveLength(1)
})

it("focus changes preserve preview without repeating requests", async () => {
  render(<QuickImportPanel accountId="account-a" enabled revalidating={false} />)
  await recognize()
  fireEvent.blur(window); fireEvent.focus(window)
  expect(screen.getByLabelText("粘贴人员表格")).toHaveValue(text)
  expect(screen.getByText("测试人员")).toBeInTheDocument()
  expect(writes).toHaveLength(1)
})
