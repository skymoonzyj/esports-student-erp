import { useQuery } from "@tanstack/react-query"

import { listAssetCategories } from "./api"
import { assetKeys } from "./keys"

export type AssetCategorySelectProps = {
  id: string
  value: string
  originalValue?: string
  onChange: (value: string) => void
  mode: "create" | "edit" | "filter"
  accountId: string | null
  authorized: boolean
  active?: boolean
  disabled?: boolean
}

export function AssetCategorySelect({ id, value, originalValue, onChange, mode, accountId, authorized, active = true, disabled = false }: AssetCategorySelectProps) {
  const allowed = Boolean(accountId && authorized && active)
  const catalog = useQuery({
    queryKey: assetKeys.categories(accountId),
    queryFn: listAssetCategories,
    enabled: allowed,
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
  const groups = allowed ? catalog.data?.groups ?? [] : []
  const legacyCategories = allowed && mode === "filter" ? catalog.data?.legacy_categories ?? [] : []
  const inPresets = groups.some((group) => group.options.includes(value))
  const inHistory = legacyCategories.includes(value)
  const showOriginal = Boolean(mode === "edit" && originalValue && !groups.some((group) => group.options.includes(originalValue)))
  const showCurrent = Boolean(value && !inPresets && !inHistory && mode !== "create" && (!showOriginal || value !== originalValue))

  return <>
    <select id={id} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
      <option value="">{mode === "filter" ? "全部分类" : "暂不分类"}</option>
      {groups.map((group) => <optgroup key={group.key} label={group.label}>
        {group.options.map((option) => <option key={option} value={option}>{option}</option>)}
      </optgroup>)}
      {legacyCategories.length > 0 ? <optgroup label="历史分类">
        {legacyCategories.map((category) => <option key={category} value={category}>{category}</option>)}
      </optgroup> : null}
      {showOriginal ? <optgroup label="原始历史分类"><option value={originalValue}>{originalValue}</option></optgroup> : null}
      {showCurrent ? <optgroup label={mode === "edit" ? "当前历史分类" : "当前分类"}><option value={value}>{value}</option></optgroup> : null}
    </select>
    {allowed && catalog.isError ? <p className="formError" role="alert">分类暂时无法读取，当前选择已保留。<button type="button" onClick={() => void catalog.refetch()}>重试读取分类</button></p> : null}
  </>
}

