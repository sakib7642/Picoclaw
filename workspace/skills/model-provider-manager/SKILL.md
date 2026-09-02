---
name: model-provider-manager
description: "Manage the PicoClaw hosted-provider/model router while keeping MobileLLM as the permanent local model. Use when asked to inspect providers, discover models, refresh routing, enable or disable a provider/model, or diagnose provider failures."
metadata: {"nanobot":{"emoji":"🧭"}}
---

# Model & Provider Manager

This deployment keeps **MobileLLM-376M** as the permanent local model and uses a localhost-only model router for optional hosted OpenAI-compatible providers. Never rename the local model to a router name and never remove the local fallback.

## Management API

The router listens only on `127.0.0.1:8100`.

### Inspect

```bash
curl -fsS http://127.0.0.1:8100/health
curl -fsS http://127.0.0.1:8100/v1/models
curl -fsS http://127.0.0.1:8100/admin/status
```

The status response contains active providers, ranked candidate models, cooldowns, failures, and last errors.

### Refresh discovery

```bash
curl -fsS -X POST http://127.0.0.1:8100/admin/refresh
```

### Disable/enable a provider

```bash
curl -fsS -X POST http://127.0.0.1:8100/admin/provider \
  -H 'Content-Type: application/json' \
  -d '{"action":"disable","id":"provider-id"}'

curl -fsS -X POST http://127.0.0.1:8100/admin/provider \
  -H 'Content-Type: application/json' \
  -d '{"action":"enable","id":"provider-id"}'
```

### Disable/enable a model

```bash
curl -fsS -X POST http://127.0.0.1:8100/admin/model \
  -H 'Content-Type: application/json' \
  -d '{"action":"disable","provider":"openrouter","model":"model-id"}'
```

Use `enable` to re-enable it. Do not disable `mobilellm-376m` because it is the local safety fallback.

### Add a custom OpenAI-compatible provider

Provider metadata can be registered with:

```bash
curl -fsS -X POST http://127.0.0.1:8100/admin/provider \
  -H 'Content-Type: application/json' \
  -d '{"action":"add","id":"my-proxy","api_key_env":"MY_PROXY_API_KEY","base_url":"https://example.com/v1","priority":80}'
```

Adding metadata does **not** create a secret. The corresponding API-key environment variable must exist in the Render service before that provider can become active.

## Routing policy

1. Discover `/models` from every configured provider with a non-empty API key.
2. Prefer capable chat/instruct models according to the router's score.
3. Temporarily cool down providers/models after quota, auth, invalid-model, server, or network errors.
4. Retry another candidate when an upstream fails.
5. Always fall back to the local MobileLLM endpoint.
6. Never claim a provider is active unless `/admin/status` shows it as active.

## Important limitations

A 376M local model is a lightweight controller/fallback, not a replacement for large hosted models. The skill gives it deterministic management commands; it does not magically give the model the ability to discover arbitrary APIs without credentials.

For a new provider, first inspect `/admin/status`, then register the provider metadata if needed, and finally ask the user to add the provider's API key to Render if it is not already present. Never invent API keys.
