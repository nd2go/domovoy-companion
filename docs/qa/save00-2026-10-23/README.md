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
- Preserve writable Homebox values during updates. Keep an explicit physical location. Do not set an inherited location as an explicit location. Reject updates to containers with child location sync enabled.
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
| Readability | Reviewed names, control flow, comments and test prose. Retrieved the official STE Issue 9 standard during the final review. Checked rules 1.1–1.14, 5.1–5.5 and 6.1–6.6, and dictionary entries for new comments and messages. Full STE conformity is not claimed. |

The final review found and corrected three more defects. The results and limits are below. This is not a repository-wide security assessment.

## External input checks

| Input and use | Controls and executed evidence | Limits |
| --- | --- | --- |
| Parent IDs → item creation | Existing gateway credentials and Homebox authorization. Anonymous create: 401. Invalid or foreign parent: no created item. | The batch endpoint reports individual failures with HTTP 207. |
| Item ID → read and update | The same authenticated principal reads the current item before the update. Anonymous update: 401. Foreign item was unchanged. | A foreign-item lookup becomes HTTP 502 through the existing gateway mapping. |
| User update body → Homebox | Only the existing name, description and asset-ID request fields are applied. Unit tests verify that arbitrary parent, fields and insured values are ignored. | This endpoint does not implement concurrent-edit protection. |
| Stored Homebox data → update body | Explicit writable-field list; parent and tag IDs are mapped separately. Tests exclude attachments, keep explicit `locationId`, and reject child location sync before PUT. | This check covers the supported Homebox contract. |
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


## Final review and QA

This review covers PR #24 and the SAVE00 acceptance requirements.
It is not an audit of all Domovoy repositories.

### Findings and corrections

1. **Explicit physical location was lost.** Homebox has a resolved `location` and an optional explicit `locationId`.
   A description update omitted `locationId`, so Homebox removed the explicit location.
   The update now keeps `locationId` when present. It does not copy the resolved location.
   Both inherited and explicit locations were checked with independent GET requests on Homebox v0.27.0.
2. **Child location sync was reset.** Homebox PUT stores this boolean even when the request omits it.
   Sending `true` can also remove explicit child locations.
   Companion now returns HTTP 409 for these containers before sending PUT.
   The message directs the user to Homebox. A real child with a separate location remained unchanged.
   Ordinary new items have this option disabled and can be saved and edited in Companion.
3. **Manual fields remained active during AI Correction.** A delayed response could replace changes made while the request was pending.
   The review form is now disabled during the request.
   Browser tests check the pending state, success, failure, and subsequent manual changes.

The location and sync tests first produced two expected failures.
The delayed-response browser tests first produced two expected failures.
All four passed after the corrections.

The [initial receipt](location-repro.json) records both Homebox failures.
The [final receipt](api-final.json) records explicit location preservation, safe sync rejection, and three creation cases.

### Final verification

| Command | Result |
| --- | --- |
| `uv run --no-sync pytest --tb=short --show-capture=no` | 391 passed, 76 deselected; one upstream deprecation warning |
| `uv run --no-sync pytest -m live tests/test_homebox_client.py tests/test_homebox_auth_live.py tests/test_homebox_upstream_live.py tests/test_companion_auth_live.py tests/test_label_print_live.py tests/test_mcp_tools.py --tb=short --show-capture=no` | 62 passed, 28 deselected; real disposable Homebox, both auth modes |
| `uv run --no-sync ruff check .` | Passed |
| `uv run --no-sync ty check --exit-zero-on-warning` | Exit 0 with 51 inherited warnings |
| `uv run --no-sync vulture --min-confidence 70 --sort-by-size` | Passed |
| `npm run check` | Zero errors and warnings |
| `npm run lint` | Passed |
| `npm run format:check` | Passed |
| `npm run test:browser -- --workers=1 --reporter=line` | 34 passed |
| `docker build -t domovoy-save00:review .` | Passed with the unchanged pinned Dockerfile and lockfiles |
| `npm audit --json` | Exit 1: 7 high and 3 moderate package reports; see below |

The final runtime image was `sha256:a22532de9389940b4af46c5d58f22bb82b57604f2aa56d1d79f72e5dee6cdd30`.
The later message wording change does not change its behavior.

### Dependency reports

The current npm registry reported ten affected packages with unchanged dependencies.
This count includes indirect reports through SvelteKit, Svelte, minimatch, postcss-nested and Tailwind.
The reports concern devalue, brace-expansion, source-map-js, Vite and postcss-selector-parser.

The production Docker image serves an adapter-static build from FastAPI. It has no Node or Vite server.
Vite editor and file-serving reports thus concern development use, not this production server.
Brace expansion, selector parsing and source-map processing are build tools in this setup.
The browser runtime does include devalue. No affected API was found on the changed JSON correction and save paths.
This is limited path analysis, not proof that each dependency report is unreachable throughout the application.
A separate dependency review is needed. No dependency or framework upgrade was added to SAVE00.

### Remaining limits

Concurrent updates to the same saved item have no version check. This is an existing Homebox GET/PUT limitation.
A parent change after selection and before save is not locked.
Request limits, AI timeouts and batch concurrency were not changed or load-tested.
No new AI request, automatic retry or background task was added.

Technical terms used in the new comments and messages include Homebox, PUT, item, child location, location override and sync.
Their meanings come from the pinned Homebox entity API and its update contract.
The word `remove` replaces the ambiguous verb `clear` in the new comment.
The official STE manual was inspected locally and was not copied into the repository.


### Final mobile check

The final image passed two submissions at 430 × 932, with Scan More between them.
Both independent Homebox reads had parent `998b2978-91a2-4316-8518-a45863bfc926`.
The plain item was `e30d2ae0-9965-472e-a252-555770cf2ac7`.
The extended item was `d79595b1-c5e3-44ab-bb28-86d58cc60910`.
The extended item kept its serial number, manufacturer and Source field after a partial AI correction.
Each confirmed card kept one original PNG. Direct and proxied downloads had the same SHA256 as the input.
One extra initial draft was skipped before the first submission.
The earlier mobile check above separately covers two photos attached to one card.

The [final mobile receipt](mobile-final.json) records both items and attachment checksums.
Screenshots show [Scan More with the box retained](scan-more-final.png) and [the completed submission](success-final.png).
Screenshots taken during navigation showed an incomplete animation; only settled screenshots are included.
The final mobile console had no view-transition timeout. The earlier timeout is not considered fixed.
