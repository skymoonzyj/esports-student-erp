from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.mail.models import MailTemplate
from app.mail.settings_schemas import (
    MailTemplateEventKey,
    MailTemplatePlaceholder,
    MailTemplatePreviewResponse,
    MailTemplateResponse,
    MailTemplateUpdate,
)

VERIFICATION_CODE_EVENT_KEY: Final = "verification_code"
PASSWORD_RESET_EVENT_KEY: Final = "password_reset"
_TOKEN_PATTERN: Final = re.compile(r"{{\s*([^{}]+?)\s*}}")
_VALID_TOKEN_NAME: Final = re.compile(r"^[a-z_]+$")
_SUPPORTED_TOKENS: Final = {
    "site_name": MailTemplatePlaceholder(
        token="{{site_name}}",
        label="站点名称",
        example="竞学 ERP",
    ),
    "operation_name": MailTemplatePlaceholder(
        token="{{operation_name}}",
        label="操作名称",
        example="注册邮箱",
    ),
    "verification_code": MailTemplatePlaceholder(
        token="{{verification_code}}",
        label="验证码",
        example="123456",
    ),
    "expires_in_minutes": MailTemplatePlaceholder(
        token="{{expires_in_minutes}}",
        label="有效分钟数",
        example="10",
    ),
}
_PROHIBITED_TAGS: Final = frozenset({"base", "embed", "form", "iframe", "meta", "object", "script"})

DEFAULT_VERIFICATION_SUBJECT: Final = "{{site_name}} {{operation_name}}验证码"
DEFAULT_VERIFICATION_HTML: Final = """<!doctype html>
<html lang="zh-CN">
  <head></head>
  <body style="margin:0;padding:24px;background:#f4f7f8;color:#17212b;font-family:Arial,'PingFang SC','Microsoft YaHei',sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr><td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:8px;overflow:hidden;">
          <tr><td style="padding:26px 30px;background:#0f9f8f;color:#ffffff;font-size:24px;font-weight:700;">{{operation_name}}验证码</td></tr>
          <tr><td style="padding:32px 30px;font-size:16px;line-height:1.7;">
            <p style="margin:0 0 18px;">你正在进行 {{site_name}} 的{{operation_name}}操作。</p>
            <p style="margin:0 0 12px;">你的验证码是：</p>
            <p style="margin:0 0 24px;font-size:32px;letter-spacing:8px;font-weight:700;color:#0c1f2e;">{{verification_code}}</p>
            <p style="margin:0 0 12px;">验证码将在 <strong>{{expires_in_minutes}}</strong> 分钟后失效。</p>
            <p style="margin:0;">如非本人操作，请忽略本邮件。</p>
          </td></tr>
          <tr><td style="padding:18px 30px;background:#f7f9fa;color:#6d7882;font-size:12px;">此邮件由 {{site_name}} 自动发送，请勿直接回复。</td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""
DEFAULT_PASSWORD_RESET_SUBJECT: Final = "{{site_name}} 密码重置验证码"
DEFAULT_PASSWORD_RESET_HTML: Final = """<!doctype html>
<html lang="zh-CN">
  <head></head>
  <body style="margin:0;padding:24px;background:#f4f7f8;color:#17212b;font-family:Arial,'PingFang SC','Microsoft YaHei',sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr><td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:8px;overflow:hidden;">
          <tr><td style="padding:26px 30px;background:#0c1f2e;color:#ffffff;font-size:24px;font-weight:700;">密码重置验证码</td></tr>
          <tr><td style="padding:32px 30px;font-size:16px;line-height:1.7;">
            <p style="margin:0 0 18px;">你正在进行 {{site_name}} 的密码重置操作。</p>
            <p style="margin:0 0 12px;">你的密码重置验证码是：</p>
            <p style="margin:0 0 24px;font-size:32px;letter-spacing:8px;font-weight:700;color:#0c1f2e;">{{verification_code}}</p>
            <p style="margin:0 0 12px;">验证码将在 <strong>{{expires_in_minutes}}</strong> 分钟后失效。</p>
            <p style="margin:0;">如非本人操作，请立即修改密码并忽略本邮件。</p>
          </td></tr>
          <tr><td style="padding:18px 30px;background:#f7f9fa;color:#6d7882;font-size:12px;">此邮件由 {{site_name}} 自动发送，请勿直接回复。</td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""

_EVENT_DEFINITIONS: Final = {
    VERIFICATION_CODE_EVENT_KEY: {
        "name": "邮箱验证码",
        "description": "注册、监护人邀请和添加登录邮箱时发送。",
        "subject": DEFAULT_VERIFICATION_SUBJECT,
        "html": DEFAULT_VERIFICATION_HTML,
        "operation": "注册邮箱",
    },
    PASSWORD_RESET_EVENT_KEY: {
        "name": "密码重置验证码",
        "description": "重置账号密码时发送。",
        "subject": DEFAULT_PASSWORD_RESET_SUBJECT,
        "html": DEFAULT_PASSWORD_RESET_HTML,
        "operation": "密码重置",
    },
}


class MailTemplateError(ValueError):
    code = "mail_template_error"


@dataclass(frozen=True, slots=True)
class VerificationMailTemplate:
    subject_template: str
    html_template: str


@dataclass(frozen=True, slots=True)
class RenderedVerificationMail:
    subject: str
    html: str
    plain_text: str


class _TemplateSafetyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.error: str | None = None

    def _check(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in _PROHIBITED_TAGS:
            self.error = "HTML 模板不能包含脚本、嵌入页面或表单"
            return
        for name, value in attrs:
            normalized_name = name.lower()
            normalized_value = (value or "").strip().lower()
            if normalized_name.startswith("on"):
                self.error = "HTML 模板不能包含事件脚本"
                return
            if normalized_name in {"href", "src"} and normalized_value.startswith("javascript:"):
                self.error = "HTML 模板不能包含 JavaScript 链接"
                return
            if normalized_name == "style" and "expression(" in normalized_value:
                self.error = "HTML 模板不能包含脚本样式"
                return

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)


def _tokens(value: str) -> list[str]:
    names: list[str] = []
    for match in _TOKEN_PATTERN.finditer(value):
        name = match.group(1).strip()
        if not _VALID_TOKEN_NAME.fullmatch(name) or name not in _SUPPORTED_TOKENS:
            raise MailTemplateError(f"不支持的占位符：{match.group(0)}")
        names.append(name)
    if "{{" in _TOKEN_PATTERN.sub("", value) or "}}" in _TOKEN_PATTERN.sub("", value):
        raise MailTemplateError("占位符必须使用 {{placeholder_name}} 格式")
    return names


def _validate_html(value: str) -> None:
    if "\x00" in value:
        raise MailTemplateError("HTML 模板不能包含空字符")
    parser = _TemplateSafetyParser()
    try:
        parser.feed(value)
        parser.close()
    except ValueError as exc:
        raise MailTemplateError("HTML 模板格式无效") from exc
    if parser.error is not None:
        raise MailTemplateError(parser.error)


def validate_verification_template(template: VerificationMailTemplate) -> None:
    subject = template.subject_template.strip()
    body = template.html_template.strip()
    if not subject or "\r" in subject or "\n" in subject:
        raise MailTemplateError("邮件主题不能为空或包含换行符")
    if not body:
        raise MailTemplateError("HTML 模板不能为空")
    _tokens(subject)
    body_tokens = _tokens(body)
    if "verification_code" not in body_tokens:
        raise MailTemplateError("HTML 模板必须包含 {{verification_code}} 占位符")
    _validate_html(body)


def _replace_tokens(value: str, *, context: dict[str, str], escape_html: bool) -> str:
    def replacement(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        raw = context[token]
        return html.escape(raw) if escape_html else raw

    return _TOKEN_PATTERN.sub(replacement, value)


def render_verification_template(
    template: VerificationMailTemplate,
    *,
    site_name: str,
    operation_name: str,
    verification_code: str,
    expires_in_minutes: int,
) -> RenderedVerificationMail:
    validate_verification_template(template)
    context = {
        "site_name": site_name,
        "operation_name": operation_name,
        "verification_code": verification_code,
        "expires_in_minutes": str(expires_in_minutes),
    }
    return RenderedVerificationMail(
        subject=_replace_tokens(template.subject_template, context=context, escape_html=False),
        html=_replace_tokens(template.html_template, context=context, escape_html=True),
        plain_text=(
            f"{site_name} {operation_name}验证码\n\n"
            f"你正在进行 {site_name} 的{operation_name}操作。\n\n"
            f"验证码：{verification_code}\n"
            f"验证码有效期为 {expires_in_minutes} 分钟，请勿转发给他人。\n"
            "如非本人操作，请忽略本邮件。\n"
        ),
    )


class MailTemplateService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _definition(event_key: MailTemplateEventKey) -> dict[str, str]:
        try:
            return _EVENT_DEFINITIONS[event_key]
        except KeyError as exc:
            raise MailTemplateError(f"不支持的邮件事件：{event_key}") from exc

    def _row(
        self,
        event_key: MailTemplateEventKey = VERIFICATION_CODE_EVENT_KEY,
        *,
        lock: bool = False,
    ) -> MailTemplate | None:
        self._definition(event_key)
        statement = select(MailTemplate).where(MailTemplate.event_key == event_key)
        if lock:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    @classmethod
    def _default_template(cls, event_key: MailTemplateEventKey = VERIFICATION_CODE_EVENT_KEY) -> VerificationMailTemplate:
        definition = cls._definition(event_key)
        return VerificationMailTemplate(
            subject_template=definition["subject"],
            html_template=definition["html"],
        )

    def template(self, event_key: MailTemplateEventKey = VERIFICATION_CODE_EVENT_KEY) -> VerificationMailTemplate:
        row = self._row(event_key)
        template = (
            self._default_template(event_key)
            if row is None
            else VerificationMailTemplate(
                subject_template=row.subject_template,
                html_template=row.html_template,
            )
        )
        validate_verification_template(template)
        return template

    def verification_template(self) -> VerificationMailTemplate:
        return self.template(VERIFICATION_CODE_EVENT_KEY)

    def password_reset_template(self) -> VerificationMailTemplate:
        return self.template(PASSWORD_RESET_EVENT_KEY)

    def public(self, event_key: MailTemplateEventKey = VERIFICATION_CODE_EVENT_KEY) -> MailTemplateResponse:
        definition = self._definition(event_key)
        row = self._row(event_key)
        template = self.template(event_key)
        return MailTemplateResponse(
            event_key=event_key,
            event_name=definition["name"],
            description=definition["description"],
            subject_template=template.subject_template,
            html_template=template.html_template,
            placeholders=list(_SUPPORTED_TOKENS.values()),
            is_default=row is None,
            updated_at=None if row is None else row.updated_at,
        )

    def preview(
        self,
        input: MailTemplateUpdate,
        event_key: MailTemplateEventKey = VERIFICATION_CODE_EVENT_KEY,
    ) -> MailTemplatePreviewResponse:
        definition = self._definition(event_key)
        template = VerificationMailTemplate(
            subject_template=input.subject_template,
            html_template=input.html_template,
        )
        rendered = render_verification_template(
            template,
            site_name="竞学 ERP",
            operation_name=definition["operation"],
            verification_code="123456",
            expires_in_minutes=10,
        )
        return MailTemplatePreviewResponse(
            subject=rendered.subject,
            html=rendered.html,
            plain_text=rendered.plain_text,
        )

    def save(
        self,
        input: MailTemplateUpdate,
        *,
        event_key: MailTemplateEventKey = VERIFICATION_CODE_EVENT_KEY,
        actor_user_id: UUID,
        request_id: str,
    ) -> MailTemplateResponse:
        template = VerificationMailTemplate(
            subject_template=input.subject_template.strip(),
            html_template=input.html_template.strip(),
        )
        validate_verification_template(template)
        with write_transaction(self.db):
            row = self._row(event_key, lock=True)
            if row is None:
                row = MailTemplate(
                    event_key=event_key,
                    subject_template=template.subject_template,
                    html_template=template.html_template,
                    updated_by_user_id=actor_user_id,
                )
                self.db.add(row)
                self.db.flush()
            else:
                row.subject_template = template.subject_template
                row.html_template = template.html_template
                row.updated_by_user_id = actor_user_id
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="mail.template.update",
                resource_type="mail_template",
                resource_id=row.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={"event_key": event_key},
            )
        return self.public(event_key)

    def restore_default(
        self,
        *,
        event_key: MailTemplateEventKey = VERIFICATION_CODE_EVENT_KEY,
        actor_user_id: UUID,
        request_id: str,
    ) -> MailTemplateResponse:
        with write_transaction(self.db):
            row = self._row(event_key, lock=True)
            if row is not None:
                AuditService(self.db).append(
                    actor_user_id=actor_user_id,
                    action="mail.template.restore_default",
                    resource_type="mail_template",
                    resource_id=row.id,
                    purpose=None,
                    outcome="allowed",
                    request_id=request_id,
                    metadata={"event_key": event_key},
                )
                self.db.delete(row)
        return self.public(event_key)
