import { apiFetch } from "../api/client"
import type { Asset } from "./api"
import type { RentalPeriod } from "./rentalDates"

export type RentalRenewInput = {
  operation_key: string
  period: RentalPeriod
  assets: { asset_id: string; expected_rental_revision: number }[]
}
export type RentalRenewResult = {
  operation_key: string
  period: RentalPeriod
  replayed: boolean
  items: { asset_id: string; previous_expires_on: string; rental_expires_on: string; rental_revision: number }[]
}
export function renewAssetRentals(input: RentalRenewInput): Promise<RentalRenewResult> {
  return apiFetch<RentalRenewResult>("/assets/rentals/renew", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) })
}
export type RentalRenewalPage = {
  items: { id: string; period: RentalPeriod; previous_expires_on: string; rental_expires_on: string; created_at: string; actor_name: string | null }[]
  total: number
  page: number
  page_size: number
}
export function listRentalRenewals(assetId: string, page = 1): Promise<RentalRenewalPage> {
  return apiFetch<RentalRenewalPage>(`/assets/${assetId}/rental-renewals?page=${page}&page_size=20`)
}
export function returnAssetRental(assetId: string, input: { returned_on: string; expected_rental_revision: number }): Promise<Asset> {
  return apiFetch<Asset>(`/assets/${assetId}/rental-return`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) })
}
