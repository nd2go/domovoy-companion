"""Durable Companion draft metadata and original photos."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from homebox_companion.core.persistent_settings import DATA_DIR

ROOT = DATA_DIR / "drafts"
DB = ROOT / "drafts.sqlite3"
PHOTOS = ROOT / "originals"
DERIVED = ROOT / "derived"


def _connect() -> sqlite3.Connection:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    PHOTOS.mkdir(mode=0o700, parents=True, exist_ok=True)
    DERIVED.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS drafts (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            owner TEXT NOT NULL,
            environment TEXT NOT NULL,
            instance TEXT NOT NULL,
            collection TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS drafts_scope ON drafts(scope, updated_at DESC);
        CREATE TABLE IF NOT EXISTS photos (
            id TEXT NOT NULL,
            draft_id TEXT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            mime TEXT NOT NULL,
            size INTEGER NOT NULL,
            checksum TEXT NOT NULL,
            PRIMARY KEY (draft_id, id)
        );
        CREATE TABLE IF NOT EXISTS operations (
            draft_id TEXT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
            id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            state TEXT NOT NULL,
            PRIMARY KEY (draft_id, id)
        );
        CREATE TABLE IF NOT EXISTS derived (
            draft_id TEXT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
            checksum TEXT NOT NULL,
            mime TEXT NOT NULL,
            size INTEGER NOT NULL,
            PRIMARY KEY (draft_id, checksum)
        );
        """
    )
    return db


@contextmanager
def connection():
    db = _connect()
    try:
        with db:
            yield db
    finally:
        db.close()


def _record(db: sqlite3.Connection, draft_id: str, scope: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM drafts WHERE id=? AND scope=?", (draft_id, scope)).fetchone()
    if row is None:
        raise KeyError(draft_id)
    return row


def _as_dict(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    photos = db.execute(
        "SELECT id, filename, mime, size, checksum FROM photos WHERE draft_id=? ORDER BY rowid", (row["id"],)
    ).fetchall()
    return {
        "id": row["id"],
        "revision": row["revision"],
        "state": json.loads(row["state"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "photos": [dict(photo) for photo in photos],
    }


def create(draft_id: str, scope: str, owner: str, environment: str, instance: str, collection: str) -> dict:
    with connection() as db:
        now = time.time()
        db.execute(
            "INSERT OR IGNORE INTO drafts (id,scope,owner,environment,instance,collection,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (draft_id, scope, owner, environment, instance, collection, now, now),
        )
        return _as_dict(db, _record(db, draft_id, scope))


def list_drafts(scope: str) -> list[dict]:
    with connection() as db:
        rows = db.execute("SELECT * FROM drafts WHERE scope=? ORDER BY updated_at DESC", (scope,)).fetchall()
        return [_as_dict(db, row) for row in rows]


def read(draft_id: str, scope: str) -> dict:
    with connection() as db:
        return _as_dict(db, _record(db, draft_id, scope))


def update(draft_id: str, scope: str, revision: int, state: dict) -> dict:
    encoded = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode()) > 2 * 1024 * 1024:
        raise ValueError("Draft state exceeds 2 MiB")
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _record(db, draft_id, scope)
        if row["revision"] != revision:
            raise FileExistsError("Draft revision changed")
        db.execute(
            "UPDATE drafts SET revision=revision+1,state=?,updated_at=? WHERE id=?",
            (encoded, time.time(), draft_id),
        )
        if "submission" in state:
            db.execute(
                "INSERT INTO operations (draft_id,id,revision,state) VALUES (?,?,?,?) "
                "ON CONFLICT(draft_id,id) DO UPDATE SET revision=excluded.revision,state=excluded.state",
                (draft_id, "submission", revision + 1, json.dumps(state["submission"], ensure_ascii=False)),
            )
        return _as_dict(db, _record(db, draft_id, scope))


def add_photo(
    draft_id: str,
    scope: str,
    photo_id: str,
    temporary: Path,
    filename: str,
    mime: str,
    size: int,
    checksum: str,
    max_photos: int,
) -> dict:
    destination = PHOTOS / f"{draft_id}-{photo_id}"
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        _record(db, draft_id, scope)
        existing = db.execute("SELECT * FROM photos WHERE draft_id=? AND id=?", (draft_id, photo_id)).fetchone()
        if existing:
            if existing["checksum"] != checksum or existing["size"] != size:
                raise FileExistsError("Photo ID already has different bytes")
            if not destination.is_file():
                raise OSError("Accepted original photo is missing")
            return dict(existing)
        count = db.execute("SELECT COUNT(*) FROM photos WHERE draft_id=?", (draft_id,)).fetchone()[0]
        if count >= max_photos:
            raise ValueError("Draft photo limit reached")
        os.replace(temporary, destination)
        directory = os.open(PHOTOS, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        db.execute(
            "INSERT INTO photos (draft_id,id,filename,mime,size,checksum) VALUES (?,?,?,?,?,?)",
            (draft_id, photo_id, filename, mime, size, checksum),
        )
        db.execute("UPDATE drafts SET updated_at=? WHERE id=?", (time.time(), draft_id))
        return {"id": photo_id, "filename": filename, "mime": mime, "size": size, "checksum": checksum}


def photo_path(draft_id: str, scope: str, photo_id: str) -> tuple[Path, dict]:
    with connection() as db:
        _record(db, draft_id, scope)
        row = db.execute("SELECT * FROM photos WHERE draft_id=? AND id=?", (draft_id, photo_id)).fetchone()
        if row is None:
            raise KeyError(photo_id)
        return PHOTOS / f"{draft_id}-{photo_id}", dict(row)


def add_derived(draft_id: str, scope: str, temporary: Path, mime: str, size: int, checksum: str) -> dict:
    destination = DERIVED / f"{draft_id}-{checksum}"
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        _record(db, draft_id, scope)
        existing = db.execute("SELECT * FROM derived WHERE draft_id=? AND checksum=?", (draft_id, checksum)).fetchone()
        if existing:
            if not destination.is_file():
                raise OSError("Accepted derivative is missing")
            return dict(existing)
        os.replace(temporary, destination)
        directory = os.open(DERIVED, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        db.execute(
            "INSERT INTO derived (draft_id,checksum,mime,size) VALUES (?,?,?,?)",
            (draft_id, checksum, mime, size),
        )
        return {"checksum": checksum, "mime": mime, "size": size}


def derived_path(draft_id: str, scope: str, checksum: str) -> tuple[Path, dict]:
    with connection() as db:
        _record(db, draft_id, scope)
        row = db.execute("SELECT * FROM derived WHERE draft_id=? AND checksum=?", (draft_id, checksum)).fetchone()
        if row is None:
            raise KeyError(checksum)
        return DERIVED / f"{draft_id}-{checksum}", dict(row)


def delete(draft_id: str, scope: str) -> None:
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        _record(db, draft_id, scope)
        ids = [row[0] for row in db.execute("SELECT id FROM photos WHERE draft_id=?", (draft_id,))]
        derived = [row[0] for row in db.execute("SELECT checksum FROM derived WHERE draft_id=?", (draft_id,))]
        db.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
    for photo_id in ids:
        (PHOTOS / f"{draft_id}-{photo_id}").unlink(missing_ok=True)
    for checksum in derived:
        (DERIVED / f"{draft_id}-{checksum}").unlink(missing_ok=True)
