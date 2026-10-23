from uuid import uuid4

from app.registration.delivery import ConfiguredSmtpVerificationSender
from tests.services.test_mail_templates import _FakeSmtp, _template


def test_assisted_email_uses_existing_template_with_activation_context(monkeypatch):
    _FakeSmtp.sent = None
    monkeypatch.setattr("app.registration.delivery.smtplib.SMTP_SSL", _FakeSmtp)
    sender = ConfiguredSmtpVerificationSender(
        host="smtp.example.com",
        port=465,
        username="admin@example.com",
        password="authorization-code",
        sender="admin@example.com",
        from_name="竞学 ERP",
        reply_to=None,
        tls_mode="implicit_tls",
        template=_template(),
    )
    sender.send_code(
        challenge_id=uuid4(),
        destination="isolated@example.com",
        code="123456",
        purpose="assisted_activation",
    )
    message = _FakeSmtp.sent
    assert str(message["Subject"]) == "竞学 ERP 档案账号激活验证码"
    assert "123456" in message.get_body(preferencelist=("html",)).get_content()
    assert "验证码：123456" in message.get_body(preferencelist=("plain",)).get_content()
