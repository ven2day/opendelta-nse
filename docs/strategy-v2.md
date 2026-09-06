# Strategy V2 source contract

Strategy Studio V2 is the authoring boundary for user-written Python strategies.
It is intentionally separate from the built-in strategy registry and the current
NSE/Crypto signal cycle.

## Contract

A source file defines a literal `STRATEGY` dictionary plus two synchronous
functions:

- `initialize(context)` prepares derived in-memory state.
- `handle_data(context, data)` evaluates completed candles and returns `BUY`,
  `SELL`, `HOLD`, or `None`.

Metadata declares the stable strategy id, semantic version, supported markets,
supported timeframes and parameter defaults. A saved `(strategy id, version)` is
immutable. Editing requires a new semantic version.

## Phase 1 safety boundary

The API parses the source into a Python AST and validates metadata, imports,
function signatures and common unsafe/look-ahead constructs. It does **not**
import or execute submitted code. Saving a source does not register, backtest,
approve, deploy or run it.

Execution will be introduced through a separate resource-limited worker with no
network, filesystem, database, subprocess or secret access. That worker must be
in place before a V2 source can be promoted into the existing Backtest → Signals
→ Paper lifecycle.

## Allowed analysis imports

The authoring validator permits `pandas`, `numpy`, `math`, and `statistics`.
This allowlist is validation metadata, not a substitute for the execution
sandbox.
