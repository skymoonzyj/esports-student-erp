import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import {
  createFoodCuisine,
  getFoodPreferenceCatalog,
  patchFoodCuisine,
  type CuisineOption,
} from "./api"
import { foodPreferenceKeys } from "./keys"
import "./food-preferences.css"

type Props = { onAccessRevoked?: () => void }

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function isPrivateFailure(error: unknown): boolean {
  return error instanceof ProblemDetailsError &&
    (error.problem.status === 403 || error.problem.status === 404)
}

function CatalogRow({
  item,
  canEdit,
  canToggle,
  onEdit,
  onToggle,
}: {
  item: CuisineOption
  canEdit: boolean
  canToggle: boolean
  onEdit: () => void
  onToggle: () => void
}) {
  return (
    <li className="foodCuisineSettings__item" aria-label={item.name}>
      <div>
        <strong>{item.name}</strong>
        {item.is_other ? <span className="foodCuisineSettings__tag">系统保留</span> : null}
      </div>
      <span className="foodCuisineSettings__sort">排序 {item.sort_order}</span>
      {(canEdit || canToggle) && !item.is_other ? (
        <div className="foodCuisineSettings__rowActions">
          {canEdit ? <button type="button" onClick={onEdit}>编辑</button> : null}
          {canToggle ? <button type="button" onClick={onToggle}>{item.is_active ? "停用" : "恢复"}</button> : null}
        </div>
      ) : null}
    </li>
  )
}

function CuisineDialog({
  item,
  pending,
  onClose,
  onSubmit,
}: {
  item: CuisineOption | null
  pending: boolean
  onClose: () => void
  onSubmit: (value: { name: string; sort_order: number }) => void
}) {
  const [name, setName] = useState(item?.name ?? "")
  const [sortOrder, setSortOrder] = useState(String(item?.sort_order ?? 0))
  const parsedSort = Number(sortOrder)
  const valid = name.trim().length > 0 && name.trim().length <= 40 && Number.isInteger(parsedSort) && parsedSort >= 0
  return (
    <div className="foodCuisineSettings__backdrop" role="presentation">
      <section className="foodCuisineSettings__dialog" role="dialog" aria-modal="true" aria-labelledby="food-cuisine-dialog-title">
        <header><h2 id="food-cuisine-dialog-title">{item ? "编辑菜系" : "新增菜系"}</h2><button type="button" aria-label="关闭菜系窗口" onClick={onClose} disabled={pending}>×</button></header>
        <label>菜系名称<input aria-label="菜系名称" value={name} maxLength={40} onChange={(event) => setName(event.target.value)} /></label>
        <label>排序<input aria-label="排序" type="number" min={0} value={sortOrder} onChange={(event) => setSortOrder(event.target.value)} /></label>
        <footer><button type="button" onClick={onClose} disabled={pending}>取消</button><button type="button" onClick={() => onSubmit({ name: name.trim(), sort_order: parsedSort })} disabled={!valid || pending}>{pending ? "保存中…" : "保存"}</button></footer>
      </section>
    </div>
  )
}

export function FoodCuisineSettingsPage({ onAccessRevoked }: Props) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const accountId = user?.account_id ?? "anonymous"
  const revokedRef = useRef(false)
  const notifiedRef = useRef(false)
  const [accessRevoked, setAccessRevoked] = useState(false)
  const [dialogItem, setDialogItem] = useState<CuisineOption | null | undefined>(undefined)
  const [mutationError, setMutationError] = useState<string | null>(null)
  const catalog = useQuery({
    queryKey: foodPreferenceKeys.catalog(accountId),
    queryFn: ({ signal }) => getFoodPreferenceCatalog(true, signal),
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnMount: false,
    retry: false,
  })
  const data = catalog.data
  const canCreate = data?.allowed_actions.includes("create") === true
  const canUpdate = data?.allowed_actions.includes("update") === true
  const canDisable = data?.allowed_actions.includes("disable") === true
  const active = useMemo(() => data?.items.filter((item) => item.is_active) ?? [], [data])
  const inactive = useMemo(() => data?.items.filter((item) => !item.is_active) ?? [], [data])
  const createMutation = useMutation({
    mutationFn: (value: { name: string; sort_order: number }) => createFoodCuisine(value),
  })
  const patchMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: { name?: string; sort_order?: number; is_active?: boolean } }) => patchFoodCuisine(id, input),
  })
  const pending = createMutation.isPending || patchMutation.isPending

  const revokeAccess = useCallback(() => {
    if (revokedRef.current) return
    revokedRef.current = true
    setDialogItem(undefined)
    setMutationError(null)
    createMutation.reset()
    patchMutation.reset()
    const queryKey = foodPreferenceKeys.accountRoot(accountId)
    const cancellation = queryClient.cancelQueries({ queryKey })
    queryClient.removeQueries({ queryKey })
    setAccessRevoked(true)
    void cancellation.finally(() => queryClient.removeQueries({ queryKey }))
  }, [accountId, createMutation, patchMutation, queryClient])

  useEffect(() => {
    if (!accessRevoked || notifiedRef.current) return
    notifiedRef.current = true
    onAccessRevoked?.()
  }, [accessRevoked, onAccessRevoked])

  useEffect(() => {
    if (isPrivateFailure(catalog.error)) revokeAccess()
  }, [catalog.error, revokeAccess])

  function invalidate(): void {
    void queryClient.invalidateQueries({ queryKey: foodPreferenceKeys.catalog(accountId) })
  }
  async function save(value: { name: string; sort_order: number }): Promise<void> {
    setMutationError(null)
    try {
      if (dialogItem) await patchMutation.mutateAsync({ id: dialogItem.id, input: value })
      else await createMutation.mutateAsync(value)
      setDialogItem(undefined)
      invalidate()
    } catch (error) {
      if (isPrivateFailure(error)) revokeAccess()
      else setMutationError(errorMessage(error, "保存菜系失败，请稍后重试"))
    } finally {
      createMutation.reset()
      patchMutation.reset()
    }
  }
  async function toggle(item: CuisineOption): Promise<void> {
    setMutationError(null)
    try {
      await patchMutation.mutateAsync({ id: item.id, input: { is_active: !item.is_active } })
      invalidate()
    } catch (error) {
      if (isPrivateFailure(error)) revokeAccess()
      else setMutationError(errorMessage(error, "更新菜系状态失败，请稍后重试"))
    } finally {
      patchMutation.reset()
    }
  }

  return (
    <main className="foodCuisineSettingsPage">
      <header className="foodCuisineSettings__header"><div><p className="pageEyebrow">系统设置 · 敏感权限</p><h1>菜系字典管理</h1><p>维护餐饮问卷中的菜系选项；停用只影响后续选择，不删除历史记录。</p></div>{canCreate ? <button type="button" onClick={() => { setMutationError(null); setDialogItem(null) }}>新增菜系</button> : null}</header>
      {mutationError ? <p className="foodCuisineSettings__error" role="alert">{mutationError}</p> : null}
      {catalog.isPending ? <section className="foodCuisineSettings__state"><p>正在读取菜系字典…</p></section> : null}
      {catalog.isError ? <section className="foodCuisineSettings__state foodCuisineSettings__state--error" role="alert"><strong>无法读取菜系字典</strong><p>{errorMessage(catalog.error, "请稍后重试")}</p><button type="button" onClick={() => void catalog.refetch()}>重试</button></section> : null}
      {data ? <>
        <section className="foodCuisineSettings__region" aria-label="启用菜系"><header><h2>启用菜系</h2><span>{active.length} 项</span></header><ul>{active.map((item) => <CatalogRow key={item.id} item={item} canEdit={canUpdate} canToggle={canDisable} onEdit={() => setDialogItem(item)} onToggle={() => void toggle(item)} />)}</ul></section>
        <section className="foodCuisineSettings__region" aria-label="停用菜系"><header><h2>停用菜系</h2><span>{inactive.length} 项</span></header>{inactive.length ? <ul>{inactive.map((item) => <CatalogRow key={item.id} item={item} canEdit={canUpdate} canToggle={canDisable} onEdit={() => setDialogItem(item)} onToggle={() => void toggle(item)} />)}</ul> : <p className="foodCuisineSettings__muted">暂无停用菜系</p>}</section>
      </> : null}
      {dialogItem !== undefined ? <CuisineDialog item={dialogItem} pending={pending} onClose={() => { if (!pending) setDialogItem(undefined) }} onSubmit={(value) => void save(value)} /> : null}
    </main>
  )
}
