import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, Monitor, Palette } from "lucide-react"
import { useEffect, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { useAppearance } from "./AppearanceProvider"
import { getSystemAppearance, saveSystemAppearance } from "./api"
import type { Appearance } from "./store"

const themes = [
  { id: "clear", name: "清爽专业", detail: "明亮留白 · 青绿主色 · 舒展布局" },
  { id: "command", name: "深蓝效率", detail: "深蓝导航 · 蓝色主色 · 紧凑布局" },
] as const

function message(error: unknown): string {
  return error instanceof Error ? error.message : "系统外观暂不可用，请重试。"
}

function ThemePreview({ theme }: { theme: Appearance["theme"] }) {
  return <div className={`appearancePreview appearancePreview--${theme}`} aria-hidden="true">
    <aside><b>竞学 ERP</b><span className="isActive">工作台</span><span>人员管理</span><span>住宿管理</span><span>教学管理</span></aside>
    <div className="appearancePreviewCanvas">
      <div className="appearancePreviewTop"><b>工作台</b><i /></div>
      <div className="appearancePreviewStats"><span><small>在营学员</small><b>128</b></span><span><small>可用床位</small><b>36</b></span></div>
      <div className="appearancePreviewTable"><b>今日事项</b><span><i />学员资料核对<em>待处理</em></span><span><i />住宿信息确认<em>进行中</em></span></div>
    </div>
  </div>
}

export function SystemAppearancePanel() {
  const auth = useOptionalAuth()
  const canManage = auth?.status === "authenticated" && auth.user?.is_super_admin === true &&
    auth.user.permissions.includes("operations.appearance.manage")
  if (!canManage || !auth.user) return null
  return <AppearanceEditor key={auth.user.account_id} accountId={auth.user.account_id} />
}

function AppearanceEditor({ accountId }: { accountId: string }) {
  const { authoritativeAppearance, store } = useAppearance()
  const client = useQueryClient()
  const queryKey = ["operations", "appearance", accountId]
  const configuration = useQuery({
    queryKey,
    queryFn: ({ signal }) => getSystemAppearance(signal),
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 0,
  })
  useEffect(() => {
    if (configuration.data) store.accept(configuration.data)
  }, [configuration.data, store])
  const [draft, setDraft] = useState<Appearance["theme"] | null>(null)
  const [conflict, setConflict] = useState(false)
  const mutation = useMutation({
    mutationFn: ({ theme, revision }: Appearance) => saveSystemAppearance(theme, revision),
    onSuccess: (next) => {
      store.accept(next)
      client.setQueryData(queryKey, next)
      setDraft(null)
      setConflict(false)
    },
    onError: (error) => {
      setConflict(error instanceof ProblemDetailsError && error.problem.status === 409)
    },
  })
  // The operations read gates editing; accepted public updates may then advance
  // its baseline. Startup cache hints are deliberately excluded from this merge.
  const current = configuration.data && authoritativeAppearance &&
    authoritativeAppearance.revision > configuration.data.revision
    ? authoritativeAppearance
    : configuration.data
  const selection = draft ?? current?.theme
  const dirty = current !== undefined && selection !== current.theme
  const pending = mutation.isPending || configuration.isFetching

  async function reload() {
    const result = await configuration.refetch()
    if (result.isSuccess) {
      setDraft(null)
      setConflict(false)
      mutation.reset()
    }
  }

  return <section className="operationsPanel systemAppearancePanel" aria-labelledby="system-appearance-title" aria-busy={pending}>
    <header className="systemAppearanceHeading">
      <div><Palette aria-hidden="true" /><h2 id="system-appearance-title">系统外观</h2></div>
      <span><Monitor aria-hidden="true" />全体用户统一使用</span>
    </header>
    <p className="systemAppearanceDescription">仅超级管理员可发布。选择只预览卡片样式，保存后全体用户跟随；在线页面会自动同步。</p>
    {configuration.isPending ? <p role="status">正在读取系统外观…</p> : null}
    {configuration.isError ? <div className="systemAppearanceError" role="alert">{message(configuration.error)}</div> : null}
    {current ? <>
      <fieldset className="appearanceChoices" disabled={pending}>
        <legend className="visuallyHidden">选择全局外观</legend>
        {themes.map((theme) => <label key={theme.id} className={`appearanceChoice${selection === theme.id ? " isSelected" : ""}`}>
          <ThemePreview theme={theme.id} />
          <span className="appearanceChoiceLabel"><input type="radio" name="system-appearance" value={theme.id} checked={selection === theme.id} onChange={() => { setDraft(theme.id); mutation.reset() }} /><strong>{theme.name}</strong>{current.theme === theme.id ? <small><CheckCircle2 aria-hidden="true" />当前全局</small> : null}</span>
          <span className="appearanceChoiceDetail">{theme.detail}</span>
        </label>)}
      </fieldset>
      <div className="systemAppearanceActions">
        <button type="button" className="primaryButton" disabled={!dirty || pending || conflict} onClick={() => mutation.mutate({ theme: selection!, revision: current.revision })}>{mutation.isPending ? "正在保存…" : "保存全局外观"}</button>
        <button type="button" disabled={!dirty || pending} onClick={() => { setDraft(null); mutation.reset() }}>取消</button>
        <span role="status">{mutation.isSuccess ? "已保存，全体用户将自动同步。" : dirty ? "尚未保存，仅卡片预览" : `当前已保存 · 版本 ${current.revision}`}</span>
      </div>
    </> : null}
    {mutation.isError ? <div className="systemAppearanceError" role="alert">{conflict ? "配置已被其他管理员更新。请重新读取配置，再确认并保存。" : message(mutation.error)}</div> : null}
    {configuration.isError || conflict ? <button type="button" className="systemAppearanceRetry" disabled={pending} onClick={() => void reload()}>重新读取配置</button> : null}
  </section>
}
