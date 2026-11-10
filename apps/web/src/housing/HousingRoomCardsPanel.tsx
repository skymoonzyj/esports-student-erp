import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { getHousingCatalog } from "./api"
import { housingKeys } from "./housingKeys"
import { getRoomCards, saveRoomCard, validRoomCardDates } from "./roomCardsApi"
import type { RoomCard, RoomCardInput } from "./roomCardsApi"
import "./roomCards.css"

type CardDraft = { issued: string; expires: string; version: number; busy: boolean; conflict: boolean; message: string | null; saved: boolean }
function initialDraft(card: RoomCard): CardDraft {
  return { issued: card.issued_on ?? "", expires: card.expires_on ?? "", version: card.version, busy: false, conflict: false, message: null, saved: false }
}

function ResidentCard({ card, draft, updateDraft, isCurrent, onSave, onConflict, onPrivateFailure, refreshing }: {
  card: RoomCard; onSave: (stayId: string, value: RoomCardInput) => Promise<RoomCard>
  onConflict: () => Promise<unknown>; onPrivateFailure: () => void; refreshing: boolean
  draft: CardDraft; updateDraft: (patch: Partial<CardDraft>) => void; isCurrent: () => boolean
}) {
  const { issued, expires, version, busy, conflict, message, saved } = draft
  async function save() {
    if (busy || conflict || !card.can_edit || refreshing) return
    updateDraft({ saved: false })
    if (!validRoomCardDates(issued, expires)) { updateDraft({ message: "请同时填写两个日期，使用四位年份（0001–9999），且到期日不得早于开卡日；或同时清空。" }); return }
    updateDraft({ busy: true, message: null })
    try {
      const updated = await onSave(card.stay_id, { issued_on: issued || null, expires_on: expires || null, expected_version: version })
      if (!isCurrent()) return
      updateDraft({ issued: updated.issued_on ?? "", expires: updated.expires_on ?? "", version: updated.version, saved: true })
    } catch (error) {
      if (!isCurrent()) return
      if (error instanceof ProblemDetailsError && [403, 404].includes(error.problem.status)) { onPrivateFailure(); return }
      if (error instanceof ProblemDetailsError && error.problem.status === 409) {
        updateDraft({ conflict: true, message: "房卡或住宿状态已变化，已保留输入；请核对最新日期后再提交。" })
        await onConflict()
      } else updateDraft({ message: "房卡日期保存失败，已保留输入，请重试。" })
    } finally { if (isCurrent()) updateDraft({ busy: false }) }
  }
  return <article className="housingResidentCard" aria-label={`${card.person_name}的房卡日期`}>
    <h3>{card.person_name}</h3>{card.nickname ? <p>{card.nickname}</p> : null}
    <p className="housingResidentCard__current">{card.issued_on && card.expires_on ? `当前开卡日：${card.issued_on} · 到期日：${card.expires_on}` : "尚未录入房卡日期"}</p>
    {card.can_edit ? <>
      <div className="housingResidentCard__dates">
        <label>开卡日<input type="date" min="0001-01-01" max="9999-12-31" aria-label={`${card.person_name}开卡日`} value={issued} disabled={busy} onChange={(event) => updateDraft({ issued: event.target.value, saved: false })} /></label>
        <label>到期日<input type="date" min="0001-01-01" max="9999-12-31" aria-label={`${card.person_name}到期日`} value={expires} disabled={busy} onChange={(event) => updateDraft({ expires: event.target.value, saved: false })} /></label>
      </div>
      <button type="button" disabled={busy || conflict || refreshing} aria-label={`保存${card.person_name}的房卡日期`} onClick={() => { void save() }}>{busy ? "正在保存…" : "保存房卡日期"}</button>
      {conflict ? <button type="button" disabled={busy || refreshing} aria-label={`已核对${card.person_name}的最新日期，允许重新提交`} onClick={() => updateDraft({ version: card.version, conflict: false, message: null })}>已核对最新日期，允许重新提交</button> : null}
    </> : null}
    {message ? <p role="alert">{message}</p> : null}{saved ? <p role="status">房卡日期已保存</p> : null}
  </article>
}

export function HousingRoomCardsPanel({ roomId }: { roomId: string }) {
  const { user } = useAuth()
  const client = useQueryClient()
  const accountId = user?.account_id ?? "anonymous"
  const authSignature = JSON.stringify([accountId, user?.is_super_admin, [...(user?.permissions ?? [])].sort(), [...(user?.scoped_permissions ?? [])].sort()])
  return <ScopedRoomCards key={`${roomId}:${authSignature}`} authSignature={authSignature} roomId={roomId} accountId={accountId} enabled={Boolean(user?.account_id)} client={client} />
}

function ScopedRoomCards({ roomId, accountId, authSignature, enabled, client }: { roomId: string; accountId: string; authSignature: string; enabled: boolean; client: ReturnType<typeof useQueryClient> }) {
  const live = useRef(true)
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  const [blocked, setBlocked] = useState(false)
  // The room-detail parent already owns mount-time capability revalidation.
  // Join that observer; an initially empty cache still performs its first load.
  const catalog = useQuery({ queryKey: housingKeys.catalog(accountId), queryFn: ({ signal }) => getHousingCatalog(signal), enabled, staleTime: 0, refetchOnMount: false, refetchOnWindowFocus: true })
  const revision = catalog.data?.access_revision ?? "pending"
  const key = [...housingKeys.scope(accountId, revision), "room-cards", roomId, authSignature] as const
  const ready = enabled && catalog.isSuccess && !catalog.isFetching && !catalog.isError && !blocked
  const cards = useQuery({ queryKey: key, queryFn: ({ signal }) => getRoomCards(roomId, signal), enabled: ready, staleTime: 0, gcTime: 0, retry: false, refetchOnWindowFocus: false })
  const scope = `${accountId}:${revision}:${roomId}`
  // Keep drafts outside the hidden private projection, but never across an
  // actual authorization scope/room change. No resident names are stored here.
  const [drafts, setDrafts] = useState<{ scope: string; items: Record<string, CardDraft> }>({ scope, items: {} })
  if (drafts.scope !== scope) setDrafts({ scope, items: {} })
  const currentScope = useRef(scope)
  currentScope.current = scope
  const readyRef = useRef(ready)
  readyRef.current = ready
  function isCurrent() { return live.current && currentScope.current === scope }
  function updateDraft(card: RoomCard, patch: Partial<CardDraft>) {
    if (!isCurrent()) return
    setDrafts((old) => old.scope === scope ? { scope, items: { ...old.items, [card.stay_id]: { ...(old.items[card.stay_id] ?? initialDraft(card)), ...patch } } } : old)
  }
  useEffect(() => {
    const root = [...housingKeys.scope(accountId, revision), "room-cards", roomId, authSignature]
    return () => { void client.cancelQueries({ queryKey: root }); client.removeQueries({ queryKey: root }) }
  }, [accountId, authSignature, client, revision, roomId])
  useEffect(() => {
    if (!ready) void client.cancelQueries({ queryKey: [...housingKeys.scope(accountId, revision), "room-cards", roomId, authSignature] })
  }, [accountId, authSignature, client, ready, revision, roomId])
  function privateFailure() {
    setBlocked(true)
    setDrafts({ scope, items: {} })
    void client.cancelQueries({ queryKey: housingKeys.root(accountId) })
    client.removeQueries({ queryKey: key })
    void client.invalidateQueries({ queryKey: housingKeys.catalog(accountId), exact: true })
  }
  async function save(stayId: string, value: RoomCardInput) {
    const updated = await saveRoomCard(stayId, value)
    if (!live.current || !client.getQueryState(key)) return updated
    client.setQueryData<{ items: RoomCard[] }>(key, (old) => old ? { items: old.items.map((card) => card.stay_id === stayId ? updated : card) } : old)
    void client.invalidateQueries({ queryKey: ["portal"], predicate: (query) => query.queryKey[2] === "room-card-reminders" })
    return updated
  }
  const accessDenied = cards.error instanceof ProblemDetailsError && [403, 404].includes(cards.error.problem.status)
  const catalogDenied = catalog.error instanceof ProblemDetailsError && [403, 404].includes(catalog.error.problem.status)
  useEffect(() => {
    if (accessDenied || catalogDenied) setDrafts({ scope, items: {} })
  }, [accessDenied, catalogDenied, scope])
  const failed = blocked || catalog.isError || accessDenied || (cards.isError && !cards.data)
  const data = ready && !failed ? cards.data : undefined
  return <section className="housingRoomCards" aria-label="住客房卡日期" aria-busy={!failed && (!ready || cards.isPending)}>
    <h2>住客房卡日期</h2><p>仅记录开卡和到期日期，不控制实体房卡或门锁；到期当天仍有效。</p>
    {!failed && cards.isError && cards.data ? <div role="alert"><p>最新房卡日期读取失败，已保留输入；重新读取成功前不能提交。</p><button type="button" onClick={() => { void cards.refetch() }}>重新读取最新房卡日期</button></div> : null}
    {failed ? <div role="alert"><p>房卡信息暂时无法读取或访问权限已变化，请重新加载。</p><button type="button" onClick={() => { void catalog.refetch().then((result) => { if (live.current && result.isSuccess) setBlocked(false) }) }}>重新加载房卡信息</button></div> : !data ? <p role="status">正在读取房卡日期…</p> : data.items.length ? <div className="housingRoomCards__residents">{data.items.map((card) => <ResidentCard key={`${scope}:${card.stay_id}`} card={card} draft={drafts.scope === scope && drafts.items[card.stay_id] || initialDraft(card)} updateDraft={(patch) => updateDraft(card, patch)} isCurrent={isCurrent} refreshing={cards.isFetching || cards.isError} onSave={save} onConflict={async () => { if (readyRef.current) return cards.refetch() }} onPrivateFailure={privateFailure} />)}</div> : <p>暂无当前在住人员</p>}
  </section>
}
