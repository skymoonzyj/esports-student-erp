import { zodResolver } from "@hookform/resolvers/zod"
import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import type { DefaultValues } from "react-hook-form"
import { useNavigate } from "react-router-dom"
import { z } from "zod"

import { ProblemDetailsError } from "../api/client"
import { createPerson, getPerson, updatePerson } from "./api"
import type { PersonDetail, PersonInput, PersonType, StaffType } from "./api"
import "./people.css"

const personFormSchema = z
  .object({
    person_type: z.enum(["student", "guardian", "staff", "visitor"]),
    full_name: z.string().trim().min(2, "姓名至少填写 2 个字").max(80),
    identity_number: z.string().trim().min(18, "请填写 18 位身份证号码").max(18),
    sex: z.enum(["male", "female"], { error: "请选择性别" }),
    date_of_birth: z.string().min(1, "请选择出生日期"),
    phone: z.string().trim(),
    email: z.string().trim(),
    student_number: z.string().trim(),
    staff_type: z.string(),
    visit_reason: z.string().trim(),
    linked_student_id: z.string().trim(),
  })
  .superRefine((value, context) => {
    if (value.person_type === "student" && value.student_number.length < 3) {
      context.addIssue({ code: "custom", path: ["student_number"], message: "请填写学员编号" })
    }
    if (["guardian", "staff", "visitor"].includes(value.person_type) && !value.phone) {
      context.addIssue({ code: "custom", path: ["phone"], message: "该人员类型必须填写手机号" })
    }
    if (["guardian", "staff"].includes(value.person_type) && !value.email) {
      context.addIssue({ code: "custom", path: ["email"], message: "该人员类型必须填写邮箱" })
    }
    if (value.email && !z.email().safeParse(value.email).success) {
      context.addIssue({ code: "custom", path: ["email"], message: "邮箱格式不正确" })
    }
    if (value.person_type === "staff" && !value.staff_type) {
      context.addIssue({ code: "custom", path: ["staff_type"], message: "请选择工作人员类型" })
    }
    if (value.person_type === "visitor" && value.visit_reason.length < 2) {
      context.addIssue({ code: "custom", path: ["visit_reason"], message: "请填写来访原因" })
    }
  })

type PersonFormValues = z.infer<typeof personFormSchema>

const problemFieldByCode: Partial<Record<string, keyof PersonFormValues>> = {
  identity_already_exists: "identity_number",
  student_number_already_exists: "student_number",
  invalid_linked_student: "linked_student_id",
}

const personTypeOptions: Array<{ value: PersonType; label: string; hint: string }> = [
  { value: "student", label: "学员", hint: "建立学员编号和学习档案" },
  { value: "guardian", label: "监护人", hint: "后续绑定一名或多名学员" },
  { value: "staff", label: "工作人员", hint: "教练、生活老师、客服等" },
  { value: "visitor", label: "访客", hint: "家长或其他临时来访人员" },
]

const defaultValues: DefaultValues<PersonFormValues> = {
  person_type: "student",
  full_name: "",
  identity_number: "",
  sex: undefined,
  date_of_birth: "",
  phone: "",
  email: "",
  student_number: "",
  staff_type: "",
  visit_reason: "",
  linked_student_id: "",
}

function detailValues(person: PersonDetail): PersonFormValues {
  return {
    person_type: person.person_types[0] ?? "student",
    full_name: person.full_name,
    identity_number: person.identity_number,
    sex: person.sex,
    date_of_birth: person.date_of_birth,
    phone: person.phone ?? "",
    email: person.email ?? "",
    student_number: person.student_number ?? "",
    staff_type: person.staff_type ?? "",
    visit_reason: person.visit_reason ?? "",
    linked_student_id: person.linked_student_id ?? "",
  }
}

function changedValues(
  values: PersonFormValues,
  dirtyFields: Partial<Record<keyof PersonFormValues, boolean>>,
): Partial<PersonInput> {
  const input: Partial<PersonInput> = {}
  if (dirtyFields.full_name) input.full_name = values.full_name
  if (dirtyFields.identity_number) input.identity_number = values.identity_number.toUpperCase()
  if (dirtyFields.sex) input.sex = values.sex
  if (dirtyFields.date_of_birth) input.date_of_birth = values.date_of_birth
  if (dirtyFields.phone) input.phone = values.phone || null
  if (dirtyFields.email) input.email = values.email || null
  if (dirtyFields.student_number) input.student_number = values.student_number
  if (dirtyFields.staff_type) input.staff_type = values.staff_type as StaffType
  if (dirtyFields.visit_reason) input.visit_reason = values.visit_reason
  if (dirtyFields.linked_student_id) input.linked_student_id = values.linked_student_id || null
  return input
}

function fieldName(raw: string): keyof PersonFormValues | null {
  const candidate = raw.split(".").at(-1)
  if (!candidate) return null
  const fields: Array<keyof PersonFormValues> = [
    "person_type",
    "full_name",
    "identity_number",
    "sex",
    "date_of_birth",
    "phone",
    "email",
    "student_number",
    "staff_type",
    "visit_reason",
    "linked_student_id",
  ]
  return fields.includes(candidate as keyof PersonFormValues)
    ? (candidate as keyof PersonFormValues)
    : null
}

export function PersonForm({ personId }: { personId?: string }) {
  const navigate = useNavigate()
  const [submitError, setSubmitError] = useState<string | null>(null)
  const isEditing = Boolean(personId)
  const person = useQuery({
    queryKey: ["person", personId],
    queryFn: () => getPerson(personId ?? ""),
    enabled: isEditing,
  })
  const {
    register,
    handleSubmit,
    reset,
    setError,
    watch,
    formState: { dirtyFields, errors, isSubmitting },
  } = useForm<PersonFormValues>({
    resolver: zodResolver(personFormSchema),
    defaultValues,
  })
  const personType = watch("person_type")

  useEffect(() => {
    if (person.data) reset(detailValues(person.data))
  }, [person.data, reset])

  const submit = handleSubmit(async (values) => {
    setSubmitError(null)
    try {
      if (personId) {
        await updatePerson(personId, changedValues(values, dirtyFields))
        navigate(`/people/${personId}`, { replace: true })
        return
      }
      const input: PersonInput = {
        full_name: values.full_name,
        identity_number: values.identity_number.toUpperCase(),
        sex: values.sex,
        date_of_birth: values.date_of_birth,
        phone: values.phone || null,
        email: values.email || null,
      }
      if (values.person_type === "student") input.student_number = values.student_number
      if (values.person_type === "staff") input.staff_type = values.staff_type as StaffType
      if (values.person_type === "visitor") {
        input.visit_reason = values.visit_reason
        input.linked_student_id = values.linked_student_id || null
      }
      const created = await createPerson(values.person_type, input)
      navigate(`/people/${created.id}`, { replace: true })
    } catch (error) {
      if (error instanceof ProblemDetailsError) {
        let fieldErrorApplied = false
        for (const item of error.problem.errors ?? []) {
          const field = fieldName(item.field)
          if (!field) continue
          setError(field, { type: "server", message: item.message })
          fieldErrorApplied = true
        }
        if (!fieldErrorApplied) {
          const field = problemFieldByCode[error.problem.code]
          if (field) {
            setError(field, { type: "server", message: error.problem.detail })
            fieldErrorApplied = true
          }
        }
        if (!fieldErrorApplied) setSubmitError(error.problem.detail)
      } else {
        setSubmitError("暂时无法保存人员，请稍后重试")
      }
    }
  })

  function fieldError(name: keyof PersonFormValues): string | undefined {
    return errors[name]?.message
  }

  if (isEditing && person.isLoading) {
    return <div className="peopleState">正在读取人员档案…</div>
  }
  if (isEditing && (person.isError || !person.data)) {
    return (
      <div className="peopleState" role="alert">
        无法读取该人员档案，可能没有访问权限或资料已不存在。
      </div>
    )
  }

  return (
    <div className="personFormPage">
      <div className="personFormHeading">
        <div>
          <p className="pageEyebrow">{isEditing ? "EDIT PERSON" : "NEW PERSON"}</p>
          <h1>{isEditing ? "编辑人员档案" : "新建人员"}</h1>
          <p>
            {isEditing
              ? "只保存实际修改的字段；未修改的身份证和手机号掩码不会被提交。"
              : "实名字段保存后默认只显示掩码，完整信息需获权并填写用途才能查看。"}
          </p>
        </div>
      </div>

      <form className="personFormCard" onSubmit={submit} noValidate>
        <fieldset className="personTypeChooser" disabled={isEditing}>
          <legend>人员类型</legend>
          <div>
            {personTypeOptions.map((option) => (
              <label key={option.value} className={personType === option.value ? "active" : ""}>
                <input type="radio" value={option.value} {...register("person_type")} />
                <strong>{option.label}</strong>
                <small>{option.hint}</small>
              </label>
            ))}
          </div>
        </fieldset>

        <section className="formSection" aria-labelledby="basic-person-title">
          <div className="formSectionHeading">
            <span>01</span>
            <div>
              <h2 id="basic-person-title">实名基础资料</h2>
              <p>姓名、身份证、性别和出生日期适用于所有人员。</p>
            </div>
          </div>
          <div className="formGrid">
            <div className="formField">
              <label htmlFor="person-name">姓名</label>
              <input
                id="person-name"
                {...register("full_name")}
                aria-invalid={Boolean(errors.full_name)}
              />
              {fieldError("full_name") ? <small className="fieldError">{fieldError("full_name")}</small> : null}
            </div>
            <div className="formField">
              <label htmlFor="person-identity">身份证</label>
              <input
                id="person-identity"
                autoComplete="off"
                {...register("identity_number")}
                aria-invalid={Boolean(errors.identity_number)}
              />
              {fieldError("identity_number") ? <small className="fieldError">{fieldError("identity_number")}</small> : null}
            </div>
            <div className="formField">
              <label htmlFor="person-sex">性别</label>
              <select id="person-sex" {...register("sex")} aria-invalid={Boolean(errors.sex)}>
                <option value="">请选择</option>
                <option value="female">女</option>
                <option value="male">男</option>
              </select>
              {fieldError("sex") ? <small className="fieldError">{fieldError("sex")}</small> : null}
            </div>
            <div className="formField">
              <label htmlFor="person-birth">出生日期</label>
              <input
                id="person-birth"
                type="date"
                {...register("date_of_birth")}
                aria-invalid={Boolean(errors.date_of_birth)}
              />
              {fieldError("date_of_birth") ? <small className="fieldError">{fieldError("date_of_birth")}</small> : null}
            </div>
          </div>
        </section>

        <section className="formSection" aria-labelledby="contact-person-title">
          <div className="formSectionHeading">
            <span>02</span>
            <div>
              <h2 id="contact-person-title">联系与业务资料</h2>
              <p>开通登录账号前必须补齐手机号和邮箱。</p>
            </div>
          </div>
          <div className="formGrid">
            <div className="formField">
              <label htmlFor="person-phone">手机号</label>
              <input
                id="person-phone"
                type="tel"
                inputMode="numeric"
                {...register("phone")}
                aria-invalid={Boolean(errors.phone)}
              />
              {fieldError("phone") ? <small className="fieldError">{fieldError("phone")}</small> : null}
            </div>
            <div className="formField">
              <label htmlFor="person-email">邮箱</label>
              <input
                id="person-email"
                type="email"
                {...register("email")}
                aria-invalid={Boolean(errors.email)}
              />
              {fieldError("email") ? <small className="fieldError">{fieldError("email")}</small> : null}
            </div>

            {personType === "student" ? (
              <div className="formField formSpanFull">
                <label htmlFor="student-number">学员编号</label>
                <input
                  id="student-number"
                  {...register("student_number")}
                  aria-invalid={Boolean(errors.student_number)}
                />
                {fieldError("student_number") ? <small className="fieldError">{fieldError("student_number")}</small> : null}
              </div>
            ) : null}

            {personType === "staff" ? (
              <div className="formField formSpanFull">
                <label htmlFor="staff-type">工作人员类型</label>
                <select
                  id="staff-type"
                  {...register("staff_type")}
                  aria-invalid={Boolean(errors.staff_type)}
                >
                  <option value="">请选择</option>
                  <option value="coach">教练</option>
                  <option value="life_teacher">生活老师</option>
                  <option value="customer_service">客服</option>
                  <option value="administration">行政管理</option>
                  <option value="other">其他工作人员</option>
                </select>
                {fieldError("staff_type") ? <small className="fieldError">{fieldError("staff_type")}</small> : null}
              </div>
            ) : null}

            {personType === "visitor" ? (
              <>
                <div className="formField">
                  <label htmlFor="visit-reason">来访原因</label>
                  <input
                    id="visit-reason"
                    {...register("visit_reason")}
                    aria-invalid={Boolean(errors.visit_reason)}
                  />
                  {fieldError("visit_reason") ? <small className="fieldError">{fieldError("visit_reason")}</small> : null}
                </div>
                <div className="formField">
                  <label htmlFor="linked-student">关联学员 ID（选填）</label>
                  <input
                    id="linked-student"
                    {...register("linked_student_id")}
                    aria-invalid={Boolean(errors.linked_student_id)}
                  />
                  {fieldError("linked_student_id") ? <small className="fieldError">{fieldError("linked_student_id")}</small> : null}
                </div>
              </>
            ) : null}
          </div>
        </section>

        {submitError ? (
          <p className="formSubmitError" role="alert">
            {submitError}
          </p>
        ) : null}
        <div className="personFormActions">
          <button
            className="secondaryButton"
            type="button"
            onClick={() => navigate(personId ? `/people/${personId}` : "/people")}
          >
            取消
          </button>
          <button className="primaryButton" type="submit" disabled={isSubmitting}>
            {isSubmitting ? "正在保存…" : isEditing ? "保存修改" : "保存人员"}
          </button>
        </div>
      </form>
    </div>
  )
}
