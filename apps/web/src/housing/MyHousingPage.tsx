import { useQuery } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"
import { useEffect, useState } from "react"

import { useAuth } from "../auth/AuthProvider"
import type { HousingPortalStay } from "../portal/api"
import { getMyHousing, portalKeys } from "../portal/api"
import { usePortalAccessError } from "../portal/usePortalAccessError"
import { guardedPortalQueryOptions } from "../portal/usePortalContext"
import { formatShanghaiDate } from "../teaching/datetime"

import "./housing.css"

const PURPOSE_LABELS = {
  permanent: "常住",
  camp: "随营",
  temporary: "临时住宿",
} as const

function statusLabel(stay: HousingPortalStay, asOf: string): string {
  if (stay.status === "checked_in") return "已入住"
  if (stay.status === "checkout_pending") return "待确认退房"
  return Date.parse(stay.planned_check_in_at) <= Date.parse(asOf)
    ? "待确认入住"
    : "已预约"
}

const TIME_FIELDS: ReadonlyArray<{
  label: string
  value: (stay: HousingPortalStay) => string | null
}> = [
  { label: "计划入住", value: (stay) => stay.planned_check_in_at },
  { label: "计划离开", value: (stay) => stay.planned_check_out_at },
  { label: "实际入住", value: (stay) => stay.actual_check_in_at },
  { label: "实际离开", value: (stay) => stay.actual_check_out_at },
]

function StayCard({
  stay,
  asOf,
  planned,
}: {
  stay: HousingPortalStay
  asOf: string
  planned: boolean
}) {
  const timeRows = TIME_FIELDS.flatMap(({ label, value }) => {
    const timestamp = value(stay)
    return timestamp ? [{ label, timestamp }] : []
  })

  return (
    <article className="myHousingStayCard">
      <header className="myHousingStayHeader">
        <div className="myHousingStayLocation">
          <p>{stay.building_name} · {stay.floor_name}</p>
          <h4>{stay.room_name}</h4>
        </div>
        <span className={`myHousingStatus myHousingStatus--${stay.status}`}>
          {statusLabel(stay, asOf)}
        </span>
      </header>

      <dl className="myHousingStayFacts">
        <div>
          <dt>住宿类型</dt>
          <dd>{PURPOSE_LABELS[stay.stay_purpose]}</dd>
        </div>
        <div>
          <dt>楼层</dt>
          <dd>{stay.floor_name}</dd>
        </div>
      </dl>

      <dl className="myHousingTimeGrid">
        {timeRows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd>{formatShanghaiDate(row.timestamp)}</dd>
          </div>
        ))}
      </dl>

      <section className="myHousingRoommates" aria-label="室友信息">
        <h5>室友</h5>
        {planned || stay.roommates === null ? (
          <p>实际入住后可查看</p>
        ) : stay.roommates.length === 0 ? (
          <p>暂无室友</p>
        ) : (
          <ul>
            {stay.roommates.map((roommate, index) => (
              <li key={`${roommate}-${index}`}>{roommate}</li>
            ))}
          </ul>
        )}
      </section>
    </article>
  )
}

function PageHeader() {
  return (
    <header className="myHousingPageHeader">
      <p className="pageEyebrow">HOUSING</p>
      <h1>住宿信息</h1>
      <p>查看当前住宿、室友和已安排的入住预约</p>
    </header>
  )
}

export function MyHousingPage() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const [selectedSubjectId, setSelectedSubjectId] = useState<string | null>(null)
  const housing = useQuery({
    queryKey: portalKeys.housing(accountId),
    queryFn: ({ signal }) => getMyHousing(signal),
    enabled: Boolean(user?.account_id),
    ...guardedPortalQueryOptions,
  })
  const accessError = usePortalAccessError(housing.error)
  const housingData = housing.data
  const subjects = housingData?.subjects ?? []

  useEffect(() => {
    if (subjects.length === 0) {
      setSelectedSubjectId(null)
      return
    }
    if (!subjects.some((item) => item.subject_person_id === selectedSubjectId)) {
      setSelectedSubjectId(subjects[0].subject_person_id)
    }
  }, [selectedSubjectId, subjects])

  if (accessError.permissionRevoked) return null

  const selectedSubject =
    subjects.find((item) => item.subject_person_id === selectedSubjectId) ?? null

  return (
    <div className="myHousingPage">
      <PageHeader />

      {housing.isFetching || (housingData && subjects.length > 0 && !selectedSubject) ? (
        <section className="portalLoading" aria-live="polite">
          <span className="portalLoadingBar" />
          <span className="portalLoadingBar short" />
          <p>正在更新住宿信息</p>
        </section>
      ) : housing.error ? (
        <section className="portalError" role="alert">
          <div>
            <strong>住宿信息暂时无法读取</strong>
            <p>{accessError.message}</p>
          </div>
          <button type="button" onClick={() => void housing.refetch()}>
            <RefreshCw aria-hidden="true" />
            重试
          </button>
        </section>
      ) : housingData && subjects.length === 0 ? (
        <section className="portalEmpty" aria-label="暂无可查看学员">
          <h2>暂无可查看学员</h2>
          <p>当前账号没有可查看的住宿对象。</p>
        </section>
      ) : selectedSubject && housingData ? (
        <div className="myHousingBody">
          <header className="myHousingSubjectHeader">
            <div>
              <p>当前查看</p>
              <h2>{selectedSubject.subject_name}</h2>
            </div>
            {subjects.length > 1 ? (
              <label className="myHousingSubjectPicker">
                <span>当前学员</span>
                <select
                  aria-label="当前学员"
                  value={selectedSubject.subject_person_id}
                  onChange={(event) => setSelectedSubjectId(event.target.value)}
                >
                  {subjects.map((subject) => (
                    <option
                      key={subject.subject_person_id}
                      value={subject.subject_person_id}
                    >
                      {subject.subject_name}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </header>

          {!selectedSubject.current_stay &&
          selectedSubject.planned_stays.length === 0 ? (
            <section className="portalEmpty" aria-label="暂无住宿安排">
              <h3>暂无住宿安排</h3>
              <p>当前没有已入住记录或待入住预约。</p>
            </section>
          ) : (
            <>
              {selectedSubject.current_stay ? (
                <section className="myHousingCurrent">
                  <div className="myHousingSectionHeading">
                    <h3>当前住宿</h3>
                  </div>
                  <StayCard
                    stay={selectedSubject.current_stay}
                    asOf={housingData.as_of}
                    planned={false}
                  />
                </section>
              ) : null}

              {selectedSubject.planned_stays.length > 0 ? (
                <section className="myHousingPlanned">
                  <div className="myHousingSectionHeading">
                    <h3>住宿预约</h3>
                    <span>{selectedSubject.planned_stays.length} 项</span>
                  </div>
                  <div className="myHousingPlannedGrid">
                    {selectedSubject.planned_stays.map((plannedStay, index) => (
                      <StayCard
                        key={`${plannedStay.building_name}-${plannedStay.floor_name}-${plannedStay.room_name}-${plannedStay.planned_check_in_at}-${index}`}
                        stay={plannedStay}
                        asOf={housingData.as_of}
                        planned
                      />
                    ))}
                  </div>
                </section>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}
