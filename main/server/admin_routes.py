"""Admin UI for catalogue cleanup.

The owner DMs ``/admin`` to the bot and receives a one-time URL. Visiting
that URL exchanges the token for a session cookie, then renders a paged
list of indexed BIN_CHANNEL entries with checkboxes. Bulk actions:

  • Delete: removes the BIN message AND the in-memory hub entry.
  • Re-tag: replaces the tag set on every selected entry.
  • Set quality: stamps a quality bucket on every selected entry.

Both re-tag and set-quality re-render the BIN caption via the same
IndexEntry pipeline used at index time so the on-channel representation
stays in sync.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main import StreamBot
from main.utils import admin_auth, media_index
from main.utils.human_readable import humanbytes
from main.utils.index_entry import IndexEntry, render
from main.vars import Var


routes = web.RouteTableDef()

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    enable_async=True,
)
_env.filters["humansize"] = lambda b: humanbytes(b) if b else ""


def _current_user(request: web.Request) -> Optional[int]:
    cookie = request.cookies.get(admin_auth.COOKIE_NAME)
    return admin_auth.verify_session(cookie or "")


def _require_session(request: web.Request) -> int:
    user_id = _current_user(request)
    if user_id is None:
        raise web.HTTPFound("/admin/login?error=expired")
    return user_id


def _html(body: str, *, status: int = 200) -> web.Response:
    return web.Response(
        text=body,
        status=status,
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )


@routes.get("/admin/login")
async def admin_login(request: web.Request) -> web.Response:
    """Exchange a one-time DM token for a session cookie.

    Expected URL: /admin/login?t=<one-time-token>. On success, redirect to
    /admin with the cookie set; on failure, render a tiny error page.
    """
    token = request.query.get("t", "")
    user_id = admin_auth.verify_one_time(token) if token else None
    if user_id is None:
        return _html(
            "<h1>Admin link invalid or expired</h1>"
            "<p>DM <code>/admin</code> to the bot to get a new link.</p>",
            status=403,
        )
    session = admin_auth.issue_session_token(user_id)
    resp = web.HTTPFound("/admin")
    resp.set_cookie(
        admin_auth.COOKIE_NAME, session,
        max_age=admin_auth.SESSION_TTL,
        httponly=True, samesite="Lax", path="/admin",
    )
    raise resp


@routes.get("/admin/logout")
async def admin_logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/")
    resp.del_cookie(admin_auth.COOKIE_NAME, path="/admin")
    raise resp


@routes.get("/admin")
async def admin_home(request: web.Request) -> web.Response:
    _require_session(request)

    items_all = sorted(
        media_index._items.values(),  # internal access — admin layer co-owns the store
        key=lambda it: it.message_id, reverse=True,
    )

    flash = request.query.get("flash", "")
    tpl = _env.get_template("admin.html")
    body = await tpl.render_async(
        items=items_all,
        catalogue_size=media_index.size(),
        flash=flash,
    )
    return _html(body)


def _is_htmx(request: web.Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


@routes.post("/admin/enrich")
async def admin_enrich(request: web.Request) -> web.Response:
    """Fire-and-forget bulk TMDB enrichment.

    HTMX requests get a 204 so the admin page stays put and the live
    progress widget picks the new state up on its next /admin/status
    poll. Non-HTMX callers (curl etc.) still get the legacy redirect.
    """
    _require_session(request)
    form = await request.post()
    force = bool(form.get("force"))
    from urllib.parse import quote

    state = media_index.enrichment_state()
    if not state.get("running"):
        import asyncio as _aio
        _aio.create_task(media_index.enrich_all(bot=StreamBot, force=force))
    flash_msg = (
        "Enrichment already running — see progress below"
        if state.get("running")
        else "Enrichment started — leave the page open to watch progress"
    )
    if _is_htmx(request):
        return web.Response(status=204)
    raise web.HTTPFound(f"/admin?flash={quote(flash_msg)}")


@routes.get("/admin/status")
async def admin_status(request: web.Request) -> web.Response:
    """JSON snapshot of the seed + enrichment progress. The admin page
    polls this every couple of seconds while either pipeline is active.
    """
    _require_session(request)
    return web.json_response({
        "seed": media_index.seed_state(),
        "enrich": media_index.enrichment_state(),
        "reindex": media_index.reindex_state(),
        "catalogue_size": media_index.size(),
    }, headers={"Cache-Control": "no-store"})


@routes.post("/admin/reindex")
async def admin_reindex(request: web.Request) -> web.Response:
    """Recompute series/movie/quality fields on every existing HubItem.

    Cheap — runs entirely against the cached metadata, no Telegram round
    trips. Used after the series or dedup detectors improve and older
    entries need to pick up the new logic.
    """
    _require_session(request)
    import asyncio as _aio
    state = media_index.reindex_state()
    if not state.get("running"):
        # Pass StreamBot so the completed re-index also uploads a fresh
        # Telegram-pinned state snapshot — cold restarts then restore
        # full enrichment data without re-hitting TMDB.
        _aio.create_task(media_index.reindex_all(bot=StreamBot))
    if _is_htmx(request):
        return web.Response(status=204)
    from urllib.parse import quote
    flash = "Re-index started — leave the page open to watch progress"
    raise web.HTTPFound(f"/admin?flash={quote(flash)}")


@routes.post("/admin/action")
async def admin_action(request: web.Request) -> web.Response:
    _require_session(request)

    form = await request.post()
    action = form.get("action", "")
    ids = [int(x) for x in form.getall("ids") if str(x).isdigit()]
    if not ids:
        raise web.HTTPFound("/admin?flash=Nothing+selected")

    if action == "delete":
        n = await _bulk_delete(ids)
        raise web.HTTPFound(f"/admin?flash=Deleted+{n}+entries")

    if action == "retag":
        tags = _normalise_tags(form.get("tags", ""))
        n = await _bulk_retag(ids, tags)
        raise web.HTTPFound(f"/admin?flash=Re-tagged+{n}+entries")

    if action == "quality":
        quality = (form.get("quality") or "").strip()
        if quality not in {"480p", "720p", "1080p", "4K"}:
            raise web.HTTPFound("/admin?flash=Invalid+quality")
        n = await _bulk_quality(ids, quality)
        raise web.HTTPFound(f"/admin?flash=Updated+quality+on+{n}+entries")

    raise web.HTTPFound("/admin?flash=Unknown+action")


@routes.post(r"/admin/edit/{id:\d+}")
async def admin_edit(request: web.Request) -> web.Response:
    """Per-row edit: title, year, tags, description in one go.

    After saving, fire a background re-enrich for this single entry so a
    title fix immediately retries the TMDB lookup with the new query —
    the operator doesn't have to also click "Enrich" to refresh
    misclassified items.
    """
    _require_session(request)
    message_id = int(request.match_info["id"])
    form = await request.post()

    new_title = (form.get("title") or "").strip()
    year_raw = (form.get("year") or "").strip()
    new_year = None
    if year_raw:
        try:
            new_year = int(year_raw)
        except ValueError:
            from urllib.parse import quote
            raise web.HTTPFound(f"/admin?flash={quote('Year must be a number')}")
    new_tags = _normalise_tags(form.get("tags") or "")
    new_description = (form.get("description") or "").strip()

    if not new_title:
        from urllib.parse import quote
        raise web.HTTPFound(f"/admin?flash={quote('Title is required')}")

    # Capture what the operator typed so we can decide whether to
    # re-enrich after the caption write.
    item_before = media_index.get_item(message_id)
    title_changed = item_before and item_before.title != new_title
    year_changed = item_before and item_before.year != new_year

    def apply(entry, _item):
        entry.title = new_title
        entry.year = new_year
        entry.tags = new_tags
        entry.description = new_description

    status = await _rewrite_caption(message_id, apply)

    # If the title or year changed and TMDB is configured, retry the
    # lookup so misclassified entries can be corrected by editing alone.
    # Reset the existing TMDB ID so enrich_one searches fresh by the new
    # title rather than just refreshing the old record. Skip for
    # 'removed' / 'failed' — there's nothing to re-enrich.
    if status in ("written", "local-only") and (title_changed or year_changed):
        from main.utils import tmdb
        if tmdb.is_configured():
            item = media_index.get_item(message_id)
            if item is not None:
                item.tmdb_id = None
                item.tmdb_kind = ""
                item.imdb_id = ""
                item.poster_path = ""
                item.backdrop_path = ""
                item.overview = ""
                item.tmdb_genres = []
                item.enriched_at = 0.0
            import asyncio as _aio
            _aio.create_task(
                media_index.enrich_one(message_id, bot=StreamBot)
            )

    from urllib.parse import quote
    if status == "written":
        msg = f"Updated bin:{message_id}"
        if title_changed or year_changed:
            msg += " — re-enrich queued"
    elif status == "local-only":
        msg = (
            f"Updated bin:{message_id} in the catalogue. The BIN_CHANNEL "
            "caption couldn't be edited (the message was posted to the "
            "channel directly rather than forwarded through the bot, so "
            "the bot doesn't own its caption)."
        )
        if title_changed or year_changed:
            msg += " Re-enrich queued."
    elif status == "removed":
        msg = (
            f"bin:{message_id} doesn't exist on BIN_CHANNEL anymore — "
            "removed from the catalogue. Refresh to drop the row."
        )
    else:
        msg = f"Edit failed for bin:{message_id} (see server logs)"
    raise web.HTTPFound(f"/admin?flash={quote(msg)}")


# --- Bulk operations --------------------------------------------------


def _normalise_tags(raw: str) -> List[str]:
    parts = [p.strip().lstrip("#").lower() for p in raw.replace(",", " ").split()]
    return [p for p in parts if p]


async def _bulk_delete(ids: List[int]) -> int:
    deleted = 0
    for mid in ids:
        try:
            await StreamBot.delete_messages(Var.BIN_CHANNEL, mid)
        except Exception:
            logging.exception("admin: delete failed for bin:%d", mid)
            continue
        await media_index.remove(mid)
        deleted += 1
    return deleted


async def _rewrite_caption(message_id: int, mutate) -> str:
    """Fetch a BIN message, rebuild its IndexEntry, mutate, and persist.

    ``mutate(entry, item)`` modifies the IndexEntry in place. Returns a
    status string:
      • ``"written"`` — caption was edited on Telegram and the in-memory
        entry was refreshed from the rewritten caption.
      • ``"local-only"`` — Telegram refused the edit (message exists but
        the bot doesn't own it) so we kept the in-memory mutation but
        didn't push it.
      • ``"removed"`` — the message truly no longer exists on the
        channel; the catalogue entry has been dropped.
      • ``"failed"`` — unexpected error; no state changes.

    Telegram errors we expect to see:
      • MessageIdInvalid — overloaded: either the message is gone OR
        the bot doesn't own it. We probe with get_messages to tell
        them apart.
      • InlineBotRequired / MessageAuthorRequired — same "can't edit"
        outcome as the not-bot-owned case.
      • MessageNotModified — same caption, treat as success.
    """
    from pyrogram.errors import MessageNotModified
    from pyrogram.errors.exceptions.bad_request_400 import MessageIdInvalid
    _UNEDITABLE: tuple = ()
    try:
        from pyrogram.errors.exceptions.forbidden_403 import InlineBotRequired
        _UNEDITABLE += (InlineBotRequired,)
    except ImportError:
        pass
    try:
        from pyrogram.errors.exceptions.forbidden_403 import MessageAuthorRequired
        _UNEDITABLE += (MessageAuthorRequired,)
    except ImportError:
        pass

    item = media_index.get_item(message_id)
    if item is None:
        return "failed"
    entry = IndexEntry(
        title=item.title,
        year=item.year,
        description=item.description,
        tags=list(item.tags),
        tmdb_id=item.tmdb_id,
        tmdb_kind=item.tmdb_kind,
        imdb_id=item.imdb_id,
        poster_path=item.poster_path,
        backdrop_path=item.backdrop_path,
    )
    mutate(entry, item)
    try:
        await StreamBot.edit_message_caption(
            chat_id=Var.BIN_CHANNEL,
            message_id=message_id,
            caption=render(entry),
        )
    except MessageNotModified:
        return "written"
    except MessageIdInvalid:
        # Telegram's MESSAGE_ID_INVALID is overloaded: it fires both when
        # the message truly is gone AND when the bot doesn't own the
        # message (admin posted it directly to the channel rather than
        # forwarded through the bot). Probe with get_messages — if it
        # comes back non-empty, the message exists and we just can't
        # edit its caption. Keep the entry, apply the mutation in
        # memory, and let the admin know.
        try:
            probe = await StreamBot.get_messages(Var.BIN_CHANNEL, message_id)
            still_exists = probe is not None and not getattr(probe, "empty", False)
        except Exception:
            still_exists = False
        if still_exists:
            logging.info(
                "admin: bin:%d not bot-owned; in-memory edit only", message_id,
            )
            _apply_local_only(message_id, entry)
            return "local-only"
        logging.info(
            "admin: bin:%d truly absent on Telegram; removing from catalogue",
            message_id,
        )
        await media_index.remove(message_id)
        return "removed"
    except _UNEDITABLE as exc:
        logging.info(
            "admin: bin:%d caption read-only (%s); skipping caption write",
            message_id, exc.__class__.__name__,
        )
        _apply_local_only(message_id, entry)
        return "local-only"
    except Exception:
        logging.exception("admin: edit_caption failed for bin:%d", message_id)
        return "failed"

    # Refresh the in-memory entry from the rewritten caption.
    try:
        fresh = await StreamBot.get_messages(Var.BIN_CHANNEL, message_id)
        await media_index.add_from_message(fresh)
    except Exception:
        logging.exception("admin: post-edit refresh failed for bin:%d", message_id)
    return "written"


def _apply_local_only(message_id: int, entry) -> None:
    """Update the in-memory HubItem fields when we couldn't push the
    edit to Telegram. Lets renames/retags still take effect on the hub
    even when the underlying channel message is read-only for our bot.
    """
    existing = media_index.get_item(message_id)
    if existing is None:
        return
    existing.title = entry.title
    existing.year = entry.year
    existing.description = entry.description
    existing.tags = list(entry.tags)


async def _bulk_retag(ids: List[int], tags: List[str]) -> int:
    def apply(entry, _item):
        entry.tags = list(tags)
    n = 0
    for mid in ids:
        status = await _rewrite_caption(mid, apply)
        if status in ("written", "local-only"):
            n += 1
    return n


async def _bulk_quality(ids: List[int], quality: str) -> int:
    # Quality is encoded into the description line so it round-trips
    # through the existing _extract_quality() regex used at index time.
    def apply(entry, item):
        # Replace an existing quality token if one is at the head of the
        # description, otherwise prepend.
        desc = (item.description or "").strip()
        for q in ("4K", "1080p", "720p", "480p", "2160p", "UHD", "FHD", "HD", "SD"):
            if desc.lower().startswith(q.lower()):
                desc = desc[len(q):].lstrip(" ·-—")
                break
        entry.description = (quality + (" · " + desc if desc else "")).strip()
    n = 0
    for mid in ids:
        status = await _rewrite_caption(mid, apply)
        if status in ("written", "local-only"):
            n += 1
    return n
