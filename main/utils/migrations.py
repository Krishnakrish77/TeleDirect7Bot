"""Mongo migration registry — ordered, ledger-recorded, run once per database.

Every store module already does idempotent index creation in its own
``_ensure_indexes`` (create_index is a no-op when the spec exists) — that
stays where it is. This module owns the *other* kind of schema change:
one-shot transforms that are wrong or wasteful to repeat — dropping a
replaced index, backfilling a field across a collection, reshaping docs.

Contract:
  * a migration is ``(id, fn)`` where ``async def fn(db) -> str | None``;
    the returned string is a human result line for the log ("" or None
    means nothing to do),
  * ids are stable strings, appended to :data:`MIGRATIONS` in dependency
    order — never reorder or repurpose an id that has shipped,
  * completion is recorded in the ``schema_migrations`` collection
    (``_id`` = id, plus run duration) so each runs exactly once per
    database, not once per deploy,
  * failures are logged and swallowed (best-effort, like every store's
    ``_ensure_indexes``); a failed migration is simply re-attempted on
    the next boot because the ledger row was never written,
  * nothing here runs at import time — call :func:`run_pending_migrations`
    from ``_connect_catalogue_store`` after the store is reachable.

Leader/replica: only the leader calls this (replicas already skip the
BIN scan and reconciliation; they share the same database, so the
leader's ledger rows satisfy them).
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from main.vars import Var

_LEDGER = "schema_migrations"

MigrationFn = Callable[[], Awaitable[str | None]]


class Migration:
    __slots__ = ("id", "description", "fn")

    def __init__(self, id: str, description: str, fn: MigrationFn) -> None:
        self.id = id
        self.description = description
        self.fn = fn


MIGRATIONS: list[Migration] = []


def register(id: str, description: str, fn: MigrationFn) -> None:
    MIGRATIONS.append(Migration(id, description, fn))


async def run_pending_migrations() -> int:
    """Run every registered migration the ledger has no row for.

    Returns the number of migrations that ran. Never raises — a migration
    that fails is logged and left unrecorded so the next boot retries it.
    """
    db = _get_db()
    if db is None:
        return 0
    ledger = db[_LEDGER]
    try:
        done_ids = {str(d["_id"]) for d in await ledger.find(
            {}, projection={"_id": 1},
        ).to_list(length=10_000)}
    except Exception:
        logging.exception("migrations: ledger read failed; skipping this boot")
        return 0
    ran = 0
    for migration in MIGRATIONS:
        if migration.id in done_ids:
            continue
        started = time.monotonic()
        try:
            result = await migration.fn()
        except Exception:
            logging.exception("migrations: %s (%s) failed", migration.id, migration.description)
            continue
        try:
            await ledger.update_one(
                {"_id": migration.id},
                {"$set": {
                    "description": migration.description,
                    "ran_at": time.time(),
                    "duration_s": round(time.monotonic() - started, 3),
                }},
                upsert=True,
            )
        except Exception:
            # Row write failed: the migration ran but is not recorded, so it
            # will re-run next boot. All migrations are idempotent by contract.
            logging.exception("migrations: ledger write failed for %s", migration.id)
            continue
        ran += 1
        logging.info("migrations: %s (%s) completed%s in %.1fs",
                     migration.id, migration.description,
                     f" — {result}" if result else "",
                     time.monotonic() - started)
    return ran


def _get_db():
    try:
        from main.utils import media_index as _mi
        s = _mi._store
        if s is None or not hasattr(s, "_client"):
            return None
        return s._client[s._db_name]
    except Exception:
        return None


def is_available() -> bool:
    return _get_db() is not None


# ── Registered migrations (append-only — never reorder or reuse ids) ─────────

async def _drop_old_dismissed_index() -> str | None:
    from main.utils import dismissed_store
    db = dismissed_store._get_db()
    if db is None:
        return None
    try:
        await db["dismissed"].drop_index("user_id_1_tmdb_id_1")
        return "dropped obsolete user_id_1_tmdb_id_1"
    except Exception as exc:
        # Absent on fresh databases or when another instance already dropped
        # it — that is the desired end state.
        if getattr(exc, "code", None) in (26, 27):
            return None
        raise


async def _drop_old_photos_text_index() -> str | None:
    from main.utils import photo_store
    db = photo_store._get_db()
    if db is None:
        return None
    try:
        existing = await db["photos"].index_information()
    except Exception:
        logging.exception("migrations: photos index_information failed")
        return None
    old_spec = existing.get("photos_text_search")
    if not old_spec or "place" in str(old_spec.get("key", old_spec.get("weights", ""))):
        return None  # nothing to replace (fresh db or already 3-field)
    try:
        await db["photos"].drop_index("photos_text_search")
        return "dropped 2-field photos_text_search (place now included)"
    except Exception as exc:
        if getattr(exc, "code", None) in (26, 27):
            return None  # another instance won the race
        raise


async def _backfill_watch_badges() -> str:
    """Seed durable badges from surviving watch_history rows (older
    evictions are unrecoverable). Bounded by _WH_CAP per user; idempotent
    upserts on (user_id, cw_key)."""
    from main.utils import wh_store
    db = wh_store._get_db()
    if db is None:
        return ""
    await wh_store._ensure_indexes()
    count = 0
    cursor = db["watch_history"].find({}, projection={"user_id": 1, "cw_key": 1, "title": 1})
    async for row in cursor:
        doc = wh_store._badge_doc(int(row["user_id"]), str(row.get("cw_key", "")), str(row.get("title", "")))
        if doc is None:
            continue
        await db[wh_store._BADGES].update_one(
            {"user_id": doc["user_id"], "cw_key": doc["cw_key"]},
            {"$set": doc},
            upsert=True,
        )
        count += 1
    return f"{count} badges"


register(
    "2026_10_dismissed_drop_numeric_index",
    "remove obsolete dismissed user_id+tmdb_id index replaced by kind-aware key",
    _drop_old_dismissed_index,
)
register(
    "2026_10_photos_text_search_place",
    "replace 2-field photos text index with the file_name/camera/place version",
    _drop_old_photos_text_index,
)
register(
    "2026_10_watch_badges_backfill",
    "seed watched_badges from surviving watch_history rows",
    _backfill_watch_badges,
)
