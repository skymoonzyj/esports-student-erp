from __future__ import annotations

import re
import sys

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?im)^(\s*(?:authorization|cookie|set-cookie|"
            r"x-(?:registration-)?csrf-token)\s*:\s*).*$"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)([\"']?(?:password|code|ciphertext|csrf_token)[\"']?\s*[:=]\s*)"
            r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,}\]\r\n]+)"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?im)(^|[\[,{]\s*)"
            r"(-?\s*[\"']?[A-Z0-9_.-]*(?:TOKEN|SECRET|PEPPER|PASSWORD|KEY)"
            r"[\"']?\s*[:=]\s*)"
            r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,}\r\n]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)(\.(?:fill|type)\(\s*)"
            r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*')(\s*\))"
        ),
        r'\1"[REDACTED]"\2',
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])"
        ),
        "[REDACTED]",
    ),
    (re.compile(r"(?<!\d)\d{6}(?!\d)"), "[REDACTED]"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "[PHONE]"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "[IDENTITY]"),
    (re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (
        re.compile(r"postgres(?:ql)?(?:\+psycopg)?://[^\s]+", re.IGNORECASE),
        "[DATABASE_URL]",
    ),
    (
        re.compile(r"(?:/Users/|/private/|/tmp/)[^\s:]+"),
        "[ABSOLUTE_PATH]",
    ),
)


def redact_text(value: str) -> str:
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def main() -> None:
    for line in sys.stdin:
        sys.stdout.write(redact_text(line))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
