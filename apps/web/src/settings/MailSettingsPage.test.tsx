import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"
import { MailSettingsPage } from "./MailSettingsPage"

const deliverySettings = {
  configured: true,
  enabled: true,
  smtp_host: "smtp.example.com",
  smtp_port: 465,
  tls_mode: "implicit_tls" as const,
  username_masked: "a***n@example.com",
  sender_masked: "a***n@example.com",
  from_name: "竞学 ERP",
  reply_to_masked: "a***n@example.com",
  authorization_code_configured: true,
  last_test_status: "passed" as const,
  last_test_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:00:00Z",
}

const template = {
  event_key: "verification_code" as const,
  event_name: "邮箱验证码",
  description: "注册、密码重置、监护人邀请和添加登录邮箱时发送。",
  subject_template: "{{site_name}} {{operation_name}}验证码",
  html_template: "<main><strong>{{verification_code}}</strong></main>",
  placeholders: [
    { token: "{{site_name}}", label: "站点名称", example: "竞学 ERP" },
    { token: "{{verification_code}}", label: "验证码", example: "123456" },
  ],
  is_default: true,
  updated_at: null,
}

const passwordResetTemplate = {
  ...template,
  event_key: "password_reset" as const,
  event_name: "密码重置验证码",
  description: "重置账号密码时发送。",
  subject_template: "{{site_name}} 密码重置验证码",
  html_template: "<main><strong>{{verification_code}}</strong><p>密码重置</p></main>",
}

afterEach(() => {
  vi.unstubAllGlobals()
  setCsrfToken(null)
})

it("previews an administrator-edited verification template in a sandboxed frame", async () => {
  setCsrfToken("test-csrf")
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/mail/settings") && !init?.method) {
      return new Response(JSON.stringify(deliverySettings), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/mail/settings/templates/verification-code") && !init?.method) {
      return new Response(JSON.stringify(template), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (
      url.endsWith("/mail/settings/templates/verification-code/preview") &&
      init?.method === "POST"
    ) {
      expect(JSON.parse(String(init.body))).toEqual({
        subject_template: "竞学通知 {{verification_code}}",
        html_template: template.html_template,
      })
      return new Response(
        JSON.stringify({
          subject: "竞学通知 123456",
          html: "<main><strong>123456</strong></main>",
          plain_text: "验证码：123456",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)

  render(
    <QueryClientProvider client={new QueryClient()}>
      <MailSettingsPage />
    </QueryClientProvider>,
  )

  const subject = await screen.findByLabelText("主题")
  fireEvent.change(subject, {
    target: { value: "竞学通知 {{verification_code}}" },
  })
  fireEvent.click(screen.getByRole("button", { name: "预览 / 刷新" }))

  const frame = await screen.findByTitle("邮件模板预览")
  await waitFor(() => {
    expect(frame).toHaveAttribute("sandbox", "")
    expect(frame).toHaveAttribute("srcdoc", "<main><strong>123456</strong></main>")
  })
  expect(screen.getByText("竞学通知 123456")).toBeVisible()
})

it("allows switching to the dedicated password-reset template", async () => {
  setCsrfToken("test-csrf")
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/mail/settings") && !init?.method) {
      return new Response(JSON.stringify(deliverySettings), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/mail/settings/templates/verification-code") && !init?.method) {
      return new Response(JSON.stringify(template), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/mail/settings/templates/password-reset") && !init?.method) {
      return new Response(JSON.stringify(passwordResetTemplate), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)

  render(
    <QueryClientProvider client={new QueryClient()}>
      <MailSettingsPage />
    </QueryClientProvider>,
  )

  const eventSelect = await screen.findByLabelText("事件")
  expect(eventSelect).not.toBeDisabled()
  fireEvent.change(eventSelect, { target: { value: "password_reset" } })

  expect(await screen.findByDisplayValue("{{site_name}} 密码重置验证码")).toBeVisible()
  expect(screen.getByText("重置账号密码时发送。")).toBeVisible()
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/mail/settings/templates/password-reset",
    expect.anything(),
  )
})
