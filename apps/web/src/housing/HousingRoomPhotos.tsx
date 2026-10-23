import { useEffect, useRef, useState } from "react"
import { ProblemDetailsError } from "../api/client"
import { fetchRoomPhoto } from "./api"
import type { HousingRoomDetail, RoomPhotoSummary } from "./api"
import { HousingDialogShell } from "./HousingDialogShell"

type PhotosProps = {
  detail: HousingRoomDetail
  scope: string
  pending: boolean
  onUpload: (slot: 1 | 2 | 3, file: File, version: number) => Promise<void>
  onDelete: (photoId: string, version: number) => Promise<void>
  onPrivateFailure: (error: unknown) => void
}

function PrivatePhoto({ roomId, photo, variant, onPrivateFailure }: {
  roomId: string; photo: RoomPhotoSummary; variant: "original" | "thumbnail"; onPrivateFailure: PhotosProps["onPrivateFailure"]
}) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const urlRef = useRef<string | null>(null)
  const failureRef = useRef(onPrivateFailure)
  failureRef.current = onPrivateFailure
  useEffect(() => () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current) }, [])
  useEffect(() => {
    let current = true
    void fetchRoomPhoto(roomId, photo.id, variant).then((blob) => {
      if (!current) return
      const next = URL.createObjectURL(blob)
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      urlRef.current = next
      setUrl(next)
      setFailed(false)
    }).catch((caught: unknown) => {
      if (!current) return
      setFailed(true)
      if (caught instanceof ProblemDetailsError && [403, 404].includes(caught.problem.status)) failureRef.current(caught)
    })
    return () => { current = false }
  }, [roomId, photo.id, photo.updated_at, variant])
  return <>{url ? <img src={url} alt={variant === "original" ? "房间照片原图" : `房间照片 ${photo.slot} 缩略图`} /> : null}{failed ? <p role="status">照片加载失败，暂时保留已显示的图片。</p> : !url ? <p role="status">正在加载照片…</p> : null}</>
}

function PhotoSlot({ slot, detail, pending, onUpload, onDelete, onPrivateFailure }: Omit<PhotosProps, "scope"> & { slot: 1 | 2 | 3 }) {
  const photo = detail.photos?.find((item) => item.slot === slot)
  const [file, setFile] = useState<File | null>(null)
  const [version, setVersion] = useState(detail.room.version)
  const [conflict, setConflict] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const photoKey = photo ? JSON.stringify([photo.id, photo.updated_at]) : null
  const [viewer, setViewer] = useState({ photoKey, expanded: false })
  // Reset before committing children: an effect would allow a new original fetch first.
  if (viewer.photoKey !== photoKey) setViewer({ photoKey, expanded: false })
  const expanded = viewer.photoKey === photoKey && viewer.expanded
  const live = useRef(true)
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  async function mutate(operation: () => Promise<void>): Promise<void> {
    if (pending || busy || conflict) return
    setBusy(true)
    setError(null)
    try {
      await operation()
      if (!live.current) return
      setFile(null)
    } catch (caught) {
      if (!live.current) return
      if (caught instanceof ProblemDetailsError && [403, 404].includes(caught.problem.status)) onPrivateFailure(caught)
      else if (caught instanceof ProblemDetailsError && caught.problem.status === 409) {
        setConflict(true)
        setError("房间数据已更新，已保留选中的照片，请重新核对当前房间和照片后确认再操作。")
      } else setError(caught instanceof ProblemDetailsError ? caught.problem.detail : "照片操作失败，已保留原照片和选中文件。")
    } finally { if (live.current) setBusy(false) }
  }
  const disabled = pending || busy
  return <article className="housingPhotoSlot" aria-label={`房间照片位置 ${slot}`}>
    <h3>照片 {slot}</h3>
    {photo ? <><button type="button" className="housingPhotoSlot__preview" aria-label={`放大房间照片 ${slot}`} onClick={() => setViewer({ photoKey, expanded: true })}><PrivatePhoto roomId={detail.room.id} photo={photo} variant="thumbnail" onPrivateFailure={onPrivateFailure} /></button>{expanded ? <HousingDialogShell title={`房间照片 ${slot}`} pending={false} error={null} onClose={() => setViewer({ photoKey, expanded: false })}><PrivatePhoto roomId={detail.room.id} photo={photo} variant="original" onPrivateFailure={onPrivateFailure} /></HousingDialogShell> : null}</> : <p>暂无照片</p>}
    {detail.room.allowed_actions.manage_photos ? <>
      <label>选择第 {slot} 张房间照片<input type="file" aria-label={`选择第 ${slot} 张房间照片`} accept="image/jpeg,image/png,image/webp" disabled={disabled} onChange={(event) => {
        const chosen = event.target.files?.[0]
        event.target.value = ""
        if (!chosen) return
        if (!["image/jpeg", "image/png", "image/webp"].includes(chosen.type) || chosen.size > 10 * 1024 * 1024) { setError("仅支持 JPEG、PNG、WebP，单张不超过 10 MiB；其他格式请先转换。"); return }
        setFile(chosen); setVersion(detail.room.version); setError(null)
      }} /></label>
      {file ? <p>{file.name}</p> : null}
      <button type="button" disabled={disabled || !file || conflict} onClick={() => file && void mutate(() => onUpload(slot, file, version))}>上传第 {slot} 张照片</button>
      {photo ? <button type="button" disabled={disabled || conflict} onClick={() => void mutate(() => onDelete(photo.id, detail.room.version))}>删除第 {slot} 张照片</button> : null}
      {conflict ? <button type="button" disabled={disabled} onClick={() => { setVersion(detail.room.version); setConflict(false); setError(null) }}>已核对当前照片，允许重新提交</button> : null}
    </> : null}
    {error ? <p role="alert">{error}</p> : null}
  </article>
}

export function HousingRoomPhotos({ scope, ...props }: PhotosProps) {
  return <section className="housingRoomPhotos" aria-label="房间照片"><h2>房间照片</h2><p>最多 3 张。支持 JPEG、PNG、WebP，单张不超过 10 MiB。请勿上传证件或他人隐私照片；HEIC 请先转换。</p><div className="housingRoomPhotos__slots">{([1, 2, 3] as const).map((slot) => <PhotoSlot key={`${scope}:${props.detail.room.id}:${slot}`} slot={slot} {...props} />)}</div></section>
}
