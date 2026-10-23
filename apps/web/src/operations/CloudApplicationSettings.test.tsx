import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"
import * as api from "./cloudAuthorizationApi"
import { CloudApplicationSettings } from "./CloudApplicationSettings"

vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => ({ status: "authenticated", user: { account_id: "owner", is_super_admin: true, permissions: ["operations.monitor.read"] } }) }))
const app: api.CloudAppView = { provider: "baidu", client_id: "app-id", secret_present: false, revision: 1, configured: false, prerequisites: ["cloud_app_unconfigured"] }
beforeEach(() => { vi.spyOn(api, "getCloudApplication").mockResolvedValue(app); vi.spyOn(api, "saveCloudApplication").mockResolvedValue({ ...app, secret_present: true, configured: true, prerequisites: [] }) })

it("identifies Baidu client_id as AppKey / API Key rather than a numeric AppID", async () => {
  render(<CloudApplicationSettings provider="baidu" onSaved={vi.fn()} />)
  expect(await screen.findByLabelText("AppKey / API Key")).toHaveValue("app-id")
  expect(screen.getByLabelText("Secret Key / AppSecret")).toHaveAttribute("type", "password")
  expect(screen.getByText(/不是数字 AppID/)).toBeVisible()
  expect(screen.queryByLabelText("AppID / Client ID")).not.toBeInTheDocument()
})
it("requires a Baidu secret for a new application, writes it once and clears the password", async () => {
  render(<CloudApplicationSettings provider="baidu" onSaved={vi.fn()} />)
  const save = await screen.findByRole("button", { name: "保存自有应用" })
  expect(save).toBeDisabled()
  const user = userEvent.setup()
  await user.type(screen.getByLabelText("Secret Key / AppSecret"), "one-time-secret")
  expect(save).toBeEnabled()
  await user.click(save)
  await waitFor(() => expect(screen.getByLabelText("Secret Key / AppSecret")).toHaveValue(""))
  expect(api.saveCloudApplication).toHaveBeenCalledWith("baidu", { client_id: "app-id", client_secret: "one-time-secret", clear_secret: false, expected_revision: 1 }, expect.any(AbortSignal))
})
it("does not require or display AppSecret for 115 PKCE", async () => {
  vi.mocked(api.getCloudApplication).mockResolvedValue({ ...app, provider: "115", configured: true, prerequisites: [] })
  render(<CloudApplicationSettings provider="115" onSaved={vi.fn()} />)
  expect(await screen.findByRole("button", { name: "保存自有应用" })).toBeEnabled()
  expect(screen.getByLabelText("AppID / Client ID")).toHaveValue("app-id")
  expect(screen.queryByLabelText(/AppSecret|Secret Key/)).not.toBeInTheDocument()
  expect(screen.queryByText(/不是数字 AppID/)).not.toBeInTheDocument()
  expect(screen.getByText(/PKCE.*不需要/)).toBeVisible()
})
it("preserves the existing secret when blank and only clears it with explicit confirmation", async () => {
  vi.mocked(api.getCloudApplication).mockResolvedValue({ ...app, secret_present: true, configured: true, prerequisites: [] })
  render(<CloudApplicationSettings provider="baidu" onSaved={vi.fn()} />)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "保存自有应用" }))
  expect(api.saveCloudApplication).toHaveBeenLastCalledWith("baidu", { client_id: "app-id", clear_secret: false, expected_revision: 1 }, expect.any(AbortSignal))
  await user.click(screen.getByRole("checkbox", { name: /明确清除/ }))
  await user.click(screen.getByRole("button", { name: "保存自有应用" }))
  expect(api.saveCloudApplication).toHaveBeenLastCalledWith("baidu", { client_id: "app-id", clear_secret: true, expected_revision: 1 }, expect.any(AbortSignal))
})
