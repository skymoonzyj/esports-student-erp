from __future__ import annotations

from email.message import EmailMessage
from typing import Any, Self
from uuid import uuid4

import pytest

from app.mail.template_service import (
    DEFAULT_PASSWORD_RESET_HTML,
    DEFAULT_PASSWORD_RESET_SUBJECT,
    DEFAULT_VERIFICATION_HTML,
    DEFAULT_VERIFICATION_SUBJECT,
    MailTemplateError,
    VerificationMailTemplate,
    render_verification_template,
    validate_verification_template,
)
from app.registration.delivery import ConfiguredSmtpVerificationSender


def _template(*, subject: str = DEFAULT_VERIFICATION_SUBJECT, html: str = DEFAULT_VERIFICATION_HTML) -> VerificationMailTemplate:
    return VerificationMailTemplate(subject_template=subject, html_template=html)


def test_verification_template_renders_only_the_supported_placeholders() -> None:
    rendered = render_verification_template(
        _template(),
        site_name="竞学 ERP",
        operation_name="密码重置",
        verification_code="123456",
        expires_in_minutes=10,
    )

    assert rendered.subject == "竞学 ERP 密码重置验证码"
    assert "123456" in rendered.html
    assert "竞学 ERP" in rendered.html
    assert "{{verification_code}}" not in rendered.html
    assert "验证码：123456" in rendered.plain_text


def test_password_reset_default_template_is_distinct() -> None:
    template = _template(
        subject=DEFAULT_PASSWORD_RESET_SUBJECT,
        html=DEFAULT_PASSWORD_RESET_HTML,
    )
    rendered = render_verification_template(
        template,
        site_name="竞学 ERP",
        operation_name="密码重置",
        verification_code="654321",
        expires_in_minutes=10,
    )

    assert rendered.subject == "竞学 ERP 密码重置验证码"
    assert "密码重置验证码" in rendered.html
    assert "654321" in rendered.plain_text


@pytest.mark.parametrize(
    "template, expected",
    [
        (
            _template(subject="{{unknown_value}}"),
            "不支持的占位符",
        ),
        (
            _template(html="<p>没有验证码</p>"),
            "必须包含 {{verification_code}}",
        ),
        (
            _template(html="<script>alert(1)</script>{{verification_code}}"),
            "不能包含脚本",
        ),
        (
            _template(subject="正常\n伪造头"),
            "主题不能为空或包含换行符",
        ),
    ],
)
def test_verification_template_rejects_unsafe_or_incomplete_content(
    template: VerificationMailTemplate,
    expected: str,
) -> None:
    with pytest.raises(MailTemplateError, match=expected):
        validate_verification_template(template)


class _FakeSmtp:
    sent: EmailMessage | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        assert username == "admin@example.com"
        assert password == "authorization-code"

    def send_message(self, message: EmailMessage) -> None:
        type(self).sent = message


def test_smtp_sender_uses_custom_template_for_html_and_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeSmtp.sent = None
    monkeypatch.setattr("app.registration.delivery.smtplib.SMTP_SSL", _FakeSmtp)
    sender = ConfiguredSmtpVerificationSender(
        host="smtp.example.com",
        port=465,
        username="admin@example.com",
        password="authorization-code",
        sender="admin@example.com",
        from_name="竞学 ERP 通知",
        reply_to="admin@example.com",
        tls_mode="implicit_tls",
        template=_template(
            subject="{{site_name}} - {{operation_name}}",
            html=(
                "<main><h1>{{operation_name}}</h1>"
                "<strong>{{verification_code}}</strong>"
                "<p>{{expires_in_minutes}}</p></main>"
            ),
        ),
    )

    sender.send_code(
        challenge_id=uuid4(),
        destination="student@example.com",
        code="654321",
        purpose="registration",
    )

    message = _FakeSmtp.sent
    assert message is not None
    assert str(message["Subject"]) == "竞学 ERP - 注册邮箱"
    assert message.get_body(preferencelist=("html",)).get_content() == (
        "<main><h1>注册邮箱</h1><strong>654321</strong><p>10</p></main>\n"
    )
    assert "验证码：654321" in message.get_body(preferencelist=("plain",)).get_content()


def test_smtp_sender_uses_password_reset_template_for_password_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeSmtp.sent = None
    monkeypatch.setattr("app.registration.delivery.smtplib.SMTP_SSL", _FakeSmtp)
    sender = ConfiguredSmtpVerificationSender(
        host="smtp.example.com",
        port=465,
        username="admin@example.com",
        password="authorization-code",
        sender="admin@example.com",
        from_name="竞学 ERP 通知",
        reply_to="admin@example.com",
        tls_mode="implicit_tls",
        template=_template(subject="通用 {{verification_code}}", html="<p>通用</p>{{verification_code}}"),
        password_reset_template=_template(
            subject="重置 {{verification_code}}",
            html="<p>密码重置专用</p><strong>{{verification_code}}</strong>",
        ),
    )

    sender.send_code(
        challenge_id=uuid4(),
        destination="student@example.com",
        code="123456",
        purpose="password_reset",
    )

    message = _FakeSmtp.sent
    assert message is not None
    assert str(message["Subject"]) == "重置 123456"
    assert "密码重置专用" in message.get_body(preferencelist=("html",)).get_content()
