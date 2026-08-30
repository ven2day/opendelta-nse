from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".p12", ".pfx", ".key"}
FORBIDDEN_ARCHIVES = (".tar", ".tar.gz", ".tgz", ".zip")
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "OpenAI token": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "credential in Git URL": re.compile(r"https://[^\s/:]+:[^\s/@]+@github\.com/"),
}


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8", errors="strict")
    return [ROOT / value for value in output.split("\0") if value]


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        lower = relative.lower()
        if (Path(lower).suffix in FORBIDDEN_SUFFIXES or lower.endswith(FORBIDDEN_ARCHIVES)):
            findings.append(f"forbidden tracked artifact: {relative}")
        name = path.name.lower()
        if name.startswith(".env") and not name.endswith((".example", ".sample", ".template")):
            findings.append(f"forbidden tracked environment file: {relative}")
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                findings.append(f"{label}: {relative}")
    if findings:
        print("Credential scan failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Credential scan passed for tracked files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
