import { apiFetch } from "../api/client"
import { validateAppearance } from "./store"
import type { Appearance } from "./store"

export async function getSystemAppearance(signal?: AbortSignal): Promise<Appearance> {
  return validateAppearance(await apiFetch<unknown>("/operations/appearance", { signal }))
}

export async function saveSystemAppearance(theme: Appearance["theme"], expectedRevision: number): Promise<Appearance> {
  return validateAppearance(await apiFetch<unknown>("/operations/appearance", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ theme, expected_revision: expectedRevision }),
  }))
}
