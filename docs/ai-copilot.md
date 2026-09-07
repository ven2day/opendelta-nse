# AI Research Copilot safety

The AI Research Copilot is an optional, provider-neutral research assistant.
It is disabled unless an operator configures a backend provider. Provider keys,
prompts, and provider calls never pass through the browser.

## Data boundary

The request API accepts resource IDs, not arbitrary filesystem paths, URLs,
SQL, environment names, or host commands. The user explicitly selects any of:

- immutable Strategy V2 source;
- immutable Indicator V2 source;
- a bounded backtest summary and up to 20 selected recorded trades;
- a durable parameter-experiment comparison;
- a durable walk-forward result.

The backend resolves those records and constructs the prompt. Credentials,
environment files, authentication data, and unrelated records are not part of
any selectable context. The complete selected context is limited to 262,144
bytes; instructions are limited to 4,000 characters; output is limited to
65,536 bytes; and calls time out after at most 60 seconds.

## Draft lifecycle

Every response is labelled `AI-generated research draft — review and validate`.
Responses are not persisted. The audit stores only provider/model, action,
context-category names, sizes, duration, numeric usage, status, and a response
hash. A user may explicitly save the exact reviewed response as a `DRAFT`; the
hash prevents substituting different content.

Loading a draft into an editor does not create an immutable V2 source. The
normal sequence remains:

```text
AI draft → user review → explicit draft/editor action → normal V2 validation
→ immutable version → backtest → manual Signals approval → manual Paper approval
```

The Copilot has no API action for approval, deployment, credentials, paper/live
activation, orders, arbitrary Python execution, shell access, filesystem access,
or arbitrary network access. Strategy and indicator execution stays in the
existing isolated networkless runner.

## Provider configuration and operation

The first adapter uses an operator-configured OpenAI-compatible chat endpoint
behind the internal `AIProvider` protocol. This is an adapter choice, not a
domain dependency; another provider can implement the protocol without changing
routes or the UI.

```dotenv
AI_PROVIDER=openai-compatible
AI_PROVIDER_ENDPOINT=https://provider.example/v1/chat/completions
AI_PROVIDER_API_KEY=deployment-secret
AI_MODEL=operator-selected-model
AI_PROVIDER_TIMEOUT_SECONDS=30
AI_COPILOT_REQUESTS_PER_MINUTE=10
```

Supply the key through deployment secrets, never Git or browser configuration.
If any required value is absent or invalid, status reports `AI provider not
configured` and the generate action remains disabled. All non-AI features keep
working.
