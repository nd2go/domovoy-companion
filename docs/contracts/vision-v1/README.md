# Vision contract, version 1

AI02 separates image analysis from the connection to a model provider.
The Companion server keeps the prompts, generated field models, and final validation.
Adapters receive the same request and return the same response.

## Connection selection

`HBC_VISION_BACKEND` selects `litellm`, `codex_cli`, or `claude_cli`.
The default is `litellm`. Existing profiles and API environment variables need no migration.
The model name is separate from the connection type. A CLI adapter can use its normal default model.

Only the LiteLLM adapter is installed by this change.
The two CLI types return `VISION_NOT_READY` until AI03 installs their worker adapters.
Tests use explicit fake adapters for both CLI types. They do not run a model or require an API key.
AI04 adds profile selection in the interface. This change adds no settings page.

The API adapter keeps its existing capability checks, prompt-only JSON mode, JSON repair, and configured Router fallback.
A CLI selection cannot fall through to the API adapter.
Chat continues to use its existing API path.

## Request and response

`request.schema.json`, `response.schema.json`, and `failure.schema.json` describe the JSON envelopes.
They come from `VisionRequest`, `VisionResponse`, and `VisionFailure` in `ai/vision_contract.py`.
The test suite checks the schemas and all examples against those models.

Requests contain a version, nonzero UUID, connection type, optional model, prompts, inline images, expected keys, schema, and timeout.
They contain no executable command, host path, API key, or login token.
Unknown envelope fields and unsupported versions are rejected.
Images use base64 data URIs. Remote URLs and file URLs are rejected.

Responses must match the request version, UUID, and connection type.
The `data` object must contain the expected keys and pass the caller's Pydantic model with strict field types.
This includes generated custom fields. Invalid output cannot become a successful item response.
Existing detector, analyzer, and corrector validation remains in place.

The Companion runner requires a compiled Pydantic model when a request contains `response_schema`.
The worker in AI03 must also validate the JSON schema received over HTTP.
The envelope schemas alone do not enforce that nested schema or all runtime resource limits.

## Runtime limits

| Resource | Limit and enforcement |
| --- | --- |
| Images | 1 to 30 per request |
| Decoded image bytes | 20 MiB each; 40 MiB total |
| Image transport | Inline JPEG, PNG, WebP, or GIF base64 only |
| Prompts | 65,536 characters each |
| Model name | 256 characters |
| Expected keys | 64 entries |
| Schema JSON | 64 KiB encoded as ASCII JSON |
| Result JSON | 2 MiB encoded as ASCII JSON |
| JSON structure | At most 64 nested levels and 65,536 visited elements |
| Total timeout | More than zero, at most 300 seconds; default 120 seconds |

The request checks all encoded image lengths before it decodes any image.
The existing upload and image-processing path owns file validation and pixel decoding.
This contract checks transport encoding and byte budgets; it does not inspect image pixels.

The API adapter checks initial and repair response bytes before JSON parsing.
The SDK has already received that content. This check does not bound the SDK's network buffer.
The common runner also bounds the parsed result before schema validation.
Existing upload limits and API rate controls remain in use.
This task adds no global request queue, paid-token quota, or model process.
AI03 must enforce its shared process limit and HTTP body limits before expensive work.

One asynchronous deadline covers readiness, inference, repair, and final validation in the runner.
Cancellation propagates to the adapter. A late result cannot become a success.
An adapter must release its own resources when cancelled.
The fake adapter tests prove coroutine cleanup. They do not prove process-group cleanup for a real CLI.

## Failures

| Code | HTTP status | Meaning |
| --- | --- | --- |
| `not_ready` | 503 | Configuration or required capabilities are unavailable |
| `timeout` | 504 | The total time budget was exceeded |
| `invalid_input` | 400 | The request violates the contract or a limit |
| `invalid_response` | 502 | Output identity, data size, or field types are invalid |
| `provider_failed` | 502 | The adapter or provider failed |

`VisionBackendError` exposes fixed messages and a `VISION_` error code to the current API error handler.
The failure envelope is available for the worker transport in AI03.
It has no free-form provider diagnostics. Optional success diagnostics contain only elapsed milliseconds.
Existing LiteLLM internal logs are outside this new error envelope.

## Acceptance and recovery

See [AI02 acceptance](../../qa/AI02.md) for commands, review evidence, and test limits.
Real host CLI execution belongs to AI03. Real profile selection and Homebox confirmation belong to AI04.
The OpenAI API image check remains a later acceptance step with configured access.

This change adds no production dependency or data migration.
Revert the PR to restore the previous vision path.
