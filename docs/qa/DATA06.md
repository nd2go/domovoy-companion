# DATA06 implementation and QA

The editor and property form extend the DATA05 schema API.
The interface adds categories, shared blocks, properties, units, archive controls, preview, and definition import/export.
Review keeps additional properties during category changes. Unit selections stay with their property until normalization.
A new property can belong only to the current item. A separate checkbox adds it to the category template.
Choice labels can change while their stored IDs stay fixed. The optional `option_labels` map holds those labels.

## Executed verification

The complete browser suite passed: 46 tests, including all 34 existing tests.
New checks cover category creation, shared blocks, revision conflicts, typed submission and unit conversion.
They also cover local versus template properties, invalid values, draft recovery, imports, stable labels, archives, oversized files and 393/412-pixel viewports.
Multiple-choice values can be set back to unknown after a known empty list.
The API is mocked in these browser tests. They do not prove real recognition or physical-device support.

Svelte check and ESLint passed. The changed server's Python, Ruff, ty and Vulture checks passed.
The schema live test passed with real disposable Homebox under session and configured API-key authentication.
The combined server and editor passed 413 non-live tests and the complete 64-test live selection.
The library and packaged seed have the same SHA-256.
A separate headed Chrome check used the changed app and real disposable Homebox.
It created, previewed and applied a new category. It also captured a synthetic image, selected a category, validated a size in a different unit and saved the item with its extra owner field. Only recognition was mocked in that flow. Desktop and mobile-width screenshots were inspected.
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
Review also found that category changes cleared the selected input unit. The unit now remains attached to its field. Existing count-unit text is also retained when the category changes.
Known properties are validated even when the user does not edit them. Bulk-confirmed items are checked before submission.
Two existing browser assertions ran before their retry completed. They now wait for the successful screen.
Their exact request-list assertions still check order and prevent duplicate creation.
Late responses cannot replace definitions after the account, collection or current item changes.
A browser regression holds a write response while switching collections and checks that the new editor stays intact.
Preview lists added and changed definitions before apply.

## Open acceptance

Real iPhone and Android checks have not run. Keep DATA06 open and its PR in draft until they pass.
The new AI proposal stage is DATA07. It still depends on the open AI02 task.
Post-merge regression also exposed an intermittent photo-count failure in the pinned Homebox boundary.
Companion PR #30 corrects the sequence and detects false upload success. It is merged as `f8daa74`.
The combined checks above include that correction.
Required physical-phone acceptance still prevents merging this editor PR under the project workflow.

## Pending phone checklist

Run the same steps on a real iPhone and Android against an isolated build of this branch.
Record device, OS, browser and build SHA. Use synthetic items and images.

1. Sign in or select the configured collection. Select a room and an item box.
2. In Settings, add a decimal property with a unit, a shared block and two categories that use the block. Preview and apply.
3. Capture a synthetic label. In Review, select the new category, set a decimal comma value, zero and a false boolean. Keep one property unknown.
4. Change category, then return. Check that the values and source unit remain. Confirm and save in the selected box. Read the values and original photo through Homebox.
5. Add a property only to one item. Check that the template does not change. Repeat with the separate template checkbox enabled.
6. Change a definition in another browser tab. Try the old revision, check the conflict and retained edits, then reload and review. Repeat after access expires or is revoked.
7. Enter another item and reload a saved draft. Check touch controls, date and multiple-choice controls, error text, navigation and absence of horizontal overflow.

These steps are a test plan. They have not been executed on physical devices.
