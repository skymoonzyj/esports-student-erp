import { useEffect, useState } from "react"
import { Link } from "react-router-dom"

import { getOwnStaffApplication, getRegistrationMe } from "./api"
import type {
  RegistrationNextAction,
  RegistrationStateResponse,
  StaffApplicationSelf,
  StaffApplicationStatus,
  StaffType,
} from "./api"
import "./registration.css"

const STAFF_TYPE_LABELS: Record<StaffType, string> = {
  coach: "教练",
  life_teacher: "生活老师",
  customer_service: "客服",
  administration: "行政",
  other: "其他岗位",
}

const STAFF_STATUS_LABELS: Record<StaffApplicationStatus, string> = {
  pending: "工作人员申请审核中",
  approved: "工作人员申请已通过",
  rejected: "工作人员申请未通过",
  withdrawn: "工作人员申请已撤回",
}

function RegistrationGuidance({ action }: { action: RegistrationNextAction }) {
  const guidance: Record<RegistrationNextAction, {
    title: string
    body: string
    link?: { to: string; label: string }
  }> = {
    verify_contact: {
      title: "请重新验证联系方式",
      body: "当前验证步骤尚未完成。",
      link: { to: "/register", label: "重新开始验证" },
    },
    complete_profile: {
      title: "请填写实名资料",
      body: "联系方式已验证，请继续完成实名注册。",
      link: { to: "/register", label: "重新进入注册流程" },
    },
    finalize: {
      title: "实名资料已完成",
      body: "账号正在等待最后开通步骤，请稍后刷新状态。",
    },
    complete_guardian: {
      title: "请完成监护人绑定",
      body: "至少一名有效监护人确认关系后，学员账号才能正式开通。",
      link: { to: "/register/guardian", label: "管理监护人邀请" },
    },
    await_review: {
      title: "资料已提交审核",
      body: "管理员正在核对资料，处理完成后可登录或找回密码。",
    },
    login: {
      title: "账号已经开通",
      body: "请使用已验证的手机号或邮箱登录。",
      link: { to: "/login", label: "去登录" },
    },
    login_or_reset: {
      title: "已有账号可使用",
      body: "请登录原账号；忘记密码时可通过已验证联系方式重置。",
      link: { to: "/forgot-password", label: "找回密码" },
    },
    start_again: {
      title: "注册申请已失效",
      body: "请重新发起注册。",
      link: { to: "/register", label: "重新注册" },
    },
    closed: {
      title: "注册流程已结束",
      body: "如需继续，请联系管理员或重新注册。",
      link: { to: "/register", label: "重新注册" },
    },
  }
  const current = guidance[action]
  return (
    <div className="registrationStatus" role="status">
      <strong>{current.title}</strong>
      <span>{current.body}</span>
      {current.link ? <Link to={current.link.to}>{current.link.label}</Link> : null}
    </div>
  )
}

export function RegistrationStatusPage() {
  const [registration, setRegistration] = useState<RegistrationStateResponse | null>(null)
  const [staff, setStaff] = useState<StaffApplicationSelf | null>(null)
  const [loading, setLoading] = useState(true)
  const [unavailable, setUnavailable] = useState(false)

  useEffect(() => {
    let active = true
    void getRegistrationMe()
      .then((result) => {
        if (active) setRegistration(result)
      })
      .catch(async () => {
        try {
          const result = await getOwnStaffApplication()
          if (active) setStaff(result)
        } catch {
          if (active) setUnavailable(true)
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  return (
    <main className="registrationPage">
      <section className="registrationCard" aria-labelledby="registration-status-title">
        <p className="registrationEyebrow">APPLICATION STATUS</p>
        <h1 id="registration-status-title">注册状态</h1>
        {loading ? <p className="registrationLead" role="status">正在确认申请状态…</p> : null}
        {registration ? (
          <>
            <p className="registrationLead">已验证联系方式</p>
            <p className="registrationMaskedValue">{registration.masked_identifier}</p>
            <RegistrationGuidance action={registration.next_action} />
          </>
        ) : null}
        {staff ? (
          <div className="registrationStatus" role="status">
            <strong>{STAFF_STATUS_LABELS[staff.status]}</strong>
            <span>申请岗位：<b>{STAFF_TYPE_LABELS[staff.requested_staff_type]}</b></span>
            <span>{staff.application_reason}</span>
            {staff.status === "approved" ? <Link to="/login">返回工作台</Link> : null}
          </div>
        ) : null}
        {unavailable ? (
          <div className="registrationStatus registrationStatusWarning" role="alert">
            <strong>暂时无法读取申请状态</strong>
            <span>请重新登录、重新开始注册或联系管理员。</span>
            <Link to="/login">返回登录</Link>
          </div>
        ) : null}
      </section>
    </main>
  )
}
