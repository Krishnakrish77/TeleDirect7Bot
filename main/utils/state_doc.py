"""Catalogue snapshot stored as a Telegram document.

A single JSON file uploaded to BIN_CHANNEL and pinned acts as the
durable source of truth for everything the in-process catalogue knows —
title, year, TMDB ids, poster paths, etc. /tmp/media_index.json is just
a hot cache on top.

The flow:

  • Admin clicks Enrich/Re-index → after the work completes,
    ``save(bot, payload)`` uploads a new ``media-index-snapshot.json``
    to BIN_CHANNEL, pins it, and deletes the previous snapshot. The
    pinned-message slot is stable so future restarts know exactly where
    to fetch from.
  • On cold start, ``load(bot, channel_id)`` reads the pinned message,
    downloads the document, parses it. Empty/missing snapshot returns
    None and the caller falls back to caption-walking + manual enrich.

This keeps TMDB load at zero during normal restarts — the catalogue
state is fully recovered from the doc and we only re-hit TMDB when the
admin explicitly asks for a refresh.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Optional

from main.vars import Var


SNAPSHOT_FILENAME = "media-index-snapshot.json"
SNAPSHOT_MARKER = "media-index-snapshot/v1"


async def save(bot, payload: dict,
               prev_id_hint: Optional[int] = None) -> Optional[int]:
    """Upload a JSON snapshot and pin it; delete the previous snapshot.

    ``prev_id_hint`` is the message id the caller remembers from the
    last successful save. We prefer it over re-discovering via
    ``chat.pinned_message`` because pinning isn't guaranteed (bot may
    lack the pin permission); without the hint, accumulated snapshots
    would silently pile up.

    Returns the message id of the new snapshot or None on failure.
    """
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    fh = io.BytesIO(raw)
    fh.name = SNAPSHOT_FILENAME

    # Pre-resolve the previous snapshot id. Hint wins, pinned-message
    # check fills in if the caller doesn't have one.
    prev_id = prev_id_hint
    if not prev_id:
        prev_id = await _previous_snapshot_id(bot)

    try:
        msg = await bot.send_document(
            chat_id=Var.BIN_CHANNEL,
            document=fh,
            file_name=SNAPSHOT_FILENAME,
            caption=SNAPSHOT_MARKER,
            disable_notification=True,
        )
    except Exception:
        logging.exception("state_doc: snapshot upload failed")
        return None

    try:
        await bot.pin_chat_message(
            chat_id=Var.BIN_CHANNEL,
            message_id=msg.id,
            disable_notification=True,
        )
    except Exception:
        logging.warning(
            "state_doc: pin failed (snapshot still saved as bin:%d)",
            msg.id, exc_info=True,
        )

    if prev_id and prev_id != msg.id:
        try:
            await bot.delete_messages(Var.BIN_CHANNEL, prev_id)
        except Exception:
            logging.debug("state_doc: prior snapshot delete failed (non-fatal)",
                          exc_info=True)

    logging.info("state_doc: snapshot saved as bin:%d (%d bytes)",
                 msg.id, len(raw))
    return msg.id


async def load(bot) -> Optional[dict]:
    """Download the pinned snapshot and parse it. Returns None if no
    valid snapshot is pinned or the download/parse fails.
    """
    try:
        chat = await bot.get_chat(Var.BIN_CHANNEL)
    except Exception:
        logging.exception("state_doc: get_chat failed")
        return None

    pinned = getattr(chat, "pinned_message", None)
    if pinned is None:
        return None

    if not _looks_like_snapshot(pinned):
        return None

    try:
        bytesio = await bot.download_media(pinned, in_memory=True)
    except Exception:
        logging.exception("state_doc: download of pinned snapshot failed")
        return None
    if bytesio is None:
        return None

    raw = bytesio.getvalue() if hasattr(bytesio, "getvalue") else bytes(bytesio)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        logging.exception("state_doc: snapshot JSON parse failed")
        return None

    logging.info("state_doc: restored snapshot from bin:%d (%d bytes)",
                 pinned.id, len(raw))
    return payload


async def _previous_snapshot_id(bot) -> Optional[int]:
    try:
        chat = await bot.get_chat(Var.BIN_CHANNEL)
    except Exception:
        return None
    pinned = getattr(chat, "pinned_message", None)
    if pinned is None or not _looks_like_snapshot(pinned):
        return None
    return pinned.id


def _looks_like_snapshot(msg) -> bool:
    """Heuristic check: is this message our snapshot doc?"""
    doc = getattr(msg, "document", None)
    if doc is None:
        return False
    if (getattr(doc, "file_name", "") or "") != SNAPSHOT_FILENAME:
        return False
    cap = (getattr(msg, "caption", "") or "").strip()
    # Caption may be the literal marker, or include extra text — we just
    # require the marker substring so future versions can append notes.
    return SNAPSHOT_MARKER in cap or cap == ""
