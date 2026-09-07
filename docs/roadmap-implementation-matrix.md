# OpenDelta V2 roadmap implementation matrix

Assessment date: 2026-09-07. Baseline: `origin/main` at `35e144a`; ordered
delivery stack: PRs #92 through #101.

This matrix is based on the checked-out repository, migrations, route graph,
tests and GitHub pull-request state. It deliberately does not treat an earlier
summary as evidence.

## Repository baseline

| Surface | Status | Repository evidence |
| --- | --- | --- |
| Shared NSE/Crypto evaluator, Dhan/OKX/VALR market support | Complete | `backend/core`, `backend/strategies`, `backend/markets`; engine and provider contract suites |
| Strategy Studio V2 | Complete | migration 012; source validation/API/UI tests |
| Isolated Strategy V2 backtests and governance | Complete | migrations 013-014; networkless runner service; backtest, signals and paper tests |
| Indicator Studio V2 | Complete | migration 015; preview/API/UI tests |
| Candlestick Strategy Chart | Complete | immutable run chart API and `/backtest?market=...&runId=...` workspace |
| Research Lab | Complete | migration 016; immutable experiments and exact child runs |
| Strategy comparison | Complete, duplicated PR metadata | The Phase 6B tree is present on `main` through PR #91. PR #89 remains open but is superseded; no second comparison engine is needed. |
| Parameter experiments | Complete | migration 017; PR #91 merged on 2026-09-06 as `35e144a` |

## Remaining-roadmap delivery

| Phase | Status | Delivery evidence |
| --- | --- | --- |
| 8 — Walk-forward validation | Complete on review stack | migration 018, anchored/rolling previews, immutable folds, bounded coordinator, cancellation and `/research` UI; PR #92 |
| 9 — Strategy comparison completion | Complete on review stack | complete metric/sort/rank surface, training versus unseen comparison and explicit unavailable rejected-trade analytics; PR #93 |
| 10 — AI Research Copilot | Complete on review stack | migration 019, provider-neutral backend contract, explicit context, audit and draft-only workflow; PR #94 |
| 11 — Secure exchange connections | Complete on review stack | migration 020, AES-256-GCM envelope encryption, OKX/VALR permission tests, Dhan status; PR #95 |
| 12 — Live execution foundation | Complete on review stack | migration 021, common adapter contract, order intents/state machine, risk gates, stops and reconciliation; real mutation defaults off; PR #96 |
| 13 — Production monitoring | Complete on review stack | migration 022, leases, append-only audit, deduplicated alerts, notifications and Operations UI; PR #97 |
| 14 — Agent/MCP access | Complete on review stack | migration 023, hashed scoped tokens, 19 strict research tools and audit/rate limits; PR #98 |
| Full V2 validation | Complete on review stack | executable structural/policy contract, lifecycle evidence report and fail-closed unknown-market fix; PR #99 |
| V2 default cutover | Complete on review stack | strict Timescale default, durable deployment source of truth, updated release/rollback guidance and preserved safe redirects; PR #100 |
| Obsolete V1 removal | Complete on review stack | removed only dead environment activation and duplicate scanner startup; historical tables and `UNCERTAIN` items remain; PR #101 |

## QuantDinger functional comparison

The current upstream README describes a self-hosted workflow spanning AI
research, Python strategies, backtests, paper/live execution, monitoring,
automation and MCP. OpenDelta implements those useful workflow categories in
its own modular FastAPI/React architecture and limits providers to Dhan, OKX
and VALR.

| QuantDinger-inspired capability | OpenDelta adaptation |
| --- | --- |
| Strategy and indicator authoring | Immutable Strategy/Indicator V2 sources with AST validation and isolated networkless execution |
| Backtest and research automation | Existing bounded OpenDelta backtest engine, immutable experiments, parameter sweeps and walk-forward folds |
| Paper/live lifecycle | Existing manual Signals/Paper governance and FIFO paper broker; live adapters remain independently gated and disabled by default |
| AI assistance | Backend-only provider-neutral Copilot that creates research drafts and cannot approve or deploy |
| Broker connections | Encrypted OKX/VALR records plus read-only Dhan health; secrets never return to the browser |
| Operations | Database leases, append-only audits, durable alerts, reconciliation visibility and Operations UI |
| Agents/MCP | Narrow research-only MCP tools with separate hashed tokens and scopes; no trading/governance/secret tools |

No QuantDinger frontend source or branding was copied. This matters because the
upstream backend is Apache-2.0, while its separately published Vue frontend uses
a source-available licence with additional commercial and branding conditions.
OpenDelta retains its existing UI, dependencies and Apache-2.0 codebase.

Upstream references inspected:

- <https://github.com/OpenByteInc/QuantDinger>
- <https://github.com/OpenByteInc/QuantDinger/blob/main/README.md>
- <https://github.com/OpenByteInc/QuantDinger/blob/main/DEVELOPMENT.md>
- <https://github.com/OpenByteInc/QuantDinger/blob/main/mcp_server/README.md>
- <https://github.com/OpenByteInc/QuantDinger-Vue/blob/main/LICENSE>

## Intentional boundaries

- Rejected-trade counts remain unavailable because OpenDelta does not persist a
  decision-event audit stream. A no-BUY decision is not a rejection.
- Automated tests never submit a production order. Provider adapters use mocks
  and contract fixtures; real live use requires separate operator authorization.
- VALR public candles are supported by the existing Crypto service and dual
  writer. The standalone canonical backfill CLI currently schedules Dhan and
  OKX only; adding a second VALR scheduler is not required for this cutover.
- Optional email/webhook alerts and AI calls remain unavailable when their
  backend settings are absent; the durable local features continue to work.
