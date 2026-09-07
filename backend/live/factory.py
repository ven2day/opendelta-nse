"""Construct live adapters only after the service has passed every gate."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from backend.collector import DhanClient, DhanConfig
from backend.connections.crypto import EnvelopeCipher, MasterKeyring
from backend.connections.repository import ExchangeConnectionRepository
from backend.live.adapters import DhanOrderAdapter, OkxOrderAdapter, OrderAdapter, ValrOrderAdapter
from backend.live.service import LiveExecutionConfig


class LiveAdapterFactory:
    def __init__(
        self,
        connections: ExchangeConnectionRepository,
        config: LiveExecutionConfig,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.connections = connections
        self.config = config
        self.environ = os.environ if environ is None else environ

    def __call__(self, context: Mapping[str, Any]) -> OrderAdapter:
        mutations_enabled = (
            self.config.live_trading_enabled and self.config.deployment_permission and self.config.environment_allowed
        )
        provider = context["provider"]
        if provider == "DHAN":
            dhan_config = DhanConfig.from_environment()
            access_token = DhanClient(dhan_config).access_token()
            return DhanOrderAdapter(
                {"clientId": dhan_config.client_id, "accessToken": access_token},
                base_url=self.environ.get("DHAN_LIVE_ORDER_BASE_URL", dhan_config.base_url),
                mutations_enabled=mutations_enabled,
            )
        keyring = MasterKeyring.from_environment(self.environ)
        if keyring is None:
            raise RuntimeError("Exchange credential encryption is not configured")
        connection_id = str(context["connection_id"])
        _record, encrypted = self.connections.encrypted(connection_id)
        credentials = EnvelopeCipher(keyring).decrypt(connection_id, encrypted)
        if provider == "OKX":
            return OkxOrderAdapter(
                credentials,
                base_url=self.environ.get("OKX_PRIVATE_API_BASE_URL", "https://www.okx.com"),
                mutations_enabled=mutations_enabled,
            )
        if provider == "VALR":
            return ValrOrderAdapter(
                credentials,
                base_url=self.environ.get("VALR_PRIVATE_API_BASE_URL", "https://api.valr.com"),
                mutations_enabled=mutations_enabled,
            )
        raise ValueError("Unsupported live order provider")
