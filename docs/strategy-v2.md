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

## Safety boundary

The API parses the source into a Python AST and validates metadata, imports,
function signatures and common unsafe/look-ahead constructs. The API process
does **not** import or execute submitted code. Saving a source does not register,
approve, deploy or run it.

For a backtest, OpenDelta sends the immutable source snapshot, resolved
parameters and one symbol's completed candles to a separate resource-limited
runner over a Unix socket. Production runs that service in a read-only Docker
container with no network, environment secrets, database connection,
subprocess capability or writable filesystem. Each request executes in a fresh,
time-limited child process.

Strategy V2 is currently **backtest-only**. A V2 backtest cannot be approved for
Signals or Paper. That deliberate lock remains until the live-runner phase adds
the same version/config/watchlist approval controls used by built-in strategies.

## Allowed analysis imports

The authoring validator permits `pandas`, `numpy`, `math`, and `statistics`.
This allowlist is validation metadata, not a substitute for the execution
sandbox.
