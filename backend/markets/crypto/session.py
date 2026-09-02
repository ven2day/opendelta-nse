"""Crypto markets trade continuously."""

from __future__ import annotations

from datetime import datetime


def crypto_session_is_open(moment: datetime) -> bool:  # noqa: ARG001 - the market never closes
    return True
