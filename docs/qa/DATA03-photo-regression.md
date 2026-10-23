# Photo persistence regression

Post-merge QA found a false upload success in the pinned Homebox image.
The existing live scenario sends two photos, updates the item, then reads both attachments independently.
One upload returned 200 but the final item held one photo. Polling did not recover the missing attachment.
A diagnostic run reproduced this twice in 40 scenarios. Homebox logged `database is locked (517)` at attachment save.
The Homebox repository rolls back this failed save but returns the rollback result. A successful rollback can hide the write error.
Thumbnail processing uses a separate SQLite transaction and can overlap the next upload.

Companion now waits for earlier thumbnail writes before the next upload and checks the saved attachment IDs through a new item read.
A 200 response without a new saved ID raises an error. The client does not repeat the POST.
Thumbnail waits have a five-second deadline and at most 20 polls, spaced by 250 ms.
Transient 500/503 reads can settle within that budget. If previous photos stay pending, no new upload starts.
After a confirmed upload, a thumbnail timeout keeps the saved original and returns its item state.
The Homebox core and pinned image are unchanged.

## Verification

- Before correction: two failures in 40 diagnostic scenarios; the missing photo stayed missing on ten further reads.
- After correction: all 40 diagnostic scenarios passed. Each scenario checks two items with two photo uploads under session or configured key access.
- `uv run --no-sync pytest tests/test_attachment_persistence.py`: six passed.
- `uv run --no-sync pytest`: 412 passed, with the existing deprecation warning.
- `uv run --no-sync ruff check .`: passed.
- `uv run --no-sync ty check --exit-zero-on-warning`: passed under the existing warning policy.
- `uv run --no-sync vulture --min-confidence 70 --sort-by-size`: passed.
- The CI-selected live command passed: 64 tests against disposable Homebox.

The live command selected `test_homebox_client.py`, `test_homebox_auth_live.py`, `test_homebox_upstream_live.py`,
`test_inventory_schema_live.py`, `test_companion_auth_live.py`, `test_label_print_live.py` and `test_mcp_tools.py` with `-m live`.
The diagnostic test was temporary. It recorded only photo counts and matching database error lines.
No inventory data, credentials, model output or raw logs are committed.

## Review passes

Logic: require a new saved attachment ID before reporting success. Never repeat the upload inside the client.
Boundaries: stop before POST when older photo work exceeds the budget. Keep transport failures distinct from saved photos.
Tests: cover false 200, independent GET, pending work, transient read locks and lost POST responses.
Security: use the same request-bound credential and collection for every read and write. No new external destination is used.
Resources: at most two bounded thumbnail waits; each has 20 polls and a five-second deadline. Existing upload and HTTP limits remain in force.
Readability: keep one constraint comment and a short helper contract. The STE dictionary check was unavailable; no conformity claim is made.

This prevents the observed sequence and detects false success. It does not provide distributed upload idempotency across other clients.
The frontend's existing retry and cleanup policy remains unchanged.
