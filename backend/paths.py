"""Stable project paths shared by packaged backend modules."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = PROJECT_ROOT / "data"


def data_file(name: str) -> Path:
    """Return a repository data file without depending on the process cwd."""

    return DATA_DIRECTORY / name
