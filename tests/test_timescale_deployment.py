from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "web" / "deploy"


def read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_timescale_service_is_private_persistent_and_pinned() -> None:
    unit = read("opendelta-timescale.service")
    assert "timescale/timescaledb:2.29.2-pg17" in unit
    assert "timescale/timescaledb:latest" not in unit
    assert "--network opendelta-internal" in unit
    assert "opendelta-timescale-data,target=/var/lib/postgresql/data" in unit
    assert "--env-file /etc/opendelta-timescale.env" in unit
    assert not re.search(r"(?:^|\s)(?:-p|--publish)(?:\s|=)", unit)


def test_installer_protects_credentials_and_runs_migration() -> None:
    installer = read("install-timescale-service.sh")
    assert "umask 077" in installer
    assert "openssl rand -hex 32" in installer
    assert 'chmod 0600 "${database_environment}" "${application_environment}"' in installer
    assert "python -m backend.data.admin migrate" in installer
    assert "enable --now opendelta-timescale-backup.timer" in installer


def test_backup_is_verified_and_restore_is_explicitly_guarded() -> None:
    backup = read("backup-timescale.sh")
    restore = read("restore-timescale.sh")
    assert "pg_dump" in backup and "--format=custom" in backup
    assert "pg_restore --list" in backup
    assert "opendelta-*.dump" in backup
    assert "--confirm-restore-opendelta" in restore
    assert "opendelta-timescale-backup" in restore
    assert "--clean --if-exists --no-owner --exit-on-error" in restore


@pytest.mark.skipif(
    sys.platform.startswith("win") or shutil.which("bash") is None,
    reason="the deploy shell scripts are syntax-checked on POSIX CI where bash exists",
)
def test_all_timescale_shell_assets_parse() -> None:
    scripts = [
        "backup-timescale.sh",
        "bootstrap-market-data.sh",
        "install-timescale-service.sh",
        "restore-timescale.sh",
    ]
    subprocess.run(["bash", "-n", *[str(DEPLOY / item) for item in scripts]], check=True)


def test_runtime_image_contains_calendar_builder() -> None:
    dockerfile = read("backtest.Dockerfile")
    assert "backend/data/calendar.py" in dockerfile or "COPY backend ./backend" in dockerfile
