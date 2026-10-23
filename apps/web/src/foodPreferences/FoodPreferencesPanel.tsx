import {
  type QueryKey,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useLocation } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import {
  getDishSuggestions,
  getFoodPreferenceCatalog,
  getFoodPreferenceProfile,
  getMyFoodPreferences,
  updateFoodPreferenceProfile,
  updateMyFoodPreferences,
  type CuisineCatalog,
  type CuisineOption,
  type FoodPreferenceDraft,
  type FoodPreferenceProfile,
  type FoodPreferenceSelfResponse,
} from "./api"
import {
  foodPreferenceKeys,
  purgePreviousFoodPreferenceRevisions,
} from "./keys"
import { normalizeFoodTextKey } from "./casefold"
import "./food-preferences.css"

export type FoodPreferencesPanelProps =
  | {
      mode: "profile"
      personId: string
      onAccessRevoked?: () => void
      onCommitted?: (value: FoodPreferenceProfile) => void
    }
  | {
      mode: "self"
      intent: "profile" | "onboarding"
      onSkipped?: () => void
      onAccessRevoked?: () => void
      onCommitted?: (value: FoodPreferenceSelfResponse) => void
    }
  | {
      mode: "draft"
      personId: string
      required: true
      value: FoodPreferenceDraft
      cuisineNameSnapshots?: Readonly<Record<string, string>>
      onChange: (value: FoodPreferenceDraft) => void
    }

type EditableCuisine = {
  cuisine_option_id: string
  custom_name: string | null
  name: string
  isOther: boolean
}

type EditableDraft = {
  cuisines: EditableCuisine[]
  dishes: string[]
}

type DetailResponse = FoodPreferenceProfile | FoodPreferenceSelfResponse

type AcceptedScope = {
  identity: string
  revision: string
}

type PendingSubmission = {
  draft: EditableDraft
  epoch: number
  identity: string
  revision: string
}

const EMPTY_DRAFT: EditableDraft = { cuisines: [], dishes: [] }
const MAX_SELECTIONS = 3
const MAX_TEXT_LENGTH = 40
const SUGGESTION_LIMIT = 10
const CONFLICT_MESSAGE = "资料已被更新，请确认后重试"

function cleanDisplayText(value: string): string {
  return value.normalize("NFKC").trim().replace(/\s+/gu, " ")
}

function normalizedText(value: string): string {
  return normalizeFoodTextKey(value)
}

function cloneDraft(value: EditableDraft): EditableDraft {
  return {
    cuisines: value.cuisines.map((item) => ({ ...item })),
    dishes: [...value.dishes],
  }
}

function profileDraft(profile: FoodPreferenceProfile): EditableDraft {
  return {
    cuisines: profile.cuisines.map((item) => ({
      cuisine_option_id: item.cuisine_option_id,
      custom_name: item.custom_name,
      name: item.name,
      isOther: item.custom_name !== null,
    })),
    dishes: [...profile.dishes],
  }
}

function controlledDraft(
  value: FoodPreferenceDraft,
  catalogValue: CuisineCatalog,
  cuisineNameSnapshots: Readonly<Record<string, string>> = {},
): EditableDraft {
  return {
    cuisines: value.cuisines.map((item, index) => {
      const option = catalogValue.items.find(
        (candidate) => candidate.id === item.cuisine_option_id,
      )
      const fallbackName =
        cleanDisplayText(cuisineNameSnapshots[item.cuisine_option_id] ?? "") ||
        cleanDisplayText(item.custom_name ?? "") ||
        `已选菜系 ${index + 1}`
      return {
        cuisine_option_id: item.cuisine_option_id,
        custom_name: item.custom_name ?? null,
        name: option?.name ?? fallbackName,
        isOther: option?.is_other ?? item.custom_name != null,
      }
    }),
    dishes: [...value.dishes],
  }
}

function publicDraft(value: EditableDraft): FoodPreferenceDraft {
  return {
    cuisines: value.cuisines.map((item) => ({
      cuisine_option_id: item.cuisine_option_id,
      custom_name: item.isOther
        ? cleanDisplayText(item.custom_name ?? "")
        : null,
    })),
    dishes: value.dishes.map(cleanDisplayText),
  }
}

function cuisineLabel(item: EditableCuisine): string {
  const customName = cleanDisplayText(item.custom_name ?? "")
  return item.isOther && customName ? customName : item.name
}

function cuisineValue(
  item: EditableCuisine,
  catalog?: CuisineCatalog,
): string {
  if (item.isOther) return item.custom_name ?? ""
  const currentOption = catalog?.items.find(
    (candidate) =>
      candidate.id === item.cuisine_option_id && candidate.is_active,
  )
  return currentOption?.name ?? item.name
}

function cuisineKey(item: EditableCuisine, catalog?: CuisineCatalog): string {
  return normalizedText(cuisineValue(item, catalog))
}

function draftIsValid(
  value: EditableDraft,
  required: boolean,
  catalog?: CuisineCatalog,
): boolean {
  if (
    value.cuisines.length > MAX_SELECTIONS ||
    value.dishes.length > MAX_SELECTIONS ||
    (required &&
      (value.cuisines.length === 0 || value.dishes.length === 0))
  ) {
    return false
  }

  const cuisineValues = value.cuisines.map((item) =>
    cuisineValue(item, catalog),
  )
  const cuisineKeys = cuisineValues.map(normalizedText)
  if (
    cuisineValues.some((item) => {
      const display = cleanDisplayText(item)
      return !display || [...display].length > MAX_TEXT_LENGTH
    }) ||
    new Set(cuisineKeys).size !== cuisineKeys.length
  ) {
    return false
  }

  const dishDisplays = value.dishes.map(cleanDisplayText)
  const dishKeys = value.dishes.map(normalizedText)
  return (
    dishDisplays.every(
      (display) => display && [...display].length <= MAX_TEXT_LENGTH,
    ) &&
    new Set(dishKeys).size === dishKeys.length
  )
}

function detailProfile(value: DetailResponse): FoodPreferenceProfile {
  return "profile" in value ? value.profile : value
}

function isPrivateFailure(error: unknown): error is ProblemDetailsError {
  return (
    error instanceof ProblemDetailsError &&
    (error.problem.status === 403 || error.problem.status === 404)
  )
}

function safeError(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function identityFor(
  props: FoodPreferencesPanelProps,
  accountId: string,
  routeIdentity: string,
): string {
  const subject =
    props.mode === "self"
      ? `self:${props.intent}`
      : `${props.mode}:${props.personId}`
  return `${accountId}|${subject}|${routeIdentity}`
}

type PreferenceEditorProps = {
  catalog: CuisineCatalog
  value: EditableDraft
  editable: boolean
  disabled: boolean
  required: boolean
  cuisineInput: string
  dishInput: string
  suggestions: string[]
  selectionError: string | null
  onCuisineInput: (value: string) => void
  onDishInput: (value: string) => void
  onChange: (value: EditableDraft) => void
  onSelectionError: (value: string | null) => void
}

function PreferenceEditor({
  catalog,
  value,
  editable,
  disabled,
  required,
  cuisineInput,
  dishInput,
  suggestions,
  selectionError,
  onCuisineInput,
  onDishInput,
  onChange,
  onSelectionError,
}: PreferenceEditorProps) {
  const sortedOptions = useMemo(
    () =>
      [...catalog.items]
        .filter((item) => item.is_active)
        .sort(
          (left, right) =>
            left.sort_order - right.sort_order ||
            left.name.localeCompare(right.name) ||
            left.id.localeCompare(right.id),
        ),
    [catalog.items],
  )
  const otherOption = sortedOptions.find((option) => option.is_other)
  const fullCuisines = value.cuisines.length >= MAX_SELECTIONS
  const fullDishes = value.dishes.length >= MAX_SELECTIONS
  const valid = draftIsValid(value, required, catalog)

  const addCuisine = (option: CuisineOption) => {
    if (disabled || fullCuisines) return
    if (
      value.cuisines.some(
        (item) => cuisineKey(item, catalog) === normalizedText(option.name),
      )
    ) {
      onSelectionError("不能添加重复菜系。")
      return
    }
    onSelectionError(null)
    onChange({
      ...value,
      cuisines: [
        ...value.cuisines,
        {
          cuisine_option_id: option.id,
          custom_name: option.is_other ? "" : null,
          name: option.name,
          isOther: option.is_other,
        },
      ],
    })
  }

  const addCustomCuisine = (candidate: string) => {
    if (disabled || fullCuisines || !otherOption) return
    const displayName = cleanDisplayText(candidate)
    if (!displayName) return
    if (
      value.cuisines.some(
        (item) => cuisineKey(item, catalog) === normalizedText(displayName),
      )
    ) {
      onSelectionError("不能添加重复菜系。")
      return
    }
    onSelectionError(null)
    onChange({
      ...value,
      cuisines: [
        ...value.cuisines,
        {
          cuisine_option_id: otherOption.id,
          custom_name: displayName,
          name: otherOption.name,
          isOther: true,
        },
      ],
    })
    onCuisineInput("")
  }

  const removeCuisine = (index: number) => {
    if (disabled) return
    onSelectionError(null)
    onChange({
      ...value,
      cuisines: value.cuisines.filter((_, itemIndex) => itemIndex !== index),
    })
  }

  const addDish = (candidate: string) => {
    if (disabled || fullDishes) return
    const displayName = cleanDisplayText(candidate)
    if (!displayName) return
    if (
      value.dishes.some(
        (item) => normalizedText(item) === normalizedText(displayName),
      )
    ) {
      onSelectionError("不能添加重复菜品。")
      return
    }
    onSelectionError(null)
    onChange({ ...value, dishes: [...value.dishes, displayName] })
    onDishInput("")
  }

  const removeDish = (index: number) => {
    if (disabled) return
    onSelectionError(null)
    onChange({
      ...value,
      dishes: value.dishes.filter((_, itemIndex) => itemIndex !== index),
    })
  }

  const removeOnKeyboard = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    remove: () => void,
  ) => {
    if (event.key !== "Backspace" && event.key !== "Delete") return
    event.preventDefault()
    remove()
  }

  return (
    <section className="foodPreferencesEditor" aria-label="餐饮偏好编辑器">
      <div className="foodPreferencesEditor__grid">
        <fieldset className="foodPreferencesEditor__group">
          <legend>喜欢的菜系</legend>
          <p className="foodPreferencesEditor__counter" role="status" aria-live="polite">
            菜系 {Math.min(value.cuisines.length, MAX_SELECTIONS)}/3
          </p>
          <ol className="foodPreferencesEditor__chips" aria-label="已选菜系">
            {value.cuisines.map((item, index) => {
              const label = cuisineLabel(item)
              return (
                <li className="foodPreferencesEditor__chip" key={`${item.cuisine_option_id}-${index}`}>
                  <span>{label}</span>
                  {editable ? (
                    <button
                      type="button"
                      aria-label={`移除菜系 ${label}`}
                      disabled={disabled}
                      onClick={() => removeCuisine(index)}
                      onKeyDown={(event) =>
                        removeOnKeyboard(event, () => removeCuisine(index))
                      }
                    >
                      ×
                    </button>
                  ) : null}
                </li>
              )
            })}
          </ol>
          {editable ? (
            <>
              <div className="foodPreferencesEditor__choices" aria-label="可选菜系">
                {sortedOptions
                  .filter(
                    (option) =>
                      !option.is_other &&
                      !value.cuisines.some(
                        (item) => item.cuisine_option_id === option.id,
                      ),
                  )
                  .map((option) => (
                    <button
                      type="button"
                      key={option.id}
                      disabled={disabled || fullCuisines}
                      onClick={() => addCuisine(option)}
                    >
                      添加菜系 {option.name}
                    </button>
                  ))}
              </div>
              <div className="foodPreferencesEditor__textEntry">
                <label className="foodPreferencesEditor__field">
                  <span>菜系名称</span>
                  <input
                    type="text"
                    maxLength={MAX_TEXT_LENGTH}
                    disabled={disabled || fullCuisines || !otherOption}
                    value={cuisineInput}
                    onChange={(event) => {
                      onSelectionError(null)
                      onCuisineInput(event.currentTarget.value)
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter") return
                      event.preventDefault()
                      addCustomCuisine(cuisineInput)
                    }}
                  />
                </label>
                <button
                  type="button"
                  disabled={
                    disabled ||
                    fullCuisines ||
                    !otherOption ||
                    !cleanDisplayText(cuisineInput)
                  }
                  onClick={() => addCustomCuisine(cuisineInput)}
                >
                  添加菜系
                </button>
              </div>
            </>
          ) : null}
        </fieldset>

        <fieldset className="foodPreferencesEditor__group">
          <legend>最喜欢的菜</legend>
          <p className="foodPreferencesEditor__counter" role="status" aria-live="polite">
            菜品 {Math.min(value.dishes.length, MAX_SELECTIONS)}/3
          </p>
          <ol className="foodPreferencesEditor__chips" aria-label="已选菜品">
            {value.dishes.map((dish, index) => (
              <li className="foodPreferencesEditor__chip" key={`${normalizedText(dish)}-${index}`}>
                <span>{dish}</span>
                {editable ? (
                  <button
                    type="button"
                    aria-label={`移除菜品 ${dish}`}
                    disabled={disabled}
                    onClick={() => removeDish(index)}
                    onKeyDown={(event) =>
                      removeOnKeyboard(event, () => removeDish(index))
                    }
                  >
                    ×
                  </button>
                ) : null}
              </li>
            ))}
          </ol>
          {editable ? (
            <>
              <div className="foodPreferencesEditor__textEntry">
                <label className="foodPreferencesEditor__field">
                  <span>菜品名称</span>
                  <input
                    type="text"
                    maxLength={MAX_TEXT_LENGTH}
                    disabled={disabled || fullDishes}
                    value={dishInput}
                    onChange={(event) => {
                      onSelectionError(null)
                      onDishInput(event.currentTarget.value)
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter") return
                      event.preventDefault()
                      addDish(dishInput)
                    }}
                  />
                </label>
                <button
                  type="button"
                  disabled={
                    disabled || fullDishes || !cleanDisplayText(dishInput)
                  }
                  onClick={() => addDish(dishInput)}
                >
                  添加菜品
                </button>
              </div>
              {suggestions.length > 0 ? (
                <div className="foodPreferencesEditor__suggestions" aria-label="菜品建议">
                  {suggestions.map((suggestion) => (
                    <button
                      type="button"
                      key={suggestion}
                      disabled={disabled || fullDishes}
                      aria-label={`使用建议 ${suggestion}`}
                      onClick={() => addDish(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}
        </fieldset>
      </div>
      {required ? (
        <p className="foodPreferencesEditor__validity" aria-live="polite">
          {valid
            ? "餐饮偏好已填写完整。"
            : "请至少选择 1 个菜系和 1 个菜品。"}
        </p>
      ) : null}
      {selectionError ? (
        <p className="foodPreferencesEditor__error" role="alert">
          {selectionError}
        </p>
      ) : null}
    </section>
  )
}

export function FoodPreferencesPanel(props: FoodPreferencesPanelProps) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const location = useLocation()
  const accountId = user?.account_id ?? ""
  const routeIdentity = `${location.key}:${location.pathname}${location.search}${location.hash}`
  const identity = identityFor(props, accountId, routeIdentity)

  const [acceptedScope, setAcceptedScope] = useState<AcceptedScope | null>(null)
  const [barrierPending, setBarrierPending] = useState(true)
  const [accessRevoked, setAccessRevoked] = useState(false)
  const [editing, setEditing] = useState(false)
  const [localDraft, setLocalDraft] = useState<EditableDraft>(EMPTY_DRAFT)
  const [cuisineInput, setCuisineInput] = useState("")
  const [dishInput, setDishInput] = useState("")
  const [selectionError, setSelectionError] = useState<string | null>(null)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [conflictVersion, setConflictVersion] = useState<number | null>(null)
  const [conflictLoading, setConflictLoading] = useState(false)

  const epochRef = useRef(0)
  const acceptedScopeRef = useRef<AcceptedScope | null>(null)
  const initializedDetailRef = useRef<string | null>(null)
  const previousIdentityRef = useRef<string | null>(null)
  const revokedIdentityRef = useRef<string | null>(null)
  const notifiedRevocationRef = useRef<string | null>(null)
  const mismatchRef = useRef<string | null>(null)
  const suggestionMismatchRef = useRef<string | null>(null)
  const pendingSubmissionRef = useRef<PendingSubmission | null>(null)
  const conflictDraftRef = useRef<EditableDraft | null>(null)
  const mutationResetRef = useRef<() => void>(() => undefined)
  const privateFailureRef = useRef<(epoch: number) => void>(() => undefined)

  useEffect(
    () => () => {
      epochRef.current += 1
      pendingSubmissionRef.current = null
      conflictDraftRef.current = null
    },
    [],
  )

  const acceptedRevision =
    acceptedScope?.identity === identity ? acceptedScope.revision : null

  const catalogQuery = useQuery({
    queryKey: foodPreferenceKeys.catalog(accountId),
    queryFn: ({ signal }) => getFoodPreferenceCatalog(false, signal),
    enabled: Boolean(accountId) && !accessRevoked,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })

  const catalogMatchesAccepted = Boolean(
    acceptedRevision &&
      catalogQuery.data?.access_revision === acceptedRevision,
  )
  const barrierReady = Boolean(
    acceptedRevision &&
      catalogMatchesAccepted &&
      !catalogQuery.isFetching &&
      !catalogQuery.error &&
      !barrierPending &&
      !accessRevoked,
  )

  const detailQueryKey =
    props.mode === "profile" && acceptedRevision
      ? foodPreferenceKeys.person(
          accountId,
          acceptedRevision,
          props.personId,
        )
      : foodPreferenceKeys.self(accountId, acceptedRevision ?? "pending")

  const detailQuery = useQuery<DetailResponse>({
    queryKey: detailQueryKey,
    queryFn: ({ signal }) => {
      if (props.mode === "profile") {
        return getFoodPreferenceProfile(props.personId, signal)
      }
      if (props.mode === "self") return getMyFoodPreferences(signal)
      throw new Error("Draft mode has no profile request")
    },
    enabled: barrierReady && props.mode !== "draft",
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })

  const responseProfile = detailQuery.data
    ? detailProfile(detailQuery.data)
    : null
  const acceptedDetail =
    !isPrivateFailure(detailQuery.error) &&
    responseProfile?.access_revision === acceptedRevision
      ? detailQuery.data ?? null
      : null
  const acceptedProfile = acceptedDetail
    ? detailProfile(acceptedDetail)
    : null
  const detailKey = acceptedProfile
    ? `${identity}|${acceptedRevision}|${acceptedProfile.person_id}`
    : null

  const onboarding = props.mode === "self" && props.intent === "onboarding"
  const editorVisible = props.mode === "draft" || onboarding || editing
  const suggestionQueryValue = cleanDisplayText(dishInput)
  const suggestionQueryKey = foodPreferenceKeys.suggestions(
    accountId,
    acceptedRevision ?? "pending",
    suggestionQueryValue,
    SUGGESTION_LIMIT,
  )
  const suggestionQuery = useQuery({
    queryKey: suggestionQueryKey,
    queryFn: ({ signal }) =>
      getDishSuggestions(suggestionQueryValue, SUGGESTION_LIMIT, signal),
    enabled:
      barrierReady &&
      editorVisible &&
      Boolean(suggestionQueryValue) &&
      !conflictVersion,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })
  const suggestions =
    !isPrivateFailure(suggestionQuery.error) &&
    suggestionQuery.data?.access_revision === acceptedRevision
      ? suggestionQuery.data.items.map((item) => item.name)
      : []

  const refetchCatalog = catalogQuery.refetch
  const reauthorizeMismatch = useCallback(
    (queryKey: QueryKey) => {
      const mismatchEpoch = epochRef.current
      setBarrierPending(true)
      void (async () => {
        const filters = { queryKey, exact: true } as const
        const cancellation = queryClient.cancelQueries(filters)
        queryClient.removeQueries(filters)
        try {
          await cancellation
        } catch {
          // Cancellation is best effort; the exact stale response still must go.
        } finally {
          if (
            mismatchEpoch === epochRef.current &&
            previousIdentityRef.current === identity
          ) {
            queryClient.removeQueries(filters)
          }
        }
        if (
          mismatchEpoch !== epochRef.current ||
          previousIdentityRef.current !== identity
        ) {
          return
        }
        await refetchCatalog()
      })().catch(() => undefined)
    },
    [identity, queryClient, refetchCatalog],
  )

  const mutation = useMutation<DetailResponse, unknown, number>({
    mutationKey: [
      ...foodPreferenceKeys.accountRoot(accountId),
      "write",
      props.mode,
    ],
    gcTime: 0,
    retry: false,
    mutationFn: (expectedVersion) => {
      const pending = pendingSubmissionRef.current
      if (!pending || pending.identity !== identity) {
        return Promise.reject(new Error("Stale food preference submission"))
      }
      const input = {
        expected_version: expectedVersion,
        ...publicDraft(pending.draft),
      }
      if (props.mode === "profile") {
        return updateFoodPreferenceProfile(props.personId, input, undefined)
      }
      if (props.mode === "self") {
        return updateMyFoodPreferences(input, undefined)
      }
      return Promise.reject(new Error("Draft mode has no mutation"))
    },
    onSuccess: (value) => {
      const pending = pendingSubmissionRef.current
      if (
        !pending ||
        pending.epoch !== epochRef.current ||
        pending.identity !== identity ||
        pending.revision !== acceptedRevision
      ) {
        mutationResetRef.current()
        pendingSubmissionRef.current = null
        return
      }
      const nextProfile = detailProfile(value)
      if (nextProfile.access_revision !== acceptedRevision) {
        mutationResetRef.current()
        pendingSubmissionRef.current = null
        reauthorizeMismatch(detailQueryKey)
        return
      }

      setStatusMessage("餐饮偏好已保存。")
      setSelectionError(null)
      setEditing(false)
      setCuisineInput("")
      setDishInput("")
      setConfirmed(false)
      setConflictVersion(null)
      setConflictLoading(false)
      conflictDraftRef.current = null
      setLocalDraft(profileDraft(nextProfile))
      pendingSubmissionRef.current = null
      mutationResetRef.current()

      void (async () => {
        await queryClient.invalidateQueries({
          queryKey: foodPreferenceKeys.catalog(accountId),
        })
        await queryClient.invalidateQueries({
          queryKey:
            props.mode === "profile"
              ? foodPreferenceKeys.person(
                  accountId,
                  acceptedRevision,
                  props.personId,
                )
              : foodPreferenceKeys.self(accountId, acceptedRevision),
        })
        await queryClient.invalidateQueries({
          queryKey: [
            ...foodPreferenceKeys.revisionRoot(accountId, acceptedRevision),
            "statistics",
          ],
        })

        if (
          pending.epoch === epochRef.current &&
          pending.identity === identity
        ) {
          if (props.mode === "profile") props.onCommitted?.(nextProfile)
          if (props.mode === "self") {
            props.onCommitted?.(value as FoodPreferenceSelfResponse)
          }
        }
      })().catch(() => undefined)
    },
    onError: (error) => {
      const pending = pendingSubmissionRef.current
      if (
        !pending ||
        pending.epoch !== epochRef.current ||
        pending.identity !== identity ||
        pending.revision !== acceptedRevision
      ) {
        pendingSubmissionRef.current = null
        mutationResetRef.current()
        return
      }

      if (isPrivateFailure(error)) {
        privateFailureRef.current(pending.epoch)
        pendingSubmissionRef.current = null
        mutationResetRef.current()
        return
      }

      if (
        error instanceof ProblemDetailsError &&
        error.problem.status === 409 &&
        error.problem.code === "food_preference_version_changed"
      ) {
        const conflictEpoch = pending.epoch
        const savedDraft = cloneDraft(pending.draft)
        conflictDraftRef.current = savedDraft
        setStatusMessage(CONFLICT_MESSAGE)
        setConflictVersion(null)
        setConflictLoading(true)
        pendingSubmissionRef.current = null
        mutationResetRef.current()
        void queryClient
          .fetchQuery<DetailResponse>({
            queryKey:
              props.mode === "profile"
                ? foodPreferenceKeys.person(
                    accountId,
                    acceptedRevision,
                    props.personId,
                  )
                : foodPreferenceKeys.self(accountId, acceptedRevision),
            queryFn: ({ signal }) =>
              props.mode === "profile"
                ? getFoodPreferenceProfile(props.personId, signal)
                : getMyFoodPreferences(signal),
            staleTime: 0,
            gcTime: 0,
          })
          .then((latest) => {
            if (
              conflictEpoch !== epochRef.current ||
              identity !== pending.identity
            ) {
              return
            }
            const latestProfile = detailProfile(latest)
            if (latestProfile.access_revision !== acceptedRevision) {
              reauthorizeMismatch(detailQueryKey)
              return
            }
            setConflictVersion(latestProfile.version)
          })
          .catch((latestError: unknown) => {
            if (conflictEpoch !== epochRef.current) return
            if (isPrivateFailure(latestError)) {
              privateFailureRef.current(conflictEpoch)
            } else {
              setStatusMessage(
                safeError(
                  latestError,
                  "暂时无法读取最新资料，请稍后重试。",
                ),
              )
            }
          })
          .finally(() => {
            if (conflictEpoch === epochRef.current) setConflictLoading(false)
          })
        return
      }

      setStatusMessage(safeError(error, "餐饮偏好保存失败，请稍后重试。"))
      pendingSubmissionRef.current = null
      mutationResetRef.current()
    },
  })
  mutationResetRef.current = mutation.reset

  const clearTransient = (clearControlledDraft: boolean) => {
    setEditing(false)
    setLocalDraft(EMPTY_DRAFT)
    setCuisineInput("")
    setDishInput("")
    setSelectionError(null)
    setStatusMessage(null)
    setConfirmed(false)
    setConflictVersion(null)
    setConflictLoading(false)
    initializedDetailRef.current = null
    pendingSubmissionRef.current = null
    conflictDraftRef.current = null
    mutationResetRef.current()
    if (clearControlledDraft && props.mode === "draft") {
      props.onChange({ cuisines: [], dishes: [] })
    }
  }

  privateFailureRef.current = (failureEpoch) => {
    if (failureEpoch !== epochRef.current) return
    if (revokedIdentityRef.current === identity) return
    revokedIdentityRef.current = identity
    epochRef.current += 1
    setAccessRevoked(true)
    setAcceptedScope(null)
    acceptedScopeRef.current = null
    clearTransient(false)
    const root = foodPreferenceKeys.accountRoot(accountId)
    const cancellation = queryClient.cancelQueries({ queryKey: root })
    queryClient.removeQueries({ queryKey: root })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey: root }))
  }

  useEffect(() => {
    const previousIdentity = previousIdentityRef.current
    const changed = previousIdentity !== null && previousIdentity !== identity
    previousIdentityRef.current = identity
    epochRef.current += 1
    revokedIdentityRef.current = null
    notifiedRevocationRef.current = null
    setAccessRevoked(false)
    setBarrierPending(true)
    clearTransient(changed)
  }, [identity])

  const onAccessRevoked =
    props.mode !== "draft" ? props.onAccessRevoked : undefined

  useEffect(() => {
    if (
      !accessRevoked ||
      revokedIdentityRef.current !== identity ||
      notifiedRevocationRef.current === identity
    ) {
      return
    }
    notifiedRevocationRef.current = identity
    onAccessRevoked?.()
  }, [accessRevoked, identity, onAccessRevoked])

  useEffect(() => {
    if (!accountId || accessRevoked) return
    if (catalogQuery.isFetching) {
      setBarrierPending(true)
      return
    }
    const nextRevision = catalogQuery.data?.access_revision
    if (!nextRevision) {
      setBarrierPending(false)
      return
    }
    const current = acceptedScopeRef.current
    if (
      current?.identity === identity &&
      current.revision === nextRevision
    ) {
      setBarrierPending(false)
      return
    }

    const clearControlled = Boolean(
      current && current.revision !== nextRevision,
    )
    const barrierEpoch = epochRef.current + 1
    epochRef.current = barrierEpoch
    acceptedScopeRef.current = null
    setAcceptedScope(null)
    setBarrierPending(true)
    clearTransient(clearControlled)
    void purgePreviousFoodPreferenceRevisions(
      queryClient,
      accountId,
      nextRevision,
    ).then(() => {
      if (
        barrierEpoch !== epochRef.current ||
        previousIdentityRef.current !== identity
      ) {
        return
      }
      const nextScope = { identity, revision: nextRevision }
      acceptedScopeRef.current = nextScope
      setAcceptedScope(nextScope)
      setBarrierPending(false)
    })
  }, [
    accessRevoked,
    accountId,
    catalogQuery.data?.access_revision,
    catalogQuery.isFetching,
    identity,
    queryClient,
  ])

  useEffect(() => {
    if (!isPrivateFailure(catalogQuery.error)) return
    privateFailureRef.current(epochRef.current)
  }, [catalogQuery.error])

  useEffect(() => {
    if (!isPrivateFailure(detailQuery.error)) return
    privateFailureRef.current(epochRef.current)
  }, [detailQuery.error])

  useEffect(() => {
    if (!isPrivateFailure(suggestionQuery.error)) return
    privateFailureRef.current(epochRef.current)
  }, [suggestionQuery.error])

  useEffect(() => {
    if (
      !detailQuery.data ||
      !acceptedRevision ||
      responseProfile?.access_revision === acceptedRevision
    ) {
      mismatchRef.current = null
      return
    }
    const mismatch = `${identity}:${acceptedRevision}:${responseProfile?.access_revision}`
    if (mismatchRef.current === mismatch) return
    mismatchRef.current = mismatch
    reauthorizeMismatch(detailQueryKey)
  }, [
    acceptedRevision,
    detailQuery.data,
    detailQueryKey,
    identity,
    reauthorizeMismatch,
    responseProfile?.access_revision,
  ])

  useEffect(() => {
    const suggestionRevision = suggestionQuery.data?.access_revision
    if (
      !suggestionRevision ||
      !acceptedRevision ||
      suggestionRevision === acceptedRevision
    ) {
      suggestionMismatchRef.current = null
      return
    }
    const mismatch = `${identity}:${acceptedRevision}:${suggestionRevision}`
    if (suggestionMismatchRef.current === mismatch) return
    suggestionMismatchRef.current = mismatch
    reauthorizeMismatch(suggestionQueryKey)
  }, [
    acceptedRevision,
    identity,
    reauthorizeMismatch,
    suggestionQuery.data?.access_revision,
    suggestionQueryKey,
  ])

  useEffect(() => {
    if (!acceptedProfile || !detailKey || props.mode === "draft") return
    if (initializedDetailRef.current === detailKey) return
    initializedDetailRef.current = detailKey
    setLocalDraft(profileDraft(acceptedProfile))
    setEditing(onboarding)
    setConfirmed(false)
  }, [acceptedProfile, detailKey, onboarding, props.mode])

  const renderedDraft = useMemo(() => {
    if (!catalogQuery.data) return EMPTY_DRAFT
    if (props.mode === "draft") {
      return controlledDraft(
        props.value,
        catalogQuery.data,
        props.cuisineNameSnapshots,
      )
    }
    if (
      acceptedProfile &&
      initializedDetailRef.current !== detailKey
    ) {
      return profileDraft(acceptedProfile)
    }
    return localDraft
  }, [
    acceptedProfile,
    catalogQuery.data,
    detailKey,
    localDraft,
    props,
  ])

  const required = props.mode === "draft" || onboarding
  const canUpdate = Boolean(
    acceptedProfile?.allowed_actions.includes("update"),
  )
  const editorDisabled =
    mutation.isPending || conflictLoading || conflictVersion !== null
  const canSubmit =
    props.mode !== "draft" &&
    canUpdate &&
    draftIsValid(renderedDraft, required, catalogQuery.data) &&
    (!onboarding || confirmed) &&
    !editorDisabled

  const changeDraft = (nextValue: EditableDraft) => {
    setSelectionError(null)
    setStatusMessage(null)
    setConfirmed(false)
    if (props.mode === "draft") {
      props.onChange(publicDraft(nextValue))
    } else {
      setLocalDraft(cloneDraft(nextValue))
    }
  }

  const startEdit = () => {
    if (!canUpdate) return
    setLocalDraft(cloneDraft(renderedDraft))
    setCuisineInput("")
    setDishInput("")
    setSelectionError(null)
    setStatusMessage(null)
    setConflictVersion(null)
    setEditing(true)
  }

  const save = (draft: EditableDraft, expectedVersion: number) => {
    if (!acceptedRevision || props.mode === "draft") return
    pendingSubmissionRef.current = {
      draft: cloneDraft(draft),
      epoch: epochRef.current,
      identity,
      revision: acceptedRevision,
    }
    mutation.mutate(expectedVersion)
  }

  const retryConflict = () => {
    if (
      conflictVersion === null ||
      !conflictDraftRef.current ||
      !acceptedRevision
    ) {
      return
    }
    const sameDraft = cloneDraft(conflictDraftRef.current)
    pendingSubmissionRef.current = {
      draft: sameDraft,
      epoch: epochRef.current,
      identity,
      revision: acceptedRevision,
    }
    setConflictVersion(null)
    mutation.mutate(conflictVersion)
  }

  if (!accountId) {
    return <p className="foodPreferencesPanel__message">请先登录。</p>
  }

  if (
    !barrierReady ||
    catalogQuery.isFetching ||
    catalogQuery.data?.access_revision !== acceptedRevision
  ) {
    if (accessRevoked) return null
    if (catalogQuery.error && !isPrivateFailure(catalogQuery.error)) {
      return (
        <div className="foodPreferencesPanel foodPreferencesPanel__message">
          <p role="alert">
            {safeError(
              catalogQuery.error,
              "暂时无法确认餐饮偏好访问权限。",
            )}
          </p>
          <button
            type="button"
            disabled={catalogQuery.isFetching}
            onClick={() => void catalogQuery.refetch()}
          >
            重新确认餐饮偏好访问权限
          </button>
        </div>
      )
    }
    return (
      <p className="foodPreferencesPanel__message" aria-live="polite">
        正在确认餐饮偏好访问权限…
      </p>
    )
  }

  if (props.mode !== "draft" && !acceptedDetail) {
    if (isPrivateFailure(detailQuery.error)) return null
    if (detailQuery.error) {
      return (
        <div className="foodPreferencesPanel foodPreferencesPanel__message">
          <p role="alert">
            {safeError(detailQuery.error, "暂时无法读取餐饮偏好。")}
          </p>
          <button
            type="button"
            disabled={detailQuery.isFetching}
            onClick={() => void detailQuery.refetch()}
          >
            重新加载餐饮偏好
          </button>
        </div>
      )
    }
    return (
      <p className="foodPreferencesPanel__message" aria-live="polite">
        正在加载餐饮偏好…
      </p>
    )
  }

  if (!catalogQuery.data) return null

  return (
    <section className="foodPreferencesPanel" aria-label="餐饮偏好">
      <header className="foodPreferencesPanel__header">
        <div>
          <h2>餐饮偏好</h2>
          <p>最多选择 3 个喜欢的菜系和 3 个最喜欢的菜。</p>
        </div>
        {props.mode !== "draft" && !editorVisible && canUpdate ? (
          <button type="button" onClick={startEdit}>
            编辑餐饮偏好
          </button>
        ) : null}
      </header>

      <PreferenceEditor
        catalog={catalogQuery.data}
        value={renderedDraft}
        editable={editorVisible && (props.mode === "draft" || canUpdate)}
        disabled={editorDisabled}
        required={required}
        cuisineInput={cuisineInput}
        dishInput={dishInput}
        suggestions={suggestions}
        selectionError={selectionError}
        onCuisineInput={setCuisineInput}
        onDishInput={setDishInput}
        onChange={changeDraft}
        onSelectionError={setSelectionError}
      />

      {suggestionQuery.error &&
      !isPrivateFailure(suggestionQuery.error) ? (
        <div className="foodPreferencesPanel__message">
          <p role="alert">
            {safeError(
              suggestionQuery.error,
              "暂时无法读取菜品建议。",
            )}
          </p>
          <button
            type="button"
            disabled={suggestionQuery.isFetching}
            onClick={() => void suggestionQuery.refetch()}
          >
            重新加载菜品建议
          </button>
        </div>
      ) : null}

      {onboarding && canUpdate ? (
        <label className="foodPreferencesPanel__confirmation">
          <input
            type="checkbox"
            checked={confirmed}
            disabled={editorDisabled}
            onChange={(event) => setConfirmed(event.currentTarget.checked)}
          />
          <span>我确认以上是本人的餐饮偏好</span>
        </label>
      ) : null}

      {statusMessage ? (
        <p
          className={
            statusMessage === "餐饮偏好已保存。"
              ? "foodPreferencesPanel__success"
              : "foodPreferencesPanel__error"
          }
          role={statusMessage === "餐饮偏好已保存。" ? "status" : "alert"}
        >
          {statusMessage}
        </p>
      ) : null}

      {conflictVersion !== null ? (
        <button
          className="foodPreferencesPanel__conflict"
          type="button"
          disabled={mutation.isPending}
          onClick={retryConflict}
        >
          确认并重试
        </button>
      ) : null}

      {props.mode !== "draft" && editorVisible ? (
        <div className="foodPreferencesPanel__actions">
          {onboarding ? (
            <button
              type="button"
              disabled={mutation.isPending}
              onClick={props.onSkipped}
            >
              稍后填写
            </button>
          ) : (
            <button
              type="button"
              disabled={mutation.isPending || conflictLoading}
              onClick={() => {
                setEditing(false)
                setLocalDraft(
                  acceptedProfile ? profileDraft(acceptedProfile) : EMPTY_DRAFT,
                )
                setCuisineInput("")
                setDishInput("")
                setSelectionError(null)
                setStatusMessage(null)
                setConfirmed(false)
                setConflictVersion(null)
                setConflictLoading(false)
                pendingSubmissionRef.current = null
                conflictDraftRef.current = null
                mutationResetRef.current()
              }}
            >
              取消编辑
            </button>
          )}
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() =>
              save(renderedDraft, acceptedProfile?.version ?? 0)
            }
          >
            {onboarding ? "完成填写" : "保存餐饮偏好"}
          </button>
        </div>
      ) : null}
    </section>
  )
}
