from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from opendelta.research_v2 import ResearchExperimentRequestV2


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "web" / "deploy"


def read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_timescale_service_is_private_persistent_and_pinned() -> None:
    unit = read("vento-nse-timescale.service")
    assert "timescale/timescaledb:2.29.2-pg17" in unit
    assert "timescale/timescaledb:latest" not in unit
    assert "--network vento-nse-internal" in unit
    assert "vento-nse-timescale-data,target=/var/lib/postgresql/data" in unit
    assert "--env-file /etc/vento-nse-timescale.env" in unit
    assert not re.search(r"(?:^|\s)(?:-p|--publish)(?:\s|=)", unit)


def test_installer_protects_credentials_and_runs_migration() -> None:
    installer = read("install-timescale-service.sh")
    assert "umask 077" in installer
    assert "openssl rand -hex 32" in installer
    assert 'chmod 0600 "${database_environment}" "${application_environment}"' in installer
    assert "python market_data_admin.py migrate" in installer
    assert "enable --now vento-nse-timescale-backup.timer" in installer


def test_backup_is_verified_and_restore_is_explicitly_guarded() -> None:
    backup = read("backup-timescale.sh")
    restore = read("restore-timescale.sh")
    assert "pg_dump" in backup and "--format=custom" in backup
    assert "pg_restore --list" in backup
    assert "opendelta-*.dump" in backup
    assert "--confirm-restore-opendelta" in restore
    assert "vento-nse-timescale-backup" in restore
    assert "--clean --if-exists --no-owner --exit-on-error" in restore


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
    assert "market_data_calendar.py" in dockerfile


def test_quant_smoke_uses_the_research_v2_request_and_response_contract() -> None:
    smoke = read("smoke-quant-platform.sh")
    match = re.search(r"research_payload='([^']+)'", smoke)
    assert match is not None
    request = ResearchExperimentRequestV2.model_validate(json.loads(match.group(1)))
    assert request.researchVersion == "2"
    assert request.symbols == ["LUPIN"]
    assert request.baseStrategyId == "neutral_research_trigger"
    assert "plannedBacktests == 1" in smoke
    assert "plannedEvaluations" not in smoke
    assert "durationYears" not in smoke
