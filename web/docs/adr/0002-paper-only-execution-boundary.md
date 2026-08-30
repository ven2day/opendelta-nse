# ADR 0002: Paper-only execution boundary

Status: accepted

V1 performs research, backtests, and paper-signal preparation. It installs no order, account, balance, withdrawal, or private exchange endpoint. Risk, strategies, results, and APIs explicitly report paper/live state. A strategy setting cannot enable execution. Retired strategies remain historical/read-only.

Any future broker adapter requires separate security, authorization, audit, risk, and operational review.
