# Model Egress Policy

Constellation denies every model egress request unless the vault explicitly names the provider, transport, model, purpose, and maximum sensitivity.

The gate authorizes and records calls; it does not call a provider itself.

## Default

New vaults contain:

```yaml
egress:
  external_enabled: false
  providers: {}
```

Existing vaults without an `egress` section also fail closed.

## Provider declaration

```yaml
egress:
  external_enabled: false
  providers:
    local-example:
      enabled: true
      transport: local
      max_sensitivity: restricted
      models:
        - fictional-local-model-v1
      purposes:
        - stage1
        - evaluation
```

External use requires two explicit choices: the provider must declare `transport: external`, and `external_enabled` must be `true`.

```yaml
egress:
  external_enabled: true
  providers:
    external-example:
      enabled: true
      transport: external
      max_sensitivity: public
      models:
        - fictional-external-model-v1
      purposes:
        - research
```

Provider names and model names are exact matches. There is no wildcard or automatic local-to-external fallback.

Supported purposes are:

- `stage1`
- `research`
- `evaluation`
- `embedding`

Supported sensitivity levels, from lowest to highest, are `public`, `internal`, `confidential`, and `restricted`.

## Durable decisions

Every authorization or denial is appended to:

```text
.constellation/egress-ledger.jsonl
```

Each entry includes the provider, model, purpose, transport, sensitivity, source hashes, policy hash, request hash, result, reason, timestamp, and authorization ID. Source text and credentials are not written to this ledger.

Callers that could transmit content must use the raising `require_egress` API. A denied request raises only after its denial has been durably recorded.

## What this does not provide

The policy gate is not a provider adapter, credential store, network sandbox, or proof that a provider deleted transmitted data. Stage 1 and automatic provider calls remain separate capabilities. Credentials must remain in the agent/provider credential system, never in the vault policy.