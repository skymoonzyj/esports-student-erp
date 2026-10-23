import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Link } from "react-router-dom"

import { useAuth } from "../auth/AuthProvider"
import { FoodPreferencesPanel } from "../foodPreferences/FoodPreferencesPanel"
import {
  getFoodPreferenceCatalog,
  getFoodPreferenceProfile,
  type CuisineCatalog,
  type FoodPreferenceDraft,
  type FoodPreferenceProfile,
} from "../foodPreferences/api"
import {
  getSurveyContext,
  reviseSurvey,
  submitSurvey,
  submitSurveySupplement,
  type SurveyContext,
  type SurveyStaff,
  type SurveySubmissionInput,
  type SurveySupplementInput,
} from "./surveyApi"
import { surveyKeys } from "./surveyKeys"
import { useSurveyAccessRevocation } from "./useSurveyAccessRevocation"
import { formatDateOnly } from "../shared/date"
import "./camp-surveys.css"

type FoodSurveyData = {
  catalog: CuisineCatalog
  profile: FoodPreferenceProfile
}

type SurveyMode = "survey" | "supplement"
type SupplementRatingDraft = SurveySupplementInput["staff_ratings"][number]

const scoreOptions = Array.from({ length: 10 }, (_, index) => index + 1)
const contextPageSize = 20
function formatShanghaiDateTime(value: string): string {
  return formatDateOnly(value)
}

function newIdempotencyKey(prefix: string): string {
  const random = typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `${prefix}-${random}`
}

function emptyDraft(context: SurveyContext): SurveySubmissionInput {
  return {
    idempotency_key: newIdempotencyKey("camp-survey"),
    expected_snapshot_version: context.snapshot_version,
    expected_food_preference_version: 0,
    favorite_cuisines: [],
    favorite_dishes: [],
    course_training_score: 8,
    life_service_score: 8,
    service_management_score: 8,
    staff_ratings: context.staff.map((staff) => ({
      staff_snapshot_id: staff.id,
      score: 8,
    })),
  }
}

function fromFoodData(
  context: SurveyContext,
  food: FoodSurveyData,
): SurveySubmissionInput {
  const draft = emptyDraft(context)
  return {
    ...draft,
    expected_food_preference_version: food.profile.version,
    favorite_cuisines: food.profile.cuisines.slice(0, 3).map((item) => ({
      cuisine_option_id: item.cuisine_option_id,
      name: item.name,
      custom_name: item.custom_name,
    })),
    favorite_dishes: food.profile.dishes.slice(0, 3),
  }
}

function fromLatestSubmission(
  context: SurveyContext,
  food: FoodSurveyData,
): SurveySubmissionInput {
  const draft = fromFoodData(context, food)
  const latest = context.latest_submission
  if (!latest) return draft
  return {
    ...draft,
    course_training_score: latest.course_training_score,
    course_training_reason: latest.course_training_reason,
    life_service_score: latest.life_service_score,
    life_service_reason: latest.life_service_reason,
    service_management_score: latest.service_management_score,
    service_management_reason: latest.service_management_reason,
    overall_suggestion: latest.overall_suggestion,
    favorite_cuisines: latest.favorite_cuisines.slice(0, 3),
    favorite_dishes: latest.favorite_dishes.slice(0, 3),
    staff_ratings: latest.staff_ratings.map((rating) => ({
      staff_snapshot_id: rating.staff_snapshot_id,
      score: rating.score,
      reason: rating.reason,
      suggestion: rating.suggestion,
    })),
  }
}

function roleLabel(category: string): string {
  const labels: Record<string, string> = {
    teaching: "教学",
    life_service: "生活服务",
    operations: "运营",
    service_management: "服务管理",
  }
  return labels[category] ?? category
}

function surveyFoodDraft(form: SurveySubmissionInput): FoodPreferenceDraft {
  return {
    cuisines: form.favorite_cuisines.map((item) => ({
      cuisine_option_id: item.cuisine_option_id,
      custom_name: item.custom_name,
    })),
    dishes: [...form.favorite_dishes],
  }
}

function updateSurveyFoodDraft(
  form: SurveySubmissionInput,
  draft: FoodPreferenceDraft,
  catalog: CuisineCatalog,
): SurveySubmissionInput {
  const favoriteCuisines = draft.cuisines.map((item) => {
    const option = catalog.items.find(
      (candidate) => candidate.id === item.cuisine_option_id,
    )
    const existing = form.favorite_cuisines.find(
      (candidate) =>
        candidate.cuisine_option_id === item.cuisine_option_id &&
        candidate.custom_name === (item.custom_name ?? null),
    )
    return {
      cuisine_option_id: item.cuisine_option_id,
      name: option?.name ?? existing?.name ?? item.custom_name ?? "已选菜系",
      custom_name: item.custom_name ?? null,
    }
  })
  return {
    ...form,
    favorite_cuisines: favoriteCuisines,
    favorite_dishes: [...draft.dishes],
  }
}

function updateStaffRating(
  form: SurveySubmissionInput,
  staffId: string,
  patch: Partial<SurveySubmissionInput["staff_ratings"][number]>,
): SurveySubmissionInput {
  return {
    ...form,
    staff_ratings: form.staff_ratings.map((rating) =>
      rating.staff_snapshot_id === staffId ? { ...rating, ...patch } : rating,
    ),
  }
}

function updateSupplementRating(
  ratings: SupplementRatingDraft[],
  staffId: string,
  patch: Partial<SupplementRatingDraft>,
): SupplementRatingDraft[] {
  return ratings.map((rating) =>
    rating.staff_snapshot_id === staffId ? { ...rating, ...patch } : rating,
  )
}

function RatingSelect({
  label,
  value,
  onChange,
}: {
  label: string
  value: number
  onChange: (value: number) => void
}) {
  return (
    <select
      aria-label={label}
      value={String(value)}
      onChange={(event) => onChange(Number(event.currentTarget.value))}
    >
      {scoreOptions.map((score) => (
        <option key={score} value={score}>{score}</option>
      ))}
    </select>
  )
}

function StaffRatingFields({
  staff,
  rating,
  onChange,
}: {
  staff: SurveyStaff
  rating: { score: number; reason?: string | null; suggestion?: string | null }
  onChange: (patch: Partial<typeof rating>) => void
}) {
  return (
    <fieldset className="campSurveyStaffRating">
      <legend>{staff.staff_name} · {staff.role_name} · {roleLabel(staff.role_category)}</legend>
      <label>
        {staff.staff_name}评分
        <RatingSelect
          label={`${staff.staff_name}评分`}
          value={rating.score}
          onChange={(score) => onChange({ score })}
        />
      </label>
      <label>
        {staff.staff_name}评分原因
        <textarea
          aria-label={`${staff.staff_name}评分原因`}
          rows={2}
          maxLength={2000}
          value={rating.reason ?? ""}
          onChange={(event) => onChange({ reason: event.currentTarget.value || null })}
        />
      </label>
      <label>
        {staff.staff_name}评分建议
        <textarea
          aria-label={`${staff.staff_name}评分建议`}
          rows={2}
          maxLength={2000}
          value={rating.suggestion ?? ""}
          onChange={(event) => onChange({ suggestion: event.currentTarget.value || null })}
        />
      </label>
    </fieldset>
  )
}

export function CampSurveysPage() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<SurveyContext | null>(null)
  const [contextPage, setContextPage] = useState(1)
  const [mode, setMode] = useState<SurveyMode>("survey")
  const [form, setForm] = useState<SurveySubmissionInput | null>(null)
  const [supplementRatings, setSupplementRatings] = useState<SupplementRatingDraft[]>([])
  const [supplementIdempotencyKey, setSupplementIdempotencyKey] = useState("")
  const [notice, setNotice] = useState("")
  const [saving, setSaving] = useState(false)

  const contextQuery = useQuery({
    queryKey: surveyKeys.context(accountId, contextPage, contextPageSize),
    queryFn: ({ signal }) => getSurveyContext(contextPage, contextPageSize, signal),
    enabled: Boolean(user?.account_id),
    refetchOnWindowFocus: "always",
    retry: false,
  })

  const foodQuery = useQuery({
    queryKey: surveyKeys.food(
      accountId,
      selected?.cycle_id ?? "none",
      selected?.student_person_id ?? "none",
    ),
    queryFn: async ({ signal }): Promise<FoodSurveyData> => {
      if (!selected) throw new Error("未选择营期")
      const [catalog, profile] = await Promise.all([
        getFoodPreferenceCatalog(false, signal),
        getFoodPreferenceProfile(selected.student_person_id, signal),
      ])
      return { catalog, profile }
    },
    enabled: Boolean(selected && mode === "survey"),
    refetchOnWindowFocus: "always",
    retry: false,
  })

  useEffect(() => {
    if (mode !== "survey" || !selected || !foodQuery.data) return
    setForm((current) =>
      current && current.expected_snapshot_version === selected.snapshot_version
        ? current
        : fromLatestSubmission(selected, foodQuery.data),
    )
  }, [foodQuery.data, mode, selected])

  useEffect(() => {
    if (!user?.account_id) return
    return () => {
      queryClient.removeQueries({ queryKey: surveyKeys.root(accountId) })
    }
  }, [accountId, queryClient, user?.account_id])

  function closeEditor(clearNotice = true): void {
    setSelected(null)
    setMode("survey")
    setForm(null)
    setSupplementRatings([])
    setSupplementIdempotencyKey("")
    if (clearNotice) setNotice("")
  }

  const revokeAccess = useSurveyAccessRevocation({
    accountId: user?.account_id,
    errors: [contextQuery.error, foodQuery.error],
    onRevoke: () => {
      closeEditor()
      setSaving(false)
    },
  })

  function openSurvey(item: SurveyContext): void {
    setNotice("")
    setMode("survey")
    setSelected(item)
    setForm(null)
    setSupplementRatings([])
    setSupplementIdempotencyKey("")
  }

  function openSupplement(item: SurveyContext): void {
    setNotice("")
    setMode("supplement")
    setSelected(item)
    setForm(null)
    setSupplementRatings(
      item.supplement_staff.map((staff) => ({
        staff_snapshot_id: staff.id,
        score: 8,
      })),
    )
    setSupplementIdempotencyKey(newIdempotencyKey("camp-survey-supplement"))
  }

  async function saveSurvey(): Promise<void> {
    if (!selected || !form || saving) return
    const validCuisines = form.favorite_cuisines.filter(
      (item) => item.cuisine_option_id && item.name.trim(),
    )
    const validDishes = form.favorite_dishes.filter((dish) => dish.trim())
    const missingCustomCuisine = validCuisines.some((item) => {
      const option = foodQuery.data?.catalog.items.find((candidate) => candidate.id === item.cuisine_option_id)
      return option?.is_other === true && !item.custom_name?.trim()
    })
    if (missingCustomCuisine) {
      setNotice("请填写自定义菜系名称。")
      return
    }
    if (
      validCuisines.length < 1 ||
      validCuisines.length > 3 ||
      validDishes.length < 1 ||
      validDishes.length > 3 ||
      form.staff_ratings.length !== selected.staff.length
    ) {
      setNotice("请至少填写 1 个菜系和 1 个最喜欢的菜（各最多 3 项），并完成所有工作人员评分。")
      return
    }
    const payload: SurveySubmissionInput = {
      ...form,
      favorite_cuisines: validCuisines,
      favorite_dishes: validDishes,
    }
    setSaving(true)
    try {
      if (selected.submission_id && selected.can_revise) {
        await reviseSurvey(selected.cycle_id, selected.submission_id, payload)
      } else {
        await submitSurvey(selected.cycle_id, payload)
      }
      closeEditor(false)
      setNotice("问卷已提交")
      await queryClient.invalidateQueries({ queryKey: surveyKeys.contextRoot(accountId) })
    } catch (error) {
      if (!revokeAccess(error)) setNotice("提交失败，请检查网络后重试。")
    } finally {
      setSaving(false)
    }
  }

  async function saveSupplement(): Promise<void> {
    const expectedIds = new Set(
      selected?.supplement_staff.map((staff) => staff.id) ?? [],
    )
    if (
      !selected?.submission_id ||
      !supplementIdempotencyKey ||
      saving ||
      supplementRatings.length !== expectedIds.size ||
      supplementRatings.some((rating) => !expectedIds.has(rating.staff_snapshot_id))
    ) {
      setNotice("请完成全部待补工作人员的评价。")
      return
    }
    setSaving(true)
    const payload: SurveySupplementInput = {
      idempotency_key: supplementIdempotencyKey,
      expected_snapshot_version: selected.snapshot_version,
      staff_ratings: supplementRatings,
    }
    try {
      await submitSurveySupplement(selected.cycle_id, selected.submission_id, payload)
      closeEditor(false)
      setNotice("补充评价已提交")
      await queryClient.invalidateQueries({ queryKey: surveyKeys.contextRoot(accountId) })
    } catch (error) {
      if (!revokeAccess(error)) setNotice("补充评价提交失败，请检查网络后重试。")
    } finally {
      setSaving(false)
    }
  }

  if (contextQuery.isPending) {
    return <main className="campSurveyPage"><p aria-live="polite">正在加载营期问卷…</p></main>
  }
  if (contextQuery.isError) {
    return (
      <main className="campSurveyPage">
        <p role="alert">问卷暂时不可用，请稍后重试。</p>
        <button type="button" onClick={() => void contextQuery.refetch()}>重试</button>
      </main>
    )
  }

  const contexts = contextQuery.data
  const foodReady = Boolean(selected && mode === "survey" && form && foodQuery.data)

  return (
    <main className="campSurveyPage">
      <header>
        <Link to="/evaluations">返回评价中心</Link>
        <p className="pageEyebrow">CAMP SURVEY</p>
        <h1>营期评价</h1>
        <p>学生与监护人可在开放期内提交一次，并拥有一次修改机会。</p>
      </header>

      <section className="campSurveyCards" aria-label="可填写的营期问卷">
        {contexts.length === 0 ? <p>当前没有可访问的营期问卷。</p> : null}
        {contexts.map((item) => {
          const supplementAllowed = Boolean(
            item.submission_id &&
            item.can_supplement &&
            item.supplement_staff.length > 0,
          )
          return (
            <article key={item.cycle_id} className="campSurveyCard">
              <h2>{item.student_name} · 截止 {formatShanghaiDateTime(item.due_at)}</h2>
              <p>开放时间：{formatShanghaiDateTime(item.opens_at)}</p>
              <p>身份：{item.respondent_kind === "guardian" ? "监护人" : "学生"}</p>
              <p>{item.submission_id ? `已提交第 ${item.latest_version ?? 1} 版` : "尚未提交"}</p>
              {item.can_submit || item.can_revise ? (
                <button type="button" onClick={() => openSurvey(item)}>
                  {item.submission_id ? "修改问卷" : "填写问卷"}
                </button>
              ) : null}
              {supplementAllowed ? (
                <button type="button" onClick={() => openSupplement(item)}>
                  补充工作人员评价
                </button>
              ) : null}
              {!item.can_submit && !item.can_revise && !supplementAllowed ? <span>当前不可操作</span> : null}
            </article>
          )
        })}
      </section>
      <nav className="campSurveyPagination" aria-label="营期问卷分页">
        <button type="button" disabled={contextPage === 1} onClick={() => setContextPage((page) => Math.max(1, page - 1))}>上一页问卷</button>
        <span>第 {contextPage} 页</span>
        <button type="button" disabled={contexts.length < contextPageSize} onClick={() => setContextPage((page) => page + 1)}>下一页问卷</button>
      </nav>

      {selected && mode === "survey" && !foodReady && foodQuery.isPending ? (
        <section className="campSurveyForm" aria-live="polite">
          <h2>{selected.submission_id ? "修改营期评价" : "填写营期评价"}</h2>
          <p>正在读取学员餐饮偏好和菜系选项…</p>
        </section>
      ) : null}
      {selected && mode === "survey" && !foodReady && foodQuery.isError ? (
        <section className="campSurveyForm" role="alert">
          <h2>无法读取学员餐饮资料</h2>
          <p>请刷新后重试；监护人填写时读取的是被监护学员的资料。</p>
          <button type="button" onClick={() => void foodQuery.refetch()}>重试</button>
        </section>
      ) : null}

      {selected && mode === "survey" && form && foodQuery.data ? (
        <section className="campSurveyForm" aria-label="问卷填写">
          <h2>{selected.submission_id ? "修改营期评价" : "填写营期评价"}</h2>
          <p className="campSurveyFoodNotice">
            <span>已读取学员餐饮偏好（版本 {foodQuery.data.profile.version}）</span>
            <span>；菜系和菜品最多各 3 项。</span>
          </p>
          <FoodPreferencesPanel
            mode="draft"
            personId={selected.student_person_id}
            required={true}
            value={surveyFoodDraft(form)}
            cuisineNameSnapshots={Object.fromEntries(
              form.favorite_cuisines.map((item) => [
                item.cuisine_option_id,
                item.name,
              ]),
            )}
            onChange={(draft) =>
              setForm(updateSurveyFoodDraft(form, draft, foodQuery.data.catalog))
            }
          />

          <fieldset>
            <legend>总体评分</legend>
            <label>课程训练评分<RatingSelect label="课程训练评分" value={form.course_training_score} onChange={(value) => setForm({ ...form, course_training_score: value })} /></label>
            <label>课程训练评分原因<textarea aria-label="课程训练评分原因" rows={2} maxLength={2000} value={form.course_training_reason ?? ""} onChange={(event) => setForm({ ...form, course_training_reason: event.currentTarget.value || null })} /></label>
            <label>生活服务评分<RatingSelect label="生活服务评分" value={form.life_service_score} onChange={(value) => setForm({ ...form, life_service_score: value })} /></label>
            <label>生活服务评分原因<textarea aria-label="生活服务评分原因" rows={2} maxLength={2000} value={form.life_service_reason ?? ""} onChange={(event) => setForm({ ...form, life_service_reason: event.currentTarget.value || null })} /></label>
            <label>服务管理评分<RatingSelect label="服务管理评分" value={form.service_management_score} onChange={(value) => setForm({ ...form, service_management_score: value })} /></label>
            <label>服务管理评分原因<textarea aria-label="服务管理评分原因" rows={2} maxLength={2000} value={form.service_management_reason ?? ""} onChange={(event) => setForm({ ...form, service_management_reason: event.currentTarget.value || null })} /></label>
            <label>总体建议<textarea aria-label="总体建议" rows={3} maxLength={2000} value={form.overall_suggestion ?? ""} onChange={(event) => setForm({ ...form, overall_suggestion: event.currentTarget.value || null })} /></label>
          </fieldset>

          <fieldset>
            <legend>工作人员评分</legend>
            {selected.staff.map((staff) => {
              const rating = form.staff_ratings.find((item) => item.staff_snapshot_id === staff.id) ?? { staff_snapshot_id: staff.id, score: 8 }
              return <StaffRatingFields key={staff.id} staff={staff} rating={rating} onChange={(patch) => setForm(updateStaffRating(form, staff.id, patch))} />
            })}
          </fieldset>
          <p className="campSurveyHint">提交后仍可在截止前修改一次；原因和建议为选填。</p>
          <div className="campSurveyActions">
            <button type="button" disabled={saving} onClick={() => void saveSurvey()}>{saving ? "正在提交…" : selected.submission_id ? "保存修改" : "提交问卷"}</button>
            <button type="button" className="campSurveySecondaryButton" disabled={saving} onClick={() => closeEditor()}>取消</button>
          </div>
        </section>
      ) : null}

      {selected && mode === "supplement" ? (
        <section className="campSurveyForm" aria-label="补充工作人员评价">
          <h2>补充工作人员评价</h2>
          <p>请完成本次快照中全部新增、尚未评价的工作人员评分。补充评价不消耗一次修改机会。</p>
          <fieldset>
            <legend>待补工作人员</legend>
            {selected.supplement_staff.map((staff) => {
              const rating = supplementRatings.find((item) => item.staff_snapshot_id === staff.id)
              return rating ? (
                <StaffRatingFields
                  key={staff.id}
                  staff={staff}
                  rating={rating}
                  onChange={(patch) => setSupplementRatings(updateSupplementRating(supplementRatings, staff.id, patch))}
                />
              ) : null
            })}
          </fieldset>
          <div className="campSurveyActions">
            <button type="button" disabled={saving} onClick={() => void saveSupplement()}>{saving ? "正在提交…" : "提交补充评价"}</button>
            <button type="button" className="campSurveySecondaryButton" disabled={saving} onClick={() => closeEditor()}>取消</button>
          </div>
        </section>
      ) : null}
      <p role="status" aria-live="polite">{notice}</p>
    </main>
  )
}
