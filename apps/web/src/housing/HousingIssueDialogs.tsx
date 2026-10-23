import { useRef, useState } from "react"
import type { FormEvent, RefObject } from "react"

import type {
  HousingRoomIssue,
  HousingRoomIssueCreate,
  HousingRoomIssueResolve,
} from "./api"
import { HousingDialogShell } from "./HousingDialogShell"

type BaseProps<T> = {
  roomNumber: string
  roomVersion: number
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (input: T) => void
}

export function ReportIssueDialog({
  roomNumber,
  roomVersion,
  pending,
  error,
  focusFallbackRef,
  onClose,
  onSubmit,
}: BaseProps<HousingRoomIssueCreate>) {
  const openingRoomVersion = useRef(roomVersion)
  const [description, setDescription] = useState("")
  const valid = Boolean(description.trim())

  function submit(event: FormEvent): void {
    event.preventDefault()
    if (!valid) return
    onSubmit({
      room_version: openingRoomVersion.current,
      description: description.trim(),
    })
  }

  return (
    <HousingDialogShell
      title={`上报问题 · 房间 ${roomNumber}`}
      pending={pending}
      error={error}
      focusFallbackRef={focusFallbackRef}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <fieldset disabled={pending}>
          <label>
            问题描述
            <textarea
              data-autofocus
              required
              maxLength={1000}
              value={description}
              onChange={(event) => setDescription(event.currentTarget.value)}
            />
          </label>
          <footer>
            <button type="button" onClick={onClose}>取消</button>
            <button type="submit" disabled={!valid}>确认上报</button>
          </footer>
        </fieldset>
      </form>
    </HousingDialogShell>
  )
}

export function ResolveIssueDialog({
  roomNumber,
  roomVersion,
  issue,
  pending,
  error,
  focusFallbackRef,
  onClose,
  onSubmit,
}: BaseProps<HousingRoomIssueResolve> & { issue: HousingRoomIssue }) {
  const openingRoomVersion = useRef(roomVersion)
  const [resolutionNote, setResolutionNote] = useState("")
  const allowed = issue.allowed_actions.resolve
  const valid = Boolean(resolutionNote.trim() && allowed)

  function submit(event: FormEvent): void {
    event.preventDefault()
    if (!valid) return
    onSubmit({
      room_version: openingRoomVersion.current,
      resolution_note: resolutionNote.trim(),
    })
  }

  return (
    <HousingDialogShell
      title={`解决问题 · 房间 ${roomNumber}`}
      pending={pending}
      error={error}
      focusFallbackRef={focusFallbackRef}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <fieldset disabled={pending || !allowed}>
          <dl className="housingIssueSummary">
            <div><dt>问题描述</dt><dd>{issue.description}</dd></div>
          </dl>
          <label>
            处理说明
            <textarea
              data-autofocus
              required
              maxLength={1000}
              value={resolutionNote}
              onChange={(event) => setResolutionNote(event.currentTarget.value)}
            />
          </label>
          <footer>
            <button type="button" onClick={onClose}>取消</button>
            <button type="submit" disabled={!valid}>确认解决</button>
          </footer>
        </fieldset>
      </form>
    </HousingDialogShell>
  )
}
