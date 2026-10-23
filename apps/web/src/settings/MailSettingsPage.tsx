import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  CircleAlert,
  CircleCheck,
  Code2,
  Eye,
  Mail,
  PlugZap,
  RotateCcw,
  Save,
  ShieldCheck,
} from "lucide-react"
import { useEffect, useState } from "react"
import type { FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import { formatDateOnly } from "../shared/date"
import {
  getMailTemplate,
  getMailDeliverySettings,
  previewMailTemplate,
  restoreMailTemplate,
  saveMailDeliverySettings,
  saveMailTemplate,
  testMailDeliverySettings,
} from "./api"
import type { MailDeliverySettings, MailTemplate, MailTemplateEventKey, MailTlsMode } from "./api"
import "./settings.css"

type MailFormState = {
  smtp_host: string
  smtp_port: string
  tls_mode: MailTlsMode
  username: string
  sender: string
  from_name: string
  reply_to: string
  authorization_code: string
  enabled: boolean
}

type MailTemplateFormState = {
  subject_template: string
  html_template: string
}

type TemplateField = keyof MailTemplateFormState

const EMPTY_FORM: MailFormState = {
  smtp_host: "smtp.qq.com",
  smtp_port: "465",
  tls_mode: "implicit_tls",
  username: "",
  sender: "",
  from_name: "竞学 ERP",
  reply_to: "",
  authorization_code: "",
  enabled: false,
}

const EMPTY_TEMPLATE_FORM: MailTemplateFormState = {
  subject_template: "",
  html_template: "",
}

const TEMPLATE_EVENT_OPTIONS: Array<{ key: MailTemplateEventKey; label: string }> = [
  { key: "verification_code", label: "邮箱验证码" },
  { key: "password_reset", label: "密码重置验证码" },
]

function formFromSettings(settings: MailDeliverySettings): MailFormState {
  return {
    smtp_host: settings.smtp_host ?? EMPTY_FORM.smtp_host,
    smtp_port: String(settings.smtp_port ?? EMPTY_FORM.smtp_port),
    tls_mode: settings.tls_mode ?? EMPTY_FORM.tls_mode,
    username: "",
    sender: "",
    from_name: settings.from_name ?? EMPTY_FORM.from_name,
    reply_to: "",
    authorization_code: "",
    enabled: settings.enabled,
  }
}

function formFromTemplate(template: MailTemplate): MailTemplateFormState {
  return {
    subject_template: template.subject_template,
    html_template: template.html_template,
  }
}

function problemMessage(error: unknown, fallback: string): string {
  if (error instanceof ProblemDetailsError) return error.problem.detail
  return fallback
}

function formatDate(value: string | null): string {
  if (!value) return "从未测试"
  return formatDateOnly(value)
}

function testStatusLabel(settings: MailDeliverySettings | undefined): string {
  if (!settings || settings.last_test_status === "never") return "尚未测试"
  return settings.last_test_status === "passed" ? "连接测试通过" : "连接测试失败"
}

export function MailSettingsPage() {
  const queryClient = useQueryClient()
  const settings = useQuery({
    queryKey: ["mail-delivery-settings"],
    queryFn: getMailDeliverySettings,
    retry: false,
  })
  const [templateEventKey, setTemplateEventKey] = useState<MailTemplateEventKey>("verification_code")
  const templateSettings = useQuery({
    queryKey: ["mail-template", templateEventKey],
    queryFn: () => getMailTemplate(templateEventKey),
    retry: false,
  })
  const [form, setForm] = useState<MailFormState>(EMPTY_FORM)
  const [formInitialized, setFormInitialized] = useState(false)
  const [templateDrafts, setTemplateDrafts] = useState<
    Partial<Record<MailTemplateEventKey, MailTemplateFormState>>
  >({})
  const [activeTemplateField, setActiveTemplateField] = useState<TemplateField>("html_template")
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [templateNotice, setTemplateNotice] = useState<string | null>(null)
  const [templateError, setTemplateError] = useState<string | null>(null)

  useEffect(() => {
    if (!settings.data || formInitialized) return
    setForm(formFromSettings(settings.data))
    setFormInitialized(true)
  }, [formInitialized, settings.data])

  const activeTemplate =
    templateSettings.data?.event_key === templateEventKey ? templateSettings.data : undefined
  const templateForm = templateDrafts[templateEventKey] ?? EMPTY_TEMPLATE_FORM

  useEffect(() => {
    if (!activeTemplate || templateSettings.isFetching) return
    setTemplateDrafts((current) =>
      current[templateEventKey]
        ? current
        : { ...current, [templateEventKey]: formFromTemplate(activeTemplate) },
    )
  }, [activeTemplate, templateEventKey, templateSettings.isFetching])

  const save = useMutation({
    mutationFn: saveMailDeliverySettings,
    onSuccess: (value) => {
      queryClient.setQueryData(["mail-delivery-settings"], value)
      setForm((current) => ({ ...current, authorization_code: "" }))
      setNotice("邮件发送配置已保存")
      setError(null)
    },
    onError: (value) => {
      setError(problemMessage(value, "保存失败，请检查配置后重试"))
      setNotice(null)
    },
  })
  const test = useMutation({
    mutationFn: testMailDeliverySettings,
    onSuccess: async (value) => {
      await queryClient.invalidateQueries({ queryKey: ["mail-delivery-settings"] })
      setNotice(value.status === "passed" ? "SMTP 连接测试通过" : "SMTP 连接测试失败")
      setError(null)
    },
    onError: (value) => {
      setError(problemMessage(value, "无法测试邮件连接，请先保存配置"))
      setNotice(null)
    },
  })
  const previewTemplate = useMutation({
    mutationFn: ({ eventKey, input }: { eventKey: MailTemplateEventKey; input: MailTemplateFormState }) =>
      previewMailTemplate(eventKey, input),
    onSuccess: () => {
      setTemplateError(null)
    },
    onError: (value) => {
      setTemplateError(problemMessage(value, "模板预览失败，请检查内容后重试"))
      setTemplateNotice(null)
    },
  })
  const saveTemplate = useMutation({
    mutationFn: ({ eventKey, input }: { eventKey: MailTemplateEventKey; input: MailTemplateFormState }) =>
      saveMailTemplate(eventKey, input),
    onSuccess: (value) => {
      queryClient.setQueryData(["mail-template", value.event_key], value)
      setTemplateDrafts((current) => ({ ...current, [value.event_key]: formFromTemplate(value) }))
      setTemplateNotice(`${value.event_name}模板已保存，后续邮件将使用新模板`)
      setTemplateError(null)
    },
    onError: (value) => {
      setTemplateError(problemMessage(value, "保存模板失败，请检查内容后重试"))
      setTemplateNotice(null)
    },
  })
  const restoreTemplate = useMutation({
    mutationFn: (eventKey: MailTemplateEventKey) => restoreMailTemplate(eventKey),
    onSuccess: (value) => {
      queryClient.setQueryData(["mail-template", value.event_key], value)
      setTemplateDrafts((current) => ({ ...current, [value.event_key]: formFromTemplate(value) }))
      previewTemplate.reset()
      setTemplateNotice(`${value.event_name}已恢复官方默认模板`)
      setTemplateError(null)
    },
    onError: (value) => {
      setTemplateError(problemMessage(value, "恢复默认模板失败"))
      setTemplateNotice(null)
    },
  })

  function update<K extends keyof MailFormState>(key: K, value: MailFormState[K]): void {
    setForm((current) => ({ ...current, [key]: value }))
    setNotice(null)
    setError(null)
  }

  function updateTemplate<K extends TemplateField>(
    key: K,
    value: MailTemplateFormState[K],
  ): void {
    setTemplateDrafts((current) => ({
      ...current,
      [templateEventKey]: {
        ...templateForm,
        [key]: value,
      },
    }))
    previewTemplate.reset()
    setTemplateNotice(null)
    setTemplateError(null)
  }

  function appendTemplateToken(token: string): void {
    const key = activeTemplateField
    const separator = templateForm[key] && !templateForm[key].endsWith(" ") ? " " : ""
    updateTemplate(key, `${templateForm[key]}${separator}${token}`)
  }

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const payload = {
      smtp_host: form.smtp_host.trim(),
      smtp_port: Number(form.smtp_port),
      tls_mode: form.tls_mode,
      ...(form.username.trim() ? { username: form.username.trim() } : {}),
      ...(form.sender.trim() ? { sender: form.sender.trim() } : {}),
      from_name: form.from_name.trim(),
      ...(form.reply_to.trim() ? { reply_to: form.reply_to.trim() } : {}),
      ...(form.authorization_code ? { authorization_code: form.authorization_code } : {}),
      enabled: form.enabled,
    }
    save.mutate(payload)
  }

  const current = settings.data!
  const busy = save.isPending || test.isPending
  const templateBusy = previewTemplate.isPending || saveTemplate.isPending || restoreTemplate.isPending
  const statusClass = current?.last_test_status === "passed"
    ? "passed"
    : current?.last_test_status === "failed"
      ? "failed"
      : "never"

  return (
    <div className="settingsPage mailSettingsPage">
      <div className="settingsHeading">
        <div>
          <p className="pageEyebrow">ADMIN · MAIL DELIVERY</p>
          <h1>邮件发送设置</h1>
          <p>仅超级管理员可以维护验证码邮件的 SMTP 发送账号和模板。</p>
        </div>
        <span className="settingsSecurityBadge">
          <ShieldCheck aria-hidden="true" /> 超级管理员专属
        </span>
      </div>

      <section className="mailSettingsStatus" aria-label="邮件配置状态">
        <div className="mailSettingsStatusIcon" aria-hidden="true">
          <Mail />
        </div>
        <div className="mailSettingsStatusCopy">
          <strong>{current?.configured ? "已保存发送账号" : "尚未配置发送账号"}</strong>
          <span>
            {current?.configured
              ? `${current.sender_masked ?? "未填写发件地址"} · ${current.enabled ? "已启用" : "未启用"}`
              : "保存后可进行连接测试"}
          </span>
        </div>
        <span className={`statusPill ${current?.enabled ? "active" : "disabled"}`}>
          {current?.enabled ? "发送已启用" : "发送未启用"}
        </span>
      </section>

      {settings.isPending ? <div className="settingsState">正在读取邮件配置…</div> : null}
      {settings.isError ? (
        <div className="settingsState" role="alert">
          <p>{problemMessage(settings.error, "暂时无法读取邮件配置")}</p>
          <button type="button" className="secondaryButton" onClick={() => void settings.refetch()}>
            重新读取
          </button>
        </div>
      ) : null}

      {settings.data ? (
        <form className="mailSettingsForm" onSubmit={submit}>
          <section className="settingsCard">
            <div className="settingsCardHeader">
              <div>
                <h2>SMTP 服务器</h2>
                <p>填写邮箱服务商提供的服务器地址和加密方式。</p>
              </div>
              <PlugZap aria-hidden="true" />
            </div>
            <div className="mailSettingsGrid">
              <div className="formField mailSettingsHost">
                <label htmlFor="mail-smtp-host">SMTP 地址</label>
                <input
                  id="mail-smtp-host"
                  value={form.smtp_host}
                  onChange={(event) => update("smtp_host", event.target.value)}
                  autoComplete="url"
                  required
                />
              </div>
              <div className="formField">
                <label htmlFor="mail-smtp-port">端口</label>
                <input
                  id="mail-smtp-port"
                  type="number"
                  min={1}
                  max={65535}
                  value={form.smtp_port}
                  onChange={(event) => update("smtp_port", event.target.value)}
                  required
                />
              </div>
              <div className="formField">
                <label htmlFor="mail-tls-mode">加密方式</label>
                <select
                  id="mail-tls-mode"
                  value={form.tls_mode}
                  onChange={(event) => update("tls_mode", event.target.value as MailTlsMode)}
                >
                  <option value="implicit_tls">SSL/TLS（465）</option>
                  <option value="starttls">STARTTLS（587）</option>
                </select>
              </div>
            </div>
          </section>

          <section className="settingsCard">
            <div className="settingsCardHeader">
              <div>
                <h2>发件身份</h2>
                <p>留空账号、发件地址或回复地址，会保留已保存的值。</p>
              </div>
              <Mail aria-hidden="true" />
            </div>
            <div className="mailSettingsGrid">
              <div className="formField">
                <label htmlFor="mail-smtp-username">SMTP 用户名</label>
                <input
                  id="mail-smtp-username"
                  value={form.username}
                  onChange={(event) => update("username", event.target.value)}
                  placeholder={current.username_masked ?? "例如 admin@example.com"}
                  autoComplete="username"
                  required={!current.configured}
                />
              </div>
              <div className="formField">
                <label htmlFor="mail-sender">发件地址</label>
                <input
                  id="mail-sender"
                  type="email"
                  value={form.sender}
                  onChange={(event) => update("sender", event.target.value)}
                  placeholder={current.sender_masked ?? "例如 admin@example.com"}
                  autoComplete="email"
                  required={!current.configured}
                />
              </div>
              <div className="formField">
                <label htmlFor="mail-from-name">显示名称</label>
                <input
                  id="mail-from-name"
                  value={form.from_name}
                  onChange={(event) => update("from_name", event.target.value)}
                  autoComplete="organization"
                  required
                />
              </div>
              <div className="formField">
                <label htmlFor="mail-reply-to">回复地址</label>
                <input
                  id="mail-reply-to"
                  type="email"
                  value={form.reply_to}
                  onChange={(event) => update("reply_to", event.target.value)}
                  placeholder={current.reply_to_masked ?? "例如 admin@example.com"}
                  autoComplete="email"
                  required={!current.configured}
                />
              </div>
            </div>
          </section>

          <section className="settingsCard">
            <div className="settingsCardHeader">
              <div>
                <h2>安全凭据</h2>
                <p>授权码只写入加密存储，不会在页面、接口或审计记录中回显。</p>
              </div>
              <ShieldCheck aria-hidden="true" />
            </div>
            <div className="mailSettingsCredentialRow">
              <div className="formField">
                <label htmlFor="mail-authorization-code">SMTP 授权码</label>
                <input
                  id="mail-authorization-code"
                  type="password"
                  value={form.authorization_code}
                  onChange={(event) => update("authorization_code", event.target.value)}
                  placeholder={current.authorization_code_configured ? "已配置，留空保持不变" : "请输入授权码"}
                  autoComplete="new-password"
                  required={!current.authorization_code_configured}
                />
              </div>
              <label className="mailSettingsSwitch">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(event) => update("enabled", event.target.checked)}
                />
                <span>
                  <strong>启用邮件发送</strong>
                  <small>关闭后不会使用此账号发送验证码</small>
                </span>
              </label>
            </div>
          </section>

          {notice ? (
            <div className="mailSettingsMessage success" role="status">
              <CircleCheck aria-hidden="true" />
              <span>{notice}</span>
            </div>
          ) : null}
          {error ? (
            <div className="mailSettingsMessage error" role="alert">
              <CircleAlert aria-hidden="true" />
              <span>{error}</span>
            </div>
          ) : null}

          <div className="mailSettingsActions">
            <div className="mailSettingsTestState">
              <span className={`statusPill ${statusClass}`}>{testStatusLabel(current)}</span>
              <small>最近测试：{formatDate(current.last_test_at)}</small>
            </div>
            <div className="mailSettingsButtons">
              <button
                className="secondaryButton"
                type="button"
                disabled={busy || !current.configured}
                onClick={() => {
                  setNotice(null)
                  setError(null)
                  test.mutate()
                }}
              >
                <PlugZap aria-hidden="true" />
                {test.isPending ? "测试中…" : "测试连接"}
              </button>
              <button className="primaryButton" type="submit" disabled={busy}>
                <Save aria-hidden="true" />
                {save.isPending ? "保存中…" : "保存配置"}
              </button>
            </div>
          </div>
        </form>
      ) : null}

      <section className="settingsCard mailTemplateCard" aria-labelledby="mail-template-heading">
        <div className="settingsCardHeader">
          <div>
            <h2 id="mail-template-heading">邮件模板</h2>
            <p>按事件自定义邮件主题与 HTML 内容，邮箱验证码与密码重置验证码可分别设置。</p>
          </div>
          <Code2 aria-hidden="true" />
        </div>

        {templateSettings.isPending || (templateSettings.isFetching && !activeTemplate) ? (
          <div className="mailTemplateState">正在读取模板…</div>
        ) : null}
        {templateSettings.isError ? (
          <div className="mailTemplateState" role="alert">
            <p>{problemMessage(templateSettings.error, "暂时无法读取邮件模板")}</p>
            <button
              type="button"
              className="secondaryButton"
              onClick={() => void templateSettings.refetch()}
            >
              重新读取
            </button>
          </div>
        ) : null}

        {activeTemplate ? (
          <>
            <div className="mailTemplateEventRow">
              <label className="formField" htmlFor="mail-template-event">
                <span>事件</span>
                <select
                  id="mail-template-event"
                  value={templateEventKey}
                  disabled={templateBusy}
                  onChange={(event) => {
                    const nextEventKey = event.target.value as MailTemplateEventKey
                    setTemplateEventKey(nextEventKey)
                    setTemplateNotice(null)
                    setTemplateError(null)
                    previewTemplate.reset()
                  }}
                >
                  {TEMPLATE_EVENT_OPTIONS.map((option) => (
                    <option key={option.key} value={option.key}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="mailTemplateEventDescription">
                <strong>{activeTemplate.event_name}</strong>
                <span>{activeTemplate.description}</span>
              </div>
              <span className={`statusPill ${activeTemplate.is_default ? "never" : "active"}`}>
                {activeTemplate.is_default ? "官方默认模板" : "自定义模板"}
              </span>
            </div>

            <div className="mailTemplateWorkspace">
              <div className="mailTemplateEditor">
                <label className="formField" htmlFor="mail-template-subject">
                  <span>主题</span>
                  <input
                    id="mail-template-subject"
                    value={templateForm.subject_template}
                    onFocus={() => setActiveTemplateField("subject_template")}
                    onChange={(event) => updateTemplate("subject_template", event.target.value)}
                    maxLength={200}
                    required
                  />
                </label>
                <label className="formField" htmlFor="mail-template-html">
                  <span>HTML 模板</span>
                  <textarea
                    id="mail-template-html"
                    value={templateForm.html_template}
                    onFocus={() => setActiveTemplateField("html_template")}
                    onChange={(event) => updateTemplate("html_template", event.target.value)}
                    spellCheck={false}
                    required
                  />
                </label>
                <div className="mailTemplateTokens" aria-label="可用占位符">
                  <div>
                    <strong>可用占位符</strong>
                    <span>点击会追加到当前编辑框，发送时由系统替换。</span>
                  </div>
                  <div className="mailTemplateTokenList">
                    {activeTemplate.placeholders.map((placeholder) => (
                      <button
                        key={placeholder.token}
                        type="button"
                        title={`${placeholder.label}，示例：${placeholder.example}`}
                        onClick={() => appendTemplateToken(placeholder.token)}
                        disabled={templateBusy}
                      >
                        {placeholder.token}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <div className="mailTemplatePreviewPanel">
                <div className="mailTemplatePreviewHeading">
                  <div>
                    <strong>实时预览</strong>
                    <span>{previewTemplate.data?.subject ?? "点击预览后显示发送主题"}</span>
                  </div>
                  <Eye aria-hidden="true" />
                </div>
                {previewTemplate.data ? (
                  <iframe
                    title="邮件模板预览"
                    className="mailTemplatePreviewFrame"
                    sandbox=""
                    referrerPolicy="no-referrer"
                    srcDoc={previewTemplate.data.html}
                  />
                ) : (
                  <div className="mailTemplatePreviewEmpty">填写后点击“预览 / 刷新”查看效果</div>
                )}
                <small>预览由服务端使用示例验证码生成，并在禁用脚本的沙箱中显示。</small>
              </div>
            </div>

            {templateNotice ? (
              <div className="mailSettingsMessage success" role="status">
                <CircleCheck aria-hidden="true" />
                <span>{templateNotice}</span>
              </div>
            ) : null}
            {templateError ? (
              <div className="mailSettingsMessage error" role="alert">
                <CircleAlert aria-hidden="true" />
                <span>{templateError}</span>
              </div>
            ) : null}

            <div className="mailTemplateActions">
              <small>
                {activeTemplate.updated_at
                  ? `最近保存：${formatDate(activeTemplate.updated_at)}`
                  : "当前使用官方默认模板"}
              </small>
              <div className="mailSettingsButtons">
                <button
                  className="secondaryButton"
                  type="button"
                  disabled={templateBusy}
                  onClick={() =>
                    previewTemplate.mutate({ eventKey: templateEventKey, input: templateForm })
                  }
                >
                  <Eye aria-hidden="true" />
                  {previewTemplate.isPending ? "生成中…" : "预览 / 刷新"}
                </button>
                <button
                  className="secondaryButton"
                  type="button"
                  disabled={templateBusy || activeTemplate.is_default}
                  onClick={() => restoreTemplate.mutate(templateEventKey)}
                >
                  <RotateCcw aria-hidden="true" />
                  恢复官方模板
                </button>
                <button
                  className="primaryButton"
                  type="button"
                  disabled={templateBusy}
                  onClick={() => saveTemplate.mutate({ eventKey: templateEventKey, input: templateForm })}
                >
                  <Save aria-hidden="true" />
                  {saveTemplate.isPending ? "保存中…" : "保存模板"}
                </button>
              </div>
            </div>
          </>
        ) : null}
      </section>
    </div>
  )
}
