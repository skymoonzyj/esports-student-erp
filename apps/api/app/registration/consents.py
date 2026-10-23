from __future__ import annotations

import hashlib

CURRENT_REGISTRATION_TERMS_VERSION = "2026-08"

REGISTRATION_TERMS_CONTENT = {
    CURRENT_REGISTRATION_TERMS_VERSION: (
        "青训 ERP 自助注册条款|版本 2026-08|本人确认所填资料真实，"
        "并同意系统按注册、教学、住宿和法定实名用途处理资料。"
    ),
}


def registration_terms_digest(version: str) -> str | None:
    content = REGISTRATION_TERMS_CONTENT.get(version)
    if content is None:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
