import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState, type FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import {
  evaluationFoundationKeys,
  listAllCertificationProjects,
} from "./foundationApi"
import { useEvaluationAuthorization } from "./EvaluationAuthorization"
import {
  captureSystemEvaluationWrite,
  createSystemEvaluationAdapterCertification,
  listSystemEvaluationAdapterCertifications,
  type SystemEvaluationAdapterCertificationResponse,
  type SystemEvaluationWrite,
} from "./systemEvaluationApi"
import { systemEvaluationKeys } from "./systemEvaluationKeys"

import "./system-evaluation.css"

function defaultIdempotencyKey(): string {
  return globalThis.crypto.randomUUID()
}

function certificationErrorMessage(error: unknown): string {
  return error instanceof ProblemDetailsError
    ? error.problem.detail
    : "适配器认证暂时无法保存，请重试。"
}

function shortDigest(value: string): string {
  return `${value.slice(0, 12)}…${value.slice(-8)}`
}

export function AdapterCertificationsPanel({
  idempotencyKeyFactory = defaultIdempotencyKey,
}: {
  idempotencyKeyFactory?: () => string
}) {
  const { user } = useAuth()
  const authorization = useEvaluationAuthorization()
  const queryClient = useQueryClient()
  const accountId = user?.account_id ?? "anonymous"
  const certificationsKey = systemEvaluationKeys.certifications(accountId)
  const projects = useQuery({
    queryKey: [
      ...evaluationFoundationKeys.certificationProjects(accountId),
      "authorization",
      authorization.revision,
    ] as const,
    queryFn: listAllCertificationProjects,
    enabled: Boolean(user?.account_id) && !authorization.pending,
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  })
  const certifications = useQuery({
    queryKey: [...certificationsKey, "authorization", authorization.revision] as const,
    queryFn: listSystemEvaluationAdapterCertifications,
    enabled: Boolean(user?.account_id) && !authorization.pending,
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  })
  const mutation = useMutation<
    SystemEvaluationAdapterCertificationResponse,
    unknown,
    { write: SystemEvaluationWrite; idempotencyKey: string }
  >({
    mutationFn: ({ write, idempotencyKey }) =>
      createSystemEvaluationAdapterCertification(write, idempotencyKey),
    gcTime: 0,
  })
  const [projectId, setProjectId] = useState("")
  const [adapterId, setAdapterId] = useState("")
  const [adapterVersion, setAdapterVersion] = useState("")
  const [ruleVersion, setRuleVersion] = useState("")
  const [schemaVersion, setSchemaVersion] = useState("")
  const [digest, setDigest] = useState("")
  const [message, setMessage] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  if (authorization.pending || projects.isPending || certifications.isPending) {
    return <section className="foundationState">正在更新适配器认证...</section>
  }
  const loadError = projects.error ?? certifications.error
  if (loadError) {
    return (
      <section className="foundationState" role="alert">
        {certificationErrorMessage(loadError)}
      </section>
    )
  }
  if (!projects.data || !certifications.data) {
    return <section className="foundationState">正在更新适配器认证...</section>
  }

  const projectNames = new Map(projects.data.map((project) => [project.id, project.name]))
  const canSubmit =
    !mutation.isPending &&
    Boolean(projectId && adapterId.trim() && adapterVersion.trim()) &&
    Boolean(ruleVersion.trim() && schemaVersion.trim()) &&
    /^[0-9a-f]{64}$/.test(digest)

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!canSubmit) return
    setMessage(null)
    setSuccess(null)
    const write = captureSystemEvaluationWrite({
      kind: "adapter_certification",
      project_id: projectId,
      adapter_id: adapterId.trim(),
      adapter_version: adapterVersion.trim(),
      scoring_rule_version: ruleVersion.trim(),
      result_schema_version: schemaVersion.trim(),
      manifest_digest: digest,
    })
    try {
      await mutation.mutateAsync({
        write,
        idempotencyKey: idempotencyKeyFactory(),
      })
      setDigest("")
      await queryClient.invalidateQueries({ queryKey: certificationsKey })
      setSuccess("适配器认证已登记。")
    } catch (error) {
      setMessage(certificationErrorMessage(error))
    } finally {
      mutation.reset()
    }
  }

  return (
    <section className="adapterCertificationsPanel" role="region" aria-label="适配器认证">
      <header>
        <h2>适配器认证</h2>
        <p>仅认证与 ERP 结果契约一致的评分适配器版本。</p>
      </header>
      <form onSubmit={(event) => void submit(event)}>
        <label>
          电竞项目
          <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
            <option value="">请选择项目</option>
            {projects.data.map((project) => (
              <option key={project.id} value={project.id}>{project.name}</option>
            ))}
          </select>
        </label>
        <label>
          适配器 ID
          <input value={adapterId} onChange={(event) => setAdapterId(event.target.value)} />
        </label>
        <label>
          适配器版本
          <input value={adapterVersion} onChange={(event) => setAdapterVersion(event.target.value)} />
        </label>
        <label>
          评分规则版本
          <input value={ruleVersion} onChange={(event) => setRuleVersion(event.target.value)} />
        </label>
        <label>
          结果模式版本
          <input value={schemaVersion} onChange={(event) => setSchemaVersion(event.target.value)} />
        </label>
        <label>
          清单摘要
          <input
            value={digest}
            minLength={64}
            maxLength={64}
            pattern="[0-9a-f]{64}"
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setDigest(event.target.value)}
          />
        </label>
        {message ? <p role="alert">{message}</p> : null}
        {success ? <p role="status">{success}</p> : null}
        <button type="submit" disabled={!canSubmit}>
          {mutation.isPending ? "正在登记..." : "新增认证"}
        </button>
      </form>

      <div className="adapterCertificationList">
        {certifications.data.items.map((item) => (
          <article key={item.id}>
            <strong>{projectNames.get(item.project_id) ?? "未知项目"}</strong>
            <p>{item.adapter_id} · {item.adapter_version}</p>
            <p>{item.scoring_rule_version} · {item.result_schema_version}</p>
            <code>{shortDigest(item.manifest_digest)}</code>
          </article>
        ))}
      </div>
    </section>
  )
}
