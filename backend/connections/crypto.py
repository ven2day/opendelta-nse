"""AES-256-GCM envelope encryption for private exchange credentials."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12
_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


class CredentialKeyError(RuntimeError):
    """Safe key-configuration or decryption failure."""


@dataclass(frozen=True)
class EncryptedCredentials:
    ciphertext: bytes
    data_nonce: bytes
    encrypted_dek: bytes
    dek_nonce: bytes
    key_version: str


@dataclass(frozen=True)
class MasterKeyring:
    current_version: str
    keys: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if not _VERSION.fullmatch(self.current_version) or self.current_version not in self.keys:
            raise CredentialKeyError("Exchange credential master-key version is invalid")
        if not self.keys or len(self.keys) > 8:
            raise CredentialKeyError("Exchange credential keyring must contain between one and eight keys")
        for version, key in self.keys.items():
            if not _VERSION.fullmatch(version) or len(key) != KEY_BYTES:
                raise CredentialKeyError("Exchange credential master keys must be 32-byte AES keys")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> MasterKeyring | None:
        values = os.environ if environ is None else environ
        encoded = values.get("EXCHANGE_CREDENTIAL_MASTER_KEY", "").strip()
        version = values.get("EXCHANGE_CREDENTIAL_MASTER_KEY_VERSION", "").strip()
        if not encoded and not version:
            return None
        if not encoded or not version:
            raise CredentialKeyError("Exchange credential master-key configuration is incomplete")
        keys = {version: _decode_key(encoded)}
        previous = values.get("EXCHANGE_CREDENTIAL_PREVIOUS_KEYS", "").strip()
        if previous:
            try:
                decoded = json.loads(previous)
            except json.JSONDecodeError as error:
                raise CredentialKeyError("Exchange credential previous-key configuration is invalid") from error
            if not isinstance(decoded, dict):
                raise CredentialKeyError("Exchange credential previous keys must be a JSON object")
            for old_version, old_key in decoded.items():
                if not isinstance(old_version, str) or not isinstance(old_key, str):
                    raise CredentialKeyError("Exchange credential previous keys are invalid")
                keys.setdefault(old_version, _decode_key(old_key))
        return cls(current_version=version, keys=keys)


class EnvelopeCipher:
    def __init__(self, keyring: MasterKeyring) -> None:
        self.keyring = keyring

    def encrypt(self, connection_id: str, credentials: Mapping[str, Any]) -> EncryptedCredentials:
        plaintext = json.dumps(
            dict(credentials), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        data_key = secrets.token_bytes(KEY_BYTES)
        data_nonce = secrets.token_bytes(NONCE_BYTES)
        dek_nonce = secrets.token_bytes(NONCE_BYTES)
        version = self.keyring.current_version
        ciphertext = AESGCM(data_key).encrypt(data_nonce, plaintext, _credential_aad(connection_id))
        encrypted_dek = AESGCM(self.keyring.keys[version]).encrypt(
            dek_nonce, data_key, _dek_aad(connection_id, version)
        )
        return EncryptedCredentials(ciphertext, data_nonce, encrypted_dek, dek_nonce, version)

    def decrypt(self, connection_id: str, encrypted: EncryptedCredentials) -> dict[str, Any]:
        master_key = self.keyring.keys.get(encrypted.key_version)
        if master_key is None:
            raise CredentialKeyError("Required exchange credential master-key version is unavailable")
        try:
            data_key = AESGCM(master_key).decrypt(
                encrypted.dek_nonce,
                encrypted.encrypted_dek,
                _dek_aad(connection_id, encrypted.key_version),
            )
            plaintext = AESGCM(data_key).decrypt(
                encrypted.data_nonce, encrypted.ciphertext, _credential_aad(connection_id)
            )
            value = json.loads(plaintext)
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CredentialKeyError("Exchange credentials could not be decrypted") from error
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise CredentialKeyError("Decrypted exchange credentials are invalid")
        return value

    def rotate(self, connection_id: str, encrypted: EncryptedCredentials) -> EncryptedCredentials:
        return self.encrypt(connection_id, self.decrypt(connection_id, encrypted))


def _decode_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise CredentialKeyError("Exchange credential master keys must be valid base64") from error
    if len(decoded) != KEY_BYTES:
        raise CredentialKeyError("Exchange credential master keys must decode to 32 bytes")
    return decoded


def _credential_aad(connection_id: str) -> bytes:
    return f"opendelta:exchange-connection:{connection_id}:credentials:v1".encode()


def _dek_aad(connection_id: str, version: str) -> bytes:
    return f"opendelta:exchange-connection:{connection_id}:dek:{version}".encode()
