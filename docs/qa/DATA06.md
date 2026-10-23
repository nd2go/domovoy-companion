# DATA06 implementation and QA

The editor and property form extend the DATA05 schema API.
The interface adds categories, shared blocks, properties, units, archive controls, preview, and definition import/export.
Review keeps additional properties during category changes. Unit selections stay with their property until normalization.
A new property can belong only to the current item. A separate checkbox adds it to the category template.
Choice labels can change while their stored IDs stay fixed. The optional `option_labels` map holds those labels.

## Executed verification

The complete browser suite passed: 43 tests, including all 34 existing tests.
New checks cover category creation, shared blocks, revision conflicts, typed submission and unit conversion.
They also cover local versus template properties, invalid values, draft recovery, and 393/412-pixel viewports.
The API is mocked in these browser tests. They do not prove real recognition or physical-device support.

Svelte check and ESLint passed. The changed server's Python, Ruff, ty and Vulture checks passed.
The schema live test passed with real disposable Homebox under session and configured API-key authentication.
A separate headed Chrome check used the changed app and real disposable Homebox.
It created, previewed and applied a new category. Desktop and mobile-width screenshots were inspected.
No model call, physical phone, printer, published QA, or owner inventory was used.

## Review passes and corrections

Logic: trace settings through preview, revision checks and projection; trace Review through validation and submission.
Boundaries: retain values on revision conflicts and category changes. Bound imports before file reading.
Tests: keep exact submission-order assertions, and wait for the successful screen before checking asynchronous completion.
Security: render names as text. The existing client binds requests to the active authenticated collection.
Resources: use the server's schema limits; release export URLs and ignore responses from an old collection.
Readability: use the repository's Svelte components and typed API conventions. No full STE conformity claim is made.
The official STE PDF returned 403, so the dictionary check remains incomplete.

Review found that stored drafts omitted `custom_fields`. Serialization now retains values, category and schema revision.
Review also found that category changes cleared the selected input unit. The unit now remains attached to its field.
Known properties are validated even when the user does not edit them. Bulk-confirmed items are checked before submission.
Two existing browser assertions ran before their retry completed. They now wait for the successful screen.
Their exact request-list assertions still check order and prevent duplicate creation.

## Open acceptance

Real iPhone and Android checks have not run. Keep DATA06 open and its PR in draft until they pass.
The new AI proposal stage is DATA07. It still depends on the open AI02 task.
Post-merge regression also exposed an intermittent photo-count failure in the pinned Homebox boundary.
That finding is being diagnosed separately; it is not a schema test pass.
