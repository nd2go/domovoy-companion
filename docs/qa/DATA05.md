# DATA05 acceptance and review

Base: `7effc18`. Review covers the schema API, value adapter, seed library and schema-aware item input.
The Homebox core, image handling and model provider are unchanged.

## Executed checks

- Baseline: 391 non-live tests passed; Svelte check passed.
- Final Python suite: 405 tests passed, 78 live tests excluded.
- Targeted schema suite: 14 tests passed.
- Ruff, ty with the repository's warning policy, and Vulture passed.
- The existing live selection plus the new schema test passed: 63 tests in the first run.
- The schema live test then passed in both session and configured API-key modes.
- A wheel build passed. The seed was checked in the wheel.

Live tests use the changed Companion app and disposable Homebox accounts.
They independently read templates and inventory through Homebox HTTP.
Assertions cover decimal text, dates, choices, false values, source notes, user fields and selected parents.
A schema label update leaves the stored inventory unchanged.
No real model, phone, physical printer or published QA was used.

## Six review passes

Logic: follow preview, projection and local revision commit. Failed projection keeps the active revision.
Boundaries: check malformed JSON, foreign collections, stale revisions, stream limits and concurrent updates.
Tests: mocks cover lost responses and negative paths; the live test proves the real Homebox boundary.
Security: verify current credentials and collection before configuration access or template writes.
Resources: bound request and response bytes, parsing input, schema size, depth, values, active operations and deadlines.
Readability: retain only interface and constraint descriptions. No full STE conformity claim is made.
The official STE PDF returned 403; the dictionary check remains incomplete.

Review found a template-default loss. Projection now keeps existing managed defaults and unrelated user fields.
A regression test retains `voltage_v=18.5` and the existing field ID during a label update.
The earlier shared-contract fixes also reject malformed definition types and inexact conversions.

| Input | Protected operation | Controls and evidence |
| --- | --- | --- |
| Session/key and group header | Read/write collection configuration | Real auth dependency; foreign and revoked access tests; both live auth modes |
| JSON body or import | Parse definitions and replace configuration | 2 MiB stream limit before parsing; 15-second receive deadline; malformed and streamed-body tests |
| Definition IDs and references | File keys and template composition | Hashed instance/collection path; bounded IDs, references, counts and depth; validator tests |
| Homebox response | Reconcile and write templates | UUID path validation, response limit, request deadline, no redirects, deterministic markers; lost-response test |
| Item values and revision | Inventory write | Validate all schema-aware cards before the first create; independent live GET |

At most four schema operations run per application process. A full projection has a 120-second deadline.
A failed update requires reconciliation; it must not be described as an inventory transaction.
Direct Homebox edits have no compare-and-swap guarantee. The application lock covers Companion schema updates.
Legacy clients keep their text-field API. Raw Homebox edits do not gain Companion type guarantees.
