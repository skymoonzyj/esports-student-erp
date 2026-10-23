import { z } from "zod"

import { apiFetch, apiFetchResponse } from "../api/client"

const INVALID_RESPONSE_MESSAGE = "餐饮偏好响应格式无效，请刷新重试。"
const XLSX_MIME =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
const ZIP_MIME = "application/zip"
const SAFE_ATTACHMENT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

const UUID = z.uuid()
const ACCESS_REVISION = z.string().regex(/^[0-9a-f]{64}$/)
const AWARE_DATE_TIME = z.iso.datetime({ offset: true })
const NON_NEGATIVE_INTEGER = z.number().int().min(0)
const POSITIVE_INTEGER = z.number().int().min(1)
const RATIO = z.number().finite().min(0).max(1)

const populationSchema = z.enum(["students", "all_people"])
const exportFormatSchema = z.enum(["xlsx", "csv_zip"])

const cuisineSelectionInputSchema = z
  .object({
    cuisine_option_id: UUID,
    custom_name: z.string().min(1).max(40).nullable().optional(),
  })
  .strict()

export const foodPreferenceDraftSchema = z
  .object({
    cuisines: z.array(cuisineSelectionInputSchema).max(3),
    dishes: z.array(z.string().min(1).max(40)).max(3),
  })
  .strict()

const foodPreferenceWriteInputSchema = z
  .object({
    expected_version: NON_NEGATIVE_INTEGER,
    cuisines: z.array(cuisineSelectionInputSchema).max(3),
    dishes: z.array(z.string().min(1).max(40)).max(3),
  })
  .strict()

const foodPreferenceExportInputSchema = z
  .object({
    population: populationSchema,
    include_inactive: z.boolean(),
    format: exportFormatSchema,
    purpose: z.string().min(4).max(200),
  })
  .strict()

const foodPreferenceStatisticsFiltersSchema = z
  .object({
    population: populationSchema,
    include_inactive: z.boolean(),
    page: POSITIVE_INTEGER,
    page_size: POSITIVE_INTEGER.max(100),
  })
  .strict()

const cuisineCreateInputSchema = z
  .object({
    name: z.string().min(1).max(40),
    sort_order: NON_NEGATIVE_INTEGER,
  })
  .strict()

const cuisinePatchInputSchema = z
  .object({
    name: z.string().min(1).max(40).optional(),
    sort_order: NON_NEGATIVE_INTEGER.optional(),
    is_active: z.boolean().optional(),
  })
  .strict()

const cuisineOptionSchema = z
  .object({
    id: UUID,
    name: z.string(),
    sort_order: NON_NEGATIVE_INTEGER,
    is_active: z.boolean(),
    is_other: z.boolean(),
  })
  .strict()

const cuisineCatalogSchema = z
  .object({
    items: z.array(cuisineOptionSchema),
    allowed_actions: z.array(z.enum(["create", "update", "disable"])),
    access_revision: ACCESS_REVISION,
  })
  .strict()

const foodPreferenceCuisineSchema = z
  .object({
    cuisine_option_id: UUID,
    name: z.string(),
    custom_name: z.string().nullable(),
  })
  .strict()

const foodPreferenceProfileSchema = z
  .object({
    person_id: UUID,
    version: NON_NEGATIVE_INTEGER,
    cuisines: z.array(foodPreferenceCuisineSchema),
    dishes: z.array(z.string()),
    updated_at: AWARE_DATE_TIME.nullable(),
    allowed_actions: z.array(z.literal("update")),
    access_revision: ACCESS_REVISION,
  })
  .strict()

const foodPreferenceSelfResponseSchema = z
  .object({
    profile: foodPreferenceProfileSchema,
    onboarding_required: z.boolean(),
    onboarding_completed_at: AWARE_DATE_TIME.nullable(),
  })
  .strict()

const dishSuggestionSchema = z.object({ name: z.string() }).strict()

const dishSuggestionPageSchema = z
  .object({
    items: z.array(dishSuggestionSchema),
    access_revision: ACCESS_REVISION,
  })
  .strict()

const foodPreferenceRankSchema = z
  .object({
    label: z.string(),
    count: NON_NEGATIVE_INTEGER,
    ratio: RATIO,
  })
  .strict()

const foodPreferencePersonSchema = z
  .object({
    person_id: UUID,
    full_name: z.string(),
    person_types: z.array(z.enum(["student", "guardian", "staff", "visitor"])),
    status: z.enum(["active", "draft", "inactive", "deleted"]),
    cuisines: z.array(z.string()),
    dishes: z.array(z.string()),
    updated_at: AWARE_DATE_TIME.nullable(),
  })
  .strict()

const foodPreferencePersonPageSchema = z
  .object({
    items: z.array(foodPreferencePersonSchema),
    page: POSITIVE_INTEGER,
    page_size: POSITIVE_INTEGER.max(100),
    total: NON_NEGATIVE_INTEGER,
  })
  .strict()

const foodPreferenceStatisticsSchema = z
  .object({
    population: populationSchema,
    include_inactive: z.boolean(),
    total_people: NON_NEGATIVE_INTEGER,
    filled_people: NON_NEGATIVE_INTEGER,
    unfilled_people: NON_NEGATIVE_INTEGER,
    filled_ratio: RATIO,
    cuisines: z.array(foodPreferenceRankSchema),
    dishes: z.array(foodPreferenceRankSchema),
    people: foodPreferencePersonPageSchema,
    access_revision: ACCESS_REVISION,
  })
  .strict()

export type FoodPopulation = z.infer<typeof populationSchema>
export type FoodExportFormat = z.infer<typeof exportFormatSchema>
export type CuisineSelectionInput = z.infer<typeof cuisineSelectionInputSchema>
export type FoodPreferenceDraft = z.infer<typeof foodPreferenceDraftSchema>
export type FoodPreferenceWriteInput = z.infer<
  typeof foodPreferenceWriteInputSchema
>
export type FoodPreferenceExportInput = z.infer<
  typeof foodPreferenceExportInputSchema
>
export type FoodPreferenceStatisticsFilters = z.infer<
  typeof foodPreferenceStatisticsFiltersSchema
>
export type CuisineCreateInput = z.infer<typeof cuisineCreateInputSchema>
export type CuisinePatchInput = z.infer<typeof cuisinePatchInputSchema>
export type CuisineOption = z.infer<typeof cuisineOptionSchema>
export type CuisineCatalog = z.infer<typeof cuisineCatalogSchema>
export type FoodPreferenceCuisine = z.infer<
  typeof foodPreferenceCuisineSchema
>
export type FoodPreferenceProfile = z.infer<typeof foodPreferenceProfileSchema>
export type FoodPreferenceSelfResponse = z.infer<
  typeof foodPreferenceSelfResponseSchema
>
export type DishSuggestion = z.infer<typeof dishSuggestionSchema>
export type DishSuggestionPage = z.infer<typeof dishSuggestionPageSchema>
export type FoodPreferenceRank = z.infer<typeof foodPreferenceRankSchema>
export type FoodPreferencePerson = z.infer<typeof foodPreferencePersonSchema>
export type FoodPreferencePersonPage = z.infer<
  typeof foodPreferencePersonPageSchema
>
export type FoodPreferenceStatistics = z.infer<
  typeof foodPreferenceStatisticsSchema
>
export type FoodPreferenceExportDownload = {
  blob: Blob
  filename: string
}

function invalidResponse(): Error {
  return new Error(INVALID_RESPONSE_MESSAGE)
}

function parseInput<T>(schema: z.ZodType<T>, value: unknown): T {
  const parsed = schema.safeParse(value)
  if (!parsed.success) throw invalidResponse()
  return parsed.data
}

async function fetchDecoded<T>(
  path: string,
  schema: z.ZodType<T>,
  init: RequestInit = {},
): Promise<T> {
  let raw: unknown
  try {
    raw = await apiFetch<unknown>(path, init)
  } catch (error) {
    if (error instanceof SyntaxError) throw invalidResponse()
    throw error
  }
  return parseInput(schema, raw)
}

function jsonWriteInit(body: unknown, signal?: AbortSignal): RequestInit {
  return {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  }
}

export function getFoodPreferenceCatalog(
  includeInactive = false,
  signal?: AbortSignal,
): Promise<CuisineCatalog> {
  const query = new URLSearchParams({
    include_inactive: String(includeInactive),
  })
  return fetchDecoded(
    `/food-preferences/cuisines?${query.toString()}`,
    cuisineCatalogSchema,
    { signal },
  )
}

export async function createFoodCuisine(
  input: CuisineCreateInput,
  signal?: AbortSignal,
): Promise<CuisineOption> {
  const body = parseInput(cuisineCreateInputSchema, input)
  return fetchDecoded(
    "/food-preferences/cuisines",
    cuisineOptionSchema,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    },
  )
}

export async function patchFoodCuisine(
  cuisineId: string,
  input: CuisinePatchInput,
  signal?: AbortSignal,
): Promise<CuisineOption> {
  const body = parseInput(cuisinePatchInputSchema, input)
  return fetchDecoded(
    `/food-preferences/cuisines/${encodeURIComponent(cuisineId)}`,
    cuisineOptionSchema,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    },
  )
}

export function getFoodPreferenceProfile(
  personId: string,
  signal?: AbortSignal,
): Promise<FoodPreferenceProfile> {
  return fetchDecoded(
    `/people/${encodeURIComponent(personId)}/food-preferences`,
    foodPreferenceProfileSchema,
    { signal },
  )
}

export async function updateFoodPreferenceProfile(
  personId: string,
  input: FoodPreferenceWriteInput,
  signal?: AbortSignal,
): Promise<FoodPreferenceProfile> {
  const body = parseInput(foodPreferenceWriteInputSchema, input)
  return fetchDecoded(
    `/people/${encodeURIComponent(personId)}/food-preferences`,
    foodPreferenceProfileSchema,
    jsonWriteInit(body, signal),
  )
}

export function getMyFoodPreferences(
  signal?: AbortSignal,
): Promise<FoodPreferenceSelfResponse> {
  return fetchDecoded(
    "/me/food-preferences",
    foodPreferenceSelfResponseSchema,
    { signal },
  )
}

export async function updateMyFoodPreferences(
  input: FoodPreferenceWriteInput,
  signal?: AbortSignal,
): Promise<FoodPreferenceSelfResponse> {
  const body = parseInput(foodPreferenceWriteInputSchema, input)
  return fetchDecoded(
    "/me/food-preferences",
    foodPreferenceSelfResponseSchema,
    jsonWriteInit(body, signal),
  )
}

export function getDishSuggestions(
  queryValue: string,
  limit = 10,
  signal?: AbortSignal,
): Promise<DishSuggestionPage> {
  const query = new URLSearchParams({
    query: queryValue,
    limit: String(limit),
  })
  return fetchDecoded(
    `/food-preferences/dish-suggestions?${query.toString()}`,
    dishSuggestionPageSchema,
    { signal },
  )
}

export async function getFoodPreferenceStatistics(
  input: FoodPreferenceStatisticsFilters,
  signal?: AbortSignal,
): Promise<FoodPreferenceStatistics> {
  const filters = parseInput(foodPreferenceStatisticsFiltersSchema, input)
  const query = new URLSearchParams({
    population: filters.population,
    include_inactive: String(filters.include_inactive),
    page: String(filters.page),
    page_size: String(filters.page_size),
  })
  return fetchDecoded(
    `/food-preferences/statistics?${query.toString()}`,
    foodPreferenceStatisticsSchema,
    { signal },
  )
}

function unquoteDispositionValue(value: string): string | null {
  if (!value.startsWith('"')) return value
  if (value.length < 2 || !value.endsWith('"')) return null
  const inner = value.slice(1, -1)
  return inner.includes('"') ? null : inner
}

function attachmentFilename(disposition: string): string | null {
  const parts = disposition.split(";").map((part) => part.trim())
  if (parts.shift()?.toLowerCase() !== "attachment") return null

  let plain: string | null = null
  let encoded: string | null = null
  let plainSeen = false
  let encodedSeen = false
  for (const part of parts) {
    const separator = part.indexOf("=")
    if (separator < 1) continue
    const name = part.slice(0, separator).trim().toLowerCase()
    const rawValue = part.slice(separator + 1).trim()
    if (name === "filename") {
      if (plainSeen) return null
      plainSeen = true
      plain = unquoteDispositionValue(rawValue)
      if (plain === null) return null
    } else if (name === "filename*") {
      if (encodedSeen) return null
      encodedSeen = true
      const match = /^UTF-8''(.+)$/i.exec(rawValue)
      if (!match) return null
      try {
        encoded = decodeURIComponent(match[1])
      } catch {
        return null
      }
    }
  }

  if (plain !== null && encoded !== null && plain !== encoded) return null
  return encoded ?? plain
}

function validatedAttachmentFilename(
  response: Response,
  format: FoodExportFormat,
): string {
  const expected =
    format === "xlsx"
      ? { mime: XLSX_MIME, extension: ".xlsx" }
      : { mime: ZIP_MIME, extension: ".zip" }
  if (response.headers.get("Content-Type") !== expected.mime) {
    throw invalidResponse()
  }
  const disposition = response.headers.get("Content-Disposition")
  const filename = disposition ? attachmentFilename(disposition) : null
  if (
    filename === null ||
    !SAFE_ATTACHMENT_NAME.test(filename) ||
    !filename.endsWith(expected.extension)
  ) {
    throw invalidResponse()
  }
  return filename
}

export async function exportFoodPreferences(
  input: FoodPreferenceExportInput,
  signal?: AbortSignal,
): Promise<FoodPreferenceExportDownload> {
  const body = parseInput(foodPreferenceExportInputSchema, input)
  const response = await apiFetchResponse("/food-preferences/exports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  })
  const filename = validatedAttachmentFilename(response, body.format)
  try {
    return { blob: await response.blob(), filename }
  } catch {
    throw invalidResponse()
  }
}
