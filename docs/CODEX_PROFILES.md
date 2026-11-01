# Codex photo profiles

AI04 connects the existing scan and review flow to the private Domovoy host worker.
Normal Codex login stays on the host. A container receives only its worker environment credential.
Real OpenAI acceptance is tracked in [AI07](https://github.com/nd2go/domovoy-companion/issues/32).

## Server configuration

Configure and verify the worker with the [Domovoy instructions](https://github.com/nd2go/domovoy/blob/main/docs/HOST_WORKER.md).
Use the same stable Homebox instance UUID and the actual collection UUID.
The Companion server checks the current Homebox identity and collection before a model call.

| Setting | Value |
| --- | --- |
| `HBC_VISION_WORKER_URL` | `http://host.docker.internal:8769` in Docker Desktop; `http://127.0.0.1:8769` on the host |
| `HBC_VISION_WORKER_TOKEN` | The selected dev or QA worker credential, provided through private server configuration |
| `HBC_VISION_ENVIRONMENT` | `dev` or `qa`, matching that credential |
| `HBC_VISION_HOMEBOX_INSTANCE` | The configured stable instance UUID |
| `HBC_LLM_TIMEOUT` | Total request budget; default 120 seconds, maximum 300 seconds for Codex |

Only loopback and Docker Desktop host destinations are accepted. Redirects and environment proxies are disabled.
Do not use an API profile's base URL as the worker address.
Do not pass the worker lifecycle credential or vendor login files to Companion.
Keep private configuration outside Git and build contexts, with mode `0600`.

## Profile selection

In Settings, add an AI profile and select **Codex CLI** under **Connection**.
Leave **Model** blank to use the CLI default, or enter a supported model name.
The profile needs no API key. Select it as the primary profile.
Readiness checks host configuration, CLI version, and normal login without running inference.
The Codex connection test also checks readiness only.

Existing profiles without a backend retain `HBC_VISION_BACKEND`, which defaults to `litellm`.
An explicit profile backend takes precedence. API models and keys are not inherited by Codex.
Codex can be primary or off. Automatic fallback remains within API profiles.
The selected profile is fixed for each analysis request. Profile updates use an atomic settings replacement.

Chat is unavailable with a CLI profile. The server also rejects direct chat model calls.
Select an API profile explicitly to use API chat. No failure switches Codex to the API.

## Analysis and cancellation

`GET /api/llm/readiness` returns safe readiness, profile revision, capabilities, and the request budget.
It never returns the worker address, credential, host paths, or vendor output.
The browser sends a UUID in `X-Request-ID`, plus draft ID, analysis revision, and profile revision headers.
These values correlate work. They do not grant access to a collection.
The server derives the environment and instance from its configuration and verifies the collection with Homebox.

Codex processes photos sequentially within a scan. The host still enforces one model process across dev and QA.
A competing request returns `busy`; it is not queued or retried automatically.
The browser timeout includes the server budget plus 15 seconds for transport.
`POST /api/tools/vision/cancel` requires the same verified owner, collection, and active request context.
Cancellation, disconnect, timeout, and invalid output release the active request.

The Codex adapter closes declared schema objects and requires their keys. Nullable field types remain nullable.
It rejects open-ended object maps before inference. The original Pydantic model still validates the result.
The API schema path is unchanged.

Errors retain photos, placement, correction instructions, and manual edits.
The user starts a retry. Old results cannot replace work after a context, profile, or analysis change.
The existing browser draft is retained. This change does not add durable server drafts or a job queue.

## Acceptance and recovery

Use an image built from the changed source. The default Domovoy release image does not include local source edits.
Run real Codex acceptance on disposable Homebox data. Record the source SHA, image ID, CLI version, and timings.
Check the saved entity and downloaded photo through independent Homebox API requests.
Report mocked failures separately from real model calls. Do not exhaust a subscription to test quota handling.
Published QA and physical-phone acceptance belong to separate tasks.

To roll back, restore the prior Companion revision and select the previous API profile.
No inventory migration is required. Preserve the Homebox data, attachments, and browser draft.
