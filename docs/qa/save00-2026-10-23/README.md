# SAVE00 verification — 23 October 2026

Task: [Companion #3](https://github.com/nd2go/domovoy-companion/issues/3).
Base: `0f39b90f7321eb298c7dc26b0e11f840ec2db79a`.

## Failures reproduced

1. With a cabinet and an item container selected, a basic item was created under the cabinet.
   An item with a serial number reached the box only during the second request.
   [The baseline receipt](baseline.json) records all three cases on Companion v3.1.1 and Homebox v0.27.0.
2. A later Companion name update removed an existing custom field in Homebox.
3. In the browser, AI Correction removed a selected tag when its response contained an empty tag list.
   Two new browser tests failed before the correction change: the selected tag had `aria-pressed="false"`.

## Changes

- Resolve the parent before creation: item container, item location, then batch location.
- Use the same parent during enrichment. Handle a missing parent without an exception.
- Preserve writable Homebox values during updates. Do not forward attachment metadata or child-location action flags.
- Preserve existing custom field IDs and unrelated fields. Do not combine existing fields that have the same name.
- Keep selected tags and known values during AI Correction. Combine partial custom field responses with existing values.
  Users can still remove values and tags in the form before confirmation.

## Acceptance results

| Requirement | Executed check | Result |
| --- | --- | --- |
| Basic and extended items use the selected box | Create both kinds with cabinet and box selected; read each item directly from Homebox | Passed |
| The storage path is correct | Read item → box → cabinet → room through the Homebox API | Passed |
| Location-only input still works | Create without a box; read the parent independently | Passed |
| Updates and repeated analysis preserve data | Correct both browser drafts; update saved descriptions; compare parent, tags, field IDs, serial and attachments | Passed |
| Mobile screen and Scan More retain the box | Complete two submissions at 430 × 932; use Scan More between them | Passed |
| Photos remain attached | Download all three photos through Homebox and Companion; compare SHA256 with the input PNGs | Passed |

The [API receipt](api.json) contains the location-only check and other direct API cases.
The [mobile receipt](mobile.json) contains item IDs, field IDs, attachment IDs, checksums and the complete hierarchy.
These records contain synthetic data only. They do not contain account credentials.

Mobile items:

| Kind | Homebox ID | Photos |
| --- | --- | --- |
| Basic | `755cf4fd-cb62-43c8-8e54-a9bf2ffe42db` | 2 |
| Serial and custom fields | `0896e6d4-e4ac-4a29-93df-c356485a13c8` | 1 |

Both have parent `104d6f62-c106-4ef5-93ad-bfb404245c2e`.
The second item retained serial `SAVE00-MOBILE-1` and `Source=Synthetic label` after a partial correction.
The requested `Control` change was saved as `Changed`.

## Runtime

- macOS ARM64 with Docker Desktop; separate synthetic containers on loopback ports 17755, 18010 and 18011.
- Homebox v0.27.0, commit `bae3fb4e9b84672e4e64da15e57a14217aea7c13`.
- Baseline Companion image: `ghcr.io/duelion/homebox-companion@sha256:90d7740f19150281f01ef9da8c6f9b31ec34c7e9c197136b3f713784541bc8ba`.
- Final test image: `sha256:1d1b44f83784782fa15e43f23707720a6ef11c6f30fb0e1cf94d5145909e5880`.
  It was built with the repository Dockerfile and unchanged lockfiles.
- Each test application had one CPU and a memory limit: Homebox 512 MiB; Companion 768 MiB.
- Chrome `155.0.8059.85`, desktop browser resized to 430 × 932. This was not a physical phone or camera test.
- Only vision `detect` and `correct` responses were mocked in the manual browser journey.
  Login, tags, item creation, updates, photo uploads and downloads used the real applications.
- The shared `domovoy-dev` and `domovoy-qa` applications were not changed.

## Commands and results

Local Python: CPython 3.14.8 and uv 0.12.5.
The local uv executable was `/tmp/domovoy-save00-tools/bin/uv`; `uv` below denotes that executable.
Local frontend checks used Node 24.19.0. The Dockerfile used its pinned Node 22 image.

| Command | Result |
| --- | --- |
| `uv sync --locked --all-groups` | Passed; lockfile unchanged |
| `uv run --no-sync pytest tests/test_item_placement.py -q --tb=short` before the backend fix | 10 failed, 13 passed; expected failures reproduced |
| `uv run --no-sync pytest tests/test_item_placement.py tests/test_item_partial_creation.py --tb=short --show-capture=no` | 30 passed |
| `uv run --no-sync pytest --tb=short --show-capture=no` | 387 passed, 76 deselected; one upstream deprecation warning |
| `uv run --no-sync pytest -m live tests/test_companion_auth_live.py tests/test_homebox_upstream_live.py --tb=short --show-capture=no` | 17 passed; real disposable Homebox, both auth modes |
| `uv run --no-sync ruff check .` | Passed |
| `uv run --no-sync ty check --exit-zero-on-warning` | Exit 0 with 51 warnings; not warning-free |
| `uv run --no-sync vulture --min-confidence 70 --sort-by-size` | Passed |
| `npm ci --no-progress` | Passed; reported 7 existing dependency advisories, not triaged in this task |
| `npm run check` | Passed; zero errors and warnings |
| `npm run lint` | Passed |
| `npm run format:check` | Passed |
| `npm run test:browser -- --workers=1 --reporter=line -g 'AI correction keeps'` before the frontend fix | Both new cases failed; expected tag loss reproduced |
| `npm run test:browser -- --workers=1 --reporter=line` after the frontend fix | 32 passed |
| `docker build -t domovoy-save00:fix --label org.opencontainers.image.revision=0f39b90-save00-working .` | Passed; production frontend build included |
| `git diff --check` | Passed |

The existing live-test fixture uses its own pinned Homebox image.
The manual API and browser checks above separately verify the required v0.27.0 image.
Real AI-provider tests were excluded. No Codex credentials or provider credentials were used.

An earlier parallel browser run had one reauthentication timeout: 29 passed, 1 failed.
The failed case passed alone. The complete serial run then passed, followed by the final 32-test run.
No authentication code was changed for that timeout.

## Review passes

| Pass | Evidence and limits |
| --- | --- |
| Logic and contracts | Traced create, enrichment, update, correction and submission. Checked the Homebox update contract and independent GET results. |
| Edge cases | Tested all parent fallbacks, empty parent, missing parent, custom field identity, duplicate field names and empty/new suggested tags. Existing partial-creation tests passed. |
| Tests | Added 24 default placement cases, two real Homebox auth variants, and two browser correction cases. Failures were observed before fixes. |
| Security | Traced request values and AI responses through schema validation, authenticated gateway, tag validation and Homebox. See the input table below. |
| CPU and memory | Parent resolution has constant cost. Field merging and tag combination have linear cost in their inputs. No new requests, retries, AI calls or background tasks were added. No performance benchmark was run. |
| Readability | Reviewed names, control flow, comments and test prose. The official STE standard and download page returned HTTP 403; the dictionary check is incomplete. Full STE conformity is not claimed. |

The review found no remaining defect in the changed paths. This is not a repository-wide security assessment.

## External input checks

| Input and use | Controls and executed evidence | Limits |
| --- | --- | --- |
| Parent IDs → item creation | Existing gateway credentials and Homebox authorization. Anonymous create: 401. Invalid or foreign parent: no created item. | The batch endpoint reports individual failures with HTTP 207. |
| Item ID → read and update | The same authenticated principal reads the current item before the update. Anonymous update: 401. Foreign item was unchanged. | A foreign-item lookup becomes HTTP 502 through the existing gateway mapping. |
| User update body → Homebox | Only the existing name, description and asset-ID request fields are applied. Unit tests verify that arbitrary parent, fields and insured values are ignored. | This endpoint does not implement concurrent-edit protection. |
| Stored Homebox data → update body | Explicit writable-field list; parent and tag IDs are mapped separately. Tests exclude attachments and `syncChildEntityLocations`. | This check covers the supported Homebox contract. |
| AI correction → form → confirmed save | Existing server response schema; text form bindings; selected tags are retained; backend filters submitted tag IDs against Homebox. Two browser regression cases passed. | AI accuracy was not tested. The user still confirms the draft. |
| Custom fields → merge | Typed submitted text fields; existing IDs retained; one linear pass through existing fields. Duplicate names are not deleted. | Existing request limit is 100 MiB. Per-batch item-count and downstream response-byte budgets were not changed or stress-tested. |

The [security receipt](security.json) records the manual negative checks against Homebox v0.27.0.
Container limits provided test isolation; they are not proof of application resource budgets.

## Visual evidence and remaining checks

- [Selected cabinet and box](selection.png).
- [Scan More retains the box](scan-more.png).
- [Second successful submission](success.png).

Both submissions completed without horizontal overflow on the success screen.
Chrome logged a DOM view-transition timeout after each successful submission. Item and photo checks still passed.
That transition behavior was not changed or diagnosed in this task.

Physical iOS/Android devices, cameras, real vision providers, QA publication and production data were not tested.
Existing catalog items were not migrated. Homebox core, framework versions and dependencies were not changed.
