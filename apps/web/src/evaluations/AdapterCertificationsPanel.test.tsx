import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"
import { AdapterCertificationsPanel } from "./AdapterCertificationsPanel"
import { EvaluationAuthorizationProvider } from "./EvaluationAuthorization"

const accountId = "10000000-0000-4000-8000-000000000001"
const projectId = "70000000-0000-4000-8000-000000000001"
const certificationId = "90000000-0000-4000-8000-000000000001"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId } }),
}))

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function certification(id = certificationId) {
  return {
    id,
    project_id: projectId,
    adapter_id: "lol",
    adapter_version: "1.0.0",
    scoring_rule_version: "rule-1",
    result_schema_version: "schema-1",
    manifest_digest: "a".repeat(64),
    certified_by_user_id: "91000000-0000-4000-8000-000000000001",
    certified_at: "2026-08-08T12:00:00Z",
  }
}

function renderPanel() {
  let created = false
  const fetchMock = vi.fn<typeof fetch>(async (request, init) => {
    const url = String(request)
    if (url.includes("/evaluations/admin/adapter-certification-projects?")) {
      return jsonResponse({
        items: [{ id: projectId, name: "英雄联盟", is_active: true }],
        total: 1,
        page: 1,
        page_size: 100,
      })
    }
    if (url.endsWith("/evaluations/admin/adapter-certifications")) {
      if (init?.method === "POST") {
        created = true
        return jsonResponse({
          api_version: "v2",
          certification: certification("90000000-0000-4000-8000-000000000002"),
        }, 201)
      }
      return jsonResponse({
        api_version: "v2",
        items: created
          ? [certification("90000000-0000-4000-8000-000000000002"), certification()]
          : [certification()],
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <EvaluationAuthorizationProvider pending={false} revision={1}>
        <AdapterCertificationsPanel idempotencyKeyFactory={() => "cert-key-1"} />
      </EvaluationAuthorizationProvider>
    </QueryClientProvider>,
  )
  return { fetchMock, queryClient }
}

afterEach(() => {
  setCsrfToken(null)
  vi.unstubAllGlobals()
})

it("lists certified adapter versions without exposing raw manifests", async () => {
  const { fetchMock } = renderPanel()

  const panel = await screen.findByRole("region", { name: "适配器认证" })
  const item = within(panel).getByText("aaaaaaaaaaaa…aaaaaaaa").closest("article")
  expect(item).toHaveTextContent("英雄联盟")
  expect(item).toHaveTextContent("lol · 1.0.0")
  expect(item).toHaveTextContent("rule-1 · schema-1")
  expect(JSON.stringify(document.body.textContent)).not.toContain("raw_manifest")
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).endsWith(
        "/evaluations/admin/adapter-certification-projects?query=&page=1&page_size=100",
      ),
    ),
  ).toBe(true)
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).includes("/evaluations/admin/projects?"),
    ),
  ).toBe(false)
})

it("creates a certification with one idempotency key and refreshes the list", async () => {
  const user = userEvent.setup()
  const view = renderPanel()
  const panel = await screen.findByRole("region", { name: "适配器认证" })

  await user.selectOptions(within(panel).getByLabelText("电竞项目"), projectId)
  await user.type(within(panel).getByLabelText("适配器 ID"), "lol")
  await user.type(within(panel).getByLabelText("适配器版本"), "1.0.0")
  await user.type(within(panel).getByLabelText("评分规则版本"), "rule-1")
  await user.type(within(panel).getByLabelText("结果模式版本"), "schema-1")
  await user.type(within(panel).getByLabelText("清单摘要"), "b".repeat(64))
  await user.click(within(panel).getByRole("button", { name: "新增认证" }))

  expect(await within(panel).findByRole("status")).toHaveTextContent("适配器认证已登记")
  expect(within(panel).getByLabelText("清单摘要")).toHaveValue("")
  const createCall = view.fetchMock.mock.calls.find(
    ([input, init]) =>
      String(input).endsWith("/evaluations/admin/adapter-certifications") &&
      init?.method === "POST",
  )
  expect(createCall).toBeDefined()
  const [, init] = createCall ?? []
  expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("cert-key-1")
  expect(JSON.parse(String(init?.body))).toEqual({
    project_id: projectId,
    adapter_id: "lol",
    adapter_version: "1.0.0",
    scoring_rule_version: "rule-1",
    result_schema_version: "schema-1",
    manifest_digest: "b".repeat(64),
  })
  expect(
    view.fetchMock.mock.calls.filter(([input]) =>
      String(input).endsWith("/evaluations/admin/adapter-certifications"),
    ).length,
  ).toBeGreaterThanOrEqual(3)
})
