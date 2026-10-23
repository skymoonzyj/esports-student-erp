import { apiFetch } from "../api/client"
import type { HousingResidentOption, HousingStay } from "../housing/api"

export type PersonHousingStay = Pick<HousingStay,
  "stay_purpose" | "duration_mode" | "planned_check_in_at" | "planned_check_out_at" | "actual_check_in_at" | "status"
> & { building_name: string; floor_name: string; room_name: string }

export type PersonHousing = {
  access_revision: string
  housing_access_revision: string
  current: PersonHousingStay | null
  planned: PersonHousingStay | null
  management_stay: HousingStay | null
  allowed_actions: { create_stay: boolean }
}

export type PersonHousingRoomOption = {
  id: string
  room_number: string
  building_name: string
  floor_name: string
  allowed_actions: { create_stay: boolean }
}

export type PersonHousingCheckInOptions = {
  access_revision: string
  housing_access_revision: string
  items: PersonHousingRoomOption[]
  resident: HousingResidentOption
}

export const personHousingKeys = {
  root: (accountId: string) => ["person-housing", accountId] as const,
  summary: (accountId: string, personId: string) => [...personHousingKeys.root(accountId), personId] as const,
  options: (accountId: string, personId: string, revision: string) => [...personHousingKeys.summary(accountId, personId), "check-in-options", revision] as const,
}

export function getPersonHousing(personId: string, signal?: AbortSignal): Promise<PersonHousing> {
  return apiFetch<PersonHousing>(`/people/${personId}/housing`, { signal })
}

export function getPersonHousingCheckInOptions(personId: string, signal?: AbortSignal): Promise<PersonHousingCheckInOptions> {
  return apiFetch<PersonHousingCheckInOptions>(`/people/${personId}/housing/check-in-options`, { signal })
}
