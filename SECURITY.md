# Security Policy

## Scope

This is a public repository for a paper-trading research platform. It contains
no broker or exchange order-placement code, and the test suite fails if one is
introduced (`tests/test_live_signals.py`, `tests/test_signal_engine.py`,
`tests/test_paper_trading.py`).

## Reporting a vulnerability

Open a private security advisory on GitHub or contact the maintainer directly
rather than filing a public issue. Include the affected component, a
reproduction, and the impact. Expect an acknowledgement within a few days.

## Secrets

- Never commit `.env` files, Dhan credentials, TOTP secrets, access tokens,
  private keys, database URLs or production host details. The root
  `.gitignore` excludes the usual files and `scripts/security_scan.py` runs in
  CI over every tracked file.
- Dhan credentials are read in exactly one place (`backend/collector.py`,
  `DhanConfig.from_environment()`); every other module receives an
  authenticated client. The web app never receives `/etc/opendelta-dhan.env`.
- Crypto market data uses public OKX/VALR endpoints; no exchange API key is
  required or stored.
- The platform database URL is read from `MARKET_DATA_DATABASE_URL` only.

## Runtime hardening

The backtest container runs read-only, with all capabilities dropped,
`no-new-privileges`, a PID limit and a memory limit, as an unprivileged user.
The dashboard proxies the API with a session cookie and a proxy token and
never exposes backend credentials to the browser. Keep the site behind HTTPS
so the login cookie is `Secure`.

## Database safety

Schema migrations are an explicit operator step
(`python -m backend.data.migrate`); the service refuses to mutate a database
implicitly. Test suites that need PostgreSQL drop and recreate the `public`
schema — point them only at a throwaway database.
