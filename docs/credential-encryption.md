# Exchange credential encryption and rotation

OpenDelta stores OKX and VALR private connection credentials with AES-256-GCM
envelope encryption. Dhan authentication remains in the existing deployment
integration and is shown read-only in the connection-status workspace.

## Encryption design

Each connection receives a cryptographically random 256-bit data-encryption key
(DEK). The complete provider credential document is encrypted with the DEK and a
unique 96-bit nonce. The DEK is then encrypted with the deployment master key and
a second unique 96-bit nonce. Both layers use AES-GCM authentication and bind the
connection ID and key version as additional authenticated data.

The database stores ciphertext, nonces, the encrypted DEK, a master-key version,
and a masked API-key suffix. It has no plaintext API-key, API-secret, or
passphrase column. API responses return none of the encrypted fields either.

Configure the current key through deployment secrets:

```dotenv
EXCHANGE_CREDENTIAL_MASTER_KEY=<base64-encoded-exactly-32-random-bytes>
EXCHANGE_CREDENTIAL_MASTER_KEY_VERSION=v1
```

Generate a key outside the repository, for example with a trusted secret manager
or `openssl rand -base64 32`. Never paste it into Git, support tickets, logs, or
browser configuration. The application fails closed for all credential writes,
tests, replacements, and rotations when the key is absent or invalid. Public OKX
and VALR market data remains operational.

## Master-key rotation runbook

1. Back up the database and verify the current key is recoverable from the secret
   manager.
2. Generate a new random 32-byte key and assign a new version.
3. Make the old key available temporarily as a compact JSON keyring, supplied by
   the secret manager (not a checked-in file):

   ```dotenv
   EXCHANGE_CREDENTIAL_MASTER_KEY=<new-base64-key>
   EXCHANGE_CREDENTIAL_MASTER_KEY_VERSION=v2
   EXCHANGE_CREDENTIAL_PREVIOUS_KEYS={"v1":"<old-base64-key>"}
   ```

4. Restart the API and use **Rotate encryption** for each connection. Rotation
   decrypts with the recorded old version and re-encrypts using a new DEK, new
   nonces, and the current master key.
5. Test each connection and confirm its stored `master_key_version` is `v2`.
6. Remove the old key from `EXCHANGE_CREDENTIAL_PREVIOUS_KEYS` and restart.

Rotation never changes provider-side keys and never makes a trading API call.
Replacing credentials is a separate, confirmation-gated action.

## Connection testing and permissions

Connection tests are read-only. OKX uses `GET /api/v5/account/config`; VALR uses
`GET /v1/account/api-keys/current`. Only normalized permission booleans and a
generic result are retained. Raw provider responses, balances, IP addresses, and
credentials are not persisted.

A successful test does not enable live execution. Keys with detected withdrawal
permission are marked `WITHDRAWAL_PERMISSION` and held disabled. OpenDelta never
needs withdrawal permission. IP allowlisting is reported only as detected/not
detected when the provider exposes it.

New and replacement connections start disabled. A read-only test does not change
that setting; enabling a tested, non-withdrawal connection is a separate user
action and still does not enable live trading.

Replacement requires `REPLACE OKX` or `REPLACE VALR`; deletion requires the
matching `DELETE` phrase. Deletion removes encrypted material while preserving a
secret-free audit event. Provider-side API keys must still be revoked separately.
