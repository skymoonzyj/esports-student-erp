import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { MailSettingsPage } from "./MailSettingsPage"

afterEach(() => vi.unstubAllGlobals())

function renderPage() {
  return render(
    <MemoryRouter>
      <QueryClientProvider
        client={new QueryClient({
          defaultOptions: { queries: { retry: false } },
        })}
      >
        <MailSettingsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

it("loads masked settings, saves a replacement authorization code, and tests the connection", async () => {
  const requests: Array<{ url: string; method: string; body?: string }> = []
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      const method = init?.method ?? "GET"
      requests.push({ url, method, body: typeof init?.body === "string" ? init.body : undefined })
      if (url.endsWith("/mail/settings") && method === "GET") {
        return new Response(
          JSON.stringify({
            configured: true,
            enabled: true,
            smtp_host: "smtp.qq.com",
            smtp_port: 465,
            tls_mode: "implicit_tls",
            username_masked: "m***5@foxmail.com",
            sender_masked: "m***5@foxmail.com",
            from_name: "竞学 ERP",
            reply_to_masked: "m***5@foxmail.com",
            authorization_code_configured: true,
            last_test_status: "never",
            last_test_at: null,
            updated_at: "2026-08-19T10:00:00+08:00",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/mail/settings") && method === "PUT") {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>
        expect(body.authorization_code).toBe("new-code")
        expect(body.username).toBeUndefined()
        expect(body.sender).toBeUndefined()
        expect(body.reply_to).toBeUndefined()
        return new Response(
          JSON.stringify({
            configured: true,
            enabled: true,
            smtp_host: body.smtp_host,
            smtp_port: body.smtp_port,
            tls_mode: body.tls_mode,
            username_masked: "m***5@foxmail.com",
            sender_masked: "m***5@foxmail.com",
            from_name: body.from_name,
            reply_to_masked: "m***5@foxmail.com",
            authorization_code_configured: true,
            last_test_status: "never",
            last_test_at: null,
            updated_at: "2026-08-19T10:01:00+08:00",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/mail/settings/test") && method === "POST") {
        return new Response(
          JSON.stringify({ status: "passed", request_id: "mail-test-1" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByRole("heading", { name: "邮件发送设置" })).toBeVisible()
  expect(await screen.findByText("m***5@foxmail.com · 已启用")).toBeVisible()
  expect(await screen.findByPlaceholderText("已配置，留空保持不变")).toBeVisible()
  expect(screen.queryByText("new-code")).not.toBeInTheDocument()

  await user.type(screen.getByLabelText("SMTP 授权码"), "new-code")
  await user.click(screen.getByRole("button", { name: "保存配置" }))
  expect(await screen.findByText("邮件发送配置已保存")).toBeVisible()

  await user.click(screen.getByRole("button", { name: "测试连接" }))
  expect(await screen.findByText("SMTP 连接测试通过")).toBeVisible()
  await waitFor(() => {
    expect(requests.some((request) => request.url.endsWith("/mail/settings/test"))).toBe(true)
  })
})

it("requires credentials on the first save and never renders the authorization code", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/mail/settings") && !init?.method) {
      return new Response(
        JSON.stringify({
          configured: false,
          enabled: false,
          smtp_host: null,
          smtp_port: null,
          tls_mode: null,
          username_masked: null,
          sender_masked: null,
          from_name: null,
          reply_to_masked: null,
          authorization_code_configured: false,
          last_test_status: "never",
          last_test_at: null,
          updated_at: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)

  renderPage()
  expect(await screen.findByText("尚未配置发送账号")).toBeVisible()
  expect(await screen.findByLabelText("SMTP 用户名")).toBeRequired()
  expect(await screen.findByLabelText("发件地址")).toBeRequired()
  expect(await screen.findByLabelText("回复地址")).toBeRequired()
  expect(await screen.findByLabelText("SMTP 授权码")).toBeRequired()
})

