"""Stable, secret-free authorization failures shared with the private runner."""

from app.core.errors import ProblemError

ERROR_DETAILS = {
    "cloud_app_unconfigured": "请先配置自有开发者应用",
    "cloud_connector_unavailable": "ERP 管理的连接器未安装或不可达",
    "cloud_runner_offline": "授权运行器离线，请联系管理员",
    "cloud_maintenance_active": "连接器正在维护或备份，请稍后重试",
    "cloud_auth_not_found": "授权会话或操作不存在",
    "cloud_auth_revision_changed": "授权配置版本已改变，请刷新后重试",
    "cloud_auth_state_invalid": "当前授权状态不允许此操作",
    "cloud_auth_operation_conflict": "操作标识已用于其他请求，请重新操作",
    "cloud_auth_live": "此网盘已有未结束的授权会话",
    "cloud_auth_rate_limited": "创建授权过于频繁，请稍后重试",
    "cloud_auth_expired": "授权已过期，请重新扫码",
    "cloud_auth_qr_unavailable": "二维码尚未就绪或已失效",
    "cloud_credentials_invalid": "授权凭据无法读取，请联系管理员",
    "cloud_auth_failed": "网盘授权操作失败，请重试",
    "cloud_provider_unsupported": "提供商不支持当前授权方式",
    "cloud_auth_denied": "用户已拒绝授权",
    "cloud_provider_throttled": "网盘请求受到限流，请稍后重试",
    "cloud_authorization_invalid": "网盘授权已失效，请重新绑定",
    "cloud_account_unverified": "无法可靠确认网盘账号身份",
    "cloud_directory_denied": "目录不可写，请选择其他目录",
    "cloud_remote_corrupt": "上传读回校验失败，请重试",
    "cloud_target_busy": "备份目标正在执行任务，请稍后重试",
    "cloud_config_changed": "备份配置版本已改变，请重新测试",
    "cloud_worker_timeout": "授权运行器处理超时，请重试",
    "cloud_connector_failed": "连接器操作失败，请联系管理员",
    "cloud_cleanup_pending": "连接器清理尚未完成，请联系管理员核查，现有保护配置将保留",
    "cloud_delivery_unknown": "连接器创建结果未确认，请由管理员暂停连接器后核查，勿重复授权",
    "cloud_directory_delivery_unknown": "目录配置结果未确认，请由管理员暂停连接器后核查，暂不切换目录",
    "cloud_invalid_path": "目录路径无效",
    "cloud_redirect_rejected": "网盘返回非预期跳转，已拒绝请求",
    "cloud_auth_body_too_large": "授权请求超过允许大小",
    "cloud_import_rate_limited": "验证授权过于频繁，请稍后重试",
    "cloud_import_tokens_invalid": "授权令牌格式无效，请重新取得授权结果",
    "cloud_import_credentials_reused": "此授权令牌已被使用，请重新取得未使用的授权结果",
    "cloud_import_account_mismatch": "续期前后网盘账号不一致，请重新授权",
    "cloud_import_checkpoint_invalid": "授权验证记录无效，请重新授权",
    "cloud_refresh_outcome_unknown": "续期结果未确认，请重新取得授权结果，勿重试旧令牌",
}
SAFE_ERROR_CODES = frozenset(ERROR_DETAILS)


def safe_error_code(code: object) -> str:
    return code if isinstance(code, str) and code in SAFE_ERROR_CODES else "cloud_auth_failed"


def auth_problem(
    code: str, status: int = 409, *, retry_after_seconds: int | None = None
) -> ProblemError:
    safe = safe_error_code(code)
    return ProblemError(
        status=status,
        code=safe,
        detail=ERROR_DETAILS[safe],
        retry_after_seconds=retry_after_seconds,
    )
