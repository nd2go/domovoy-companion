# Inventory schema API

The definitions and seed library implement Domovoy DATA04 from commit `3a80159`.
Homebox stores values. Companion stores definitions for each Homebox URL and collection.
The configuration directory is `data/inventory-schemas`. Include it in the Companion configuration backup.
It contains definitions and revision numbers, not another inventory database.

## Requests

- GET `/api/settings/inventory-schema` returns `revision` and `schema`.
- POST `/api/settings/inventory-schema/preview` checks `expected_revision` and `schema` without a write.
- PUT at the same root applies templates and then commits the new local revision.
- POST `/api/settings/inventory-schema/values` checks a revision, category, values and optional input units.

All routes verify current Homebox credentials and the selected collection.
The API accepts at most 2 MiB before JSON parsing. The upload has a 15-second deadline.
Each Homebox operation has a 15-second deadline and a 2 MiB response limit.
A full template update has a 120-second deadline. A file lock rejects a concurrent update.
Definitions limit tree depth, object counts, template fields, choices and individual values.
Homebox access uses the configured server URL. A schema cannot supply a destination URL.

New item submissions can include `schema_revision` and `category_id`.
The server checks every schema-aware item before the first inventory write.
Old clients can continue to submit their existing text custom fields.
Property text is validated against its definition and encoded for Homebox.
The raw Homebox interface remains available; invalid external edits need explicit correction.

## Recovery and compatibility

A revision conflict returns 409. Keep edits and reload the current definitions before retrying.
A failed Homebox operation leaves the local revision unchanged.
The next attempt reads managed template markers before a create, including after a lost response.
Template projection does not create, change, or delete inventory records.
It keeps unrelated user template fields, field IDs, and native defaults.
Existing DATA01 templates and item values are retained.
Schema templates use a separate marker; repeated application retains their IDs.

Managed values use text to preserve unknowns, false values, dates and decimal fractions.
Changing a label does not change its field ID.
Changing a field kind or unit requires a new ID. Existing choices and unit meanings are retained.
Archive definitions instead of deleting them. A template change does not rewrite existing cards.

## QA

Run the schema HTTP tests and the disposable Homebox live test.
The live test runs the changed Companion app, not an upstream Companion image.
No real AI service, private photo, phone, printer, or published QA is used.
