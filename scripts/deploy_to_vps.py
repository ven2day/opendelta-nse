#!/usr/bin/env python3
"""Trigger a production deploy to the VPS and watch it run.

This does not SSH anywhere itself -- it triggers the same GitHub Actions
workflow (.github/workflows/deploy.yml) that runs automatically on every
merge to main, then streams its progress. That workflow is the only thing
holding SSH credentials, so this script (and your machine) never need them.

Requirements:
  - GitHub CLI (`gh`) installed and authenticated: https://cli.github.com
  - Push access to ven2day/opendelta-nse (or membership letting you
    trigger workflow_dispatch)

Usage:
    python3 scripts/deploy_to_vps.py
    python3 scripts/deploy_to_vps.py --ref main --no-watch
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

REPO = "ven2day/opendelta-nse"
WORKFLOW = "deploy.yml"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, **kwargs)


def require_gh() -> None:
    if shutil.which("gh") is None:
        sys.exit(
            "error: GitHub CLI ('gh') not found on PATH.\n"
            "Install it from https://cli.github.com and run 'gh auth login' first."
        )
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(
            "error: 'gh' is installed but not authenticated.\n"
            "Run 'gh auth login' first, then re-run this script."
        )


def trigger(ref: str) -> None:
    result = run(
        ["gh", "workflow", "run", WORKFLOW, "--repo", REPO, "--ref", ref],
    )
    if result.returncode != 0:
        sys.exit(1)


def latest_run_id(ref: str) -> str:
    result = run(
        [
            "gh", "run", "list",
            "--repo", REPO,
            "--workflow", WORKFLOW,
            "--branch", ref,
            "--limit", "1",
            "--json", "databaseId",
            "--jq", ".[0].databaseId",
        ],
        capture_output=True,
        text=True,
    )
    run_id = result.stdout.strip()
    if result.returncode != 0 or not run_id:
        sys.exit("error: could not find the triggered run. Check 'gh run list' manually.")
    return run_id


def watch(run_id: str) -> int:
    result = run(["gh", "run", "watch", run_id, "--repo", REPO, "--exit-status"])
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="main", help="Branch to deploy (default: main)")
    parser.add_argument("--no-watch", action="store_true", help="Trigger the deploy and exit immediately")
    args = parser.parse_args()

    require_gh()

    print(f"Triggering deploy of '{args.ref}' to the VPS...")
    trigger(args.ref)

    if args.no_watch:
        print("Triggered. Follow it at:")
        print(f"  https://github.com/{REPO}/actions/workflows/{WORKFLOW}")
        return

    import time
    time.sleep(3)  # give GitHub a moment to register the new run
    run_id = latest_run_id(args.ref)
    print(f"Watching run {run_id}...")
    exit_code = watch(run_id)

    if exit_code == 0:
        print("Deploy succeeded.")
    else:
        print(f"Deploy failed (exit code {exit_code}). See the run log above.")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
