from __future__ import annotations

import os
import subprocess
from pathlib import Path


ACME_DIR = Path(__file__).resolve().parent
RENEW_SCRIPT = ACME_DIR / "renew-erp-tls.sh"
ACTIVATE_SCRIPT = ACME_DIR / "activate-erp-https.sh"


def test_renewal_reuses_cached_certbot_image_without_registry_pull(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "erp-tls"
    secret_dir = base_dir / "secrets"
    acme_dir = base_dir / "acme"
    bin_dir = tmp_path / "bin"
    secret_dir.mkdir(parents=True)
    acme_dir.mkdir()
    bin_dir.mkdir()

    for name in ("dnshe_api_key", "dnshe_api_secret"):
        path = secret_dir / name
        path.write_text("test-value\n", encoding="utf-8")
        path.chmod(0o600)
    (acme_dir / "dnshe_certbot_hook.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )

    calls = tmp_path / "docker-calls"
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
if [[ "$1 ${2:-}" == "image inspect" ]]; then
  exit 0
fi
if [[ "$1" == "pull" ]]; then
  exit 97
fi
if [[ "$1" == "run" ]]; then
  acme_dir=""
  for arg in "$@"; do
    if [[ "$arg" == *:/etc/letsencrypt ]]; then
      acme_dir="${arg%:/etc/letsencrypt}"
    fi
  done
  mkdir -p "$acme_dir/live/$ERP_TLS_DOMAIN"
  printf 'certificate\\n' > "$acme_dir/live/$ERP_TLS_DOMAIN/fullchain.pem"
  printf 'private-key\\n' > "$acme_dir/live/$ERP_TLS_DOMAIN/privkey.pem"
  exit 0
fi
if [[ "$1" == "ps" ]]; then
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "DOCKER_CALLS": str(calls),
            "ERP_TLS_BASE_DIR": str(base_dir),
            "ERP_TLS_DOMAIN": "skymoonzyj.l.cd",
            "PATH": f"{bin_dir}:{env['PATH']}",
        }
    )
    result = subprocess.run(
        [str(RENEW_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    docker_calls = calls.read_text(encoding="utf-8")
    assert "image inspect certbot/certbot:latest" in docker_calls
    assert "pull certbot/certbot:latest" not in docker_calls
    assert (base_dir / "certs" / "fullchain.pem").is_file()
    assert (base_dir / "certs" / "privkey.pem").is_file()


def test_activation_defaults_to_the_current_release_pointer() -> None:
    script = ACTIVATE_SCRIPT.read_text(encoding="utf-8")

    assert (
        'RELEASE_DIR="${ERP_RELEASE_DIR:-/home/skymoonzyj/esports-erp/current}"'
        in script
    )
    assert "f1f996557e7cf1fe" not in script


def test_renewal_exports_certificate_without_host_access_to_certbot_store(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "erp-tls"
    secret_dir = base_dir / "secrets"
    acme_dir = base_dir / "acme"
    bin_dir = tmp_path / "bin"
    secret_dir.mkdir(parents=True)
    acme_dir.mkdir()
    bin_dir.mkdir()

    for name in ("dnshe_api_key", "dnshe_api_secret"):
        path = secret_dir / name
        path.write_text("test-value\n", encoding="utf-8")
        path.chmod(0o600)
    (acme_dir / "dnshe_certbot_hook.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )

    calls = tmp_path / "docker-calls"
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
if [[ "$1 ${2:-}" == "image inspect" ]]; then
  exit 0
fi
if [[ "$1" == "run" ]]; then
  export_dir=""
  for arg in "$@"; do
    if [[ "$arg" == *:/export ]]; then
      export_dir="${arg%:/export}"
    fi
  done
  if [[ -n "$export_dir" ]]; then
    printf 'certificate\\n' > "$export_dir/fullchain.pem"
    printf 'private-key\\n' > "$export_dir/privkey.pem"
  fi
  exit 0
fi
if [[ "$1" == "ps" ]]; then
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "DOCKER_CALLS": str(calls),
            "ERP_TLS_BASE_DIR": str(base_dir),
            "ERP_TLS_DOMAIN": "skymoonzyj.l.cd",
            "PATH": f"{bin_dir}:{env['PATH']}",
        }
    )
    result = subprocess.run(
        [str(RENEW_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert (base_dir / "certs" / "fullchain.pem").read_text() == "certificate\n"
    assert (base_dir / "certs" / "privkey.pem").read_text() == "private-key\n"
    assert ":/export" in calls.read_text(encoding="utf-8")
