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

import asyncio
import base64
import json
import logging
import secrets
import time
from pathlib import Path
from typing import List, Optional, Tuple

import aiohttp
from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main import StreamBot
from main.utils import admin_auth, media_index
from main.utils.human_readable import humanbytes
from main.utils.index_entry import IndexEntry, render
from main.utils import series as series_parse
from main.utils.media_index import compute_movie_key
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
        # Lazy import — the server module owns the renderer and importing
        # at module load would create a circular dependency.
        from main.server import render_error
        link_minutes = max(1, round(admin_auth.TOKEN_TTL / 60))
        return await render_error(
            403,
            title="Admin link invalid or expired",
            message=(
                f"One-time admin links expire {link_minutes} minutes after "
                "they're issued. DM <code>/admin</code> to the bot to get "
                "a new one."
            ),
            action_href="https://t.me/" + (StreamBot.username or ""),
            action_label="Open bot in Telegram",
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


_FLASH_COOKIE = "admin_flash"


def _redirect_with_flash(message: str, target: str = "/admin") -> web.Response:
    """Set a short-lived flash cookie and redirect to a clean URL.

    Flash messages used to live in ``?flash=<encoded>`` query strings,
    which made the address bar ugly, leaked the text into browser
    history, re-showed the toast on refresh, and could expose state
    when an admin shared a URL. Cookies are a better fit: write once,
    read once, auto-cleared after the next /admin render.
    """
    from urllib.parse import quote as _q
    resp = web.HTTPFound(target)
    if message:
        # Cookie value is URL-encoded so commas / spaces / semicolons
        # don't mangle the Set-Cookie syntax. Decoded server-side
        # before rendering.
        resp.set_cookie(
            _FLASH_COOKIE, _q(message, safe=""),
            max_age=60, path="/admin", httponly=True, samesite="Lax",
        )
    return resp


def _pop_flash(request: web.Request, resp: web.Response) -> str:
    """Read the flash cookie if present and immediately delete it.

    Called from ``admin_home`` so the message renders exactly once
    and clears whether or not the user refreshes.
    """
    raw = request.cookies.get(_FLASH_COOKIE)
    if not raw:
        return ""
    from urllib.parse import unquote as _u
    try:
        msg = _u(raw)
    except Exception:
        msg = ""
    resp.del_cookie(_FLASH_COOKIE, path="/admin")
    return msg


@routes.get("/admin")
async def admin_home(request: web.Request) -> web.Response:
    _require_session(request)

    items_all = sorted(
        media_index._items.values(),  # internal access — admin layer co-owns the store
        key=lambda it: it.message_id, reverse=True,
    )

    # Detect duplicate groups. A "duplicate" means two rows that
    # point at the SAME underlying file — the same byte stream
    # forwarded into BIN_CHANNEL more than once.
    #
    # secure_hash alone is NOT enough: it's only the first 6 chars
    # of Telegram's file_unique_id, and bot-uploaded media share a
    # constant ~4-char prefix ("AgAD…"). That leaves ~2 chars =
    # 4096 buckets, giving wildly high birthday-paradox false-
    # positive rates even at a few hundred items. Joint key with
    # ``file_size`` collapses the FP probability to near zero
    # while keeping the check cheap.
    by_key: dict = {}
    for it in items_all:
        if it.secure_hash and it.file_size:
            by_key.setdefault((it.secure_hash, it.file_size), []).append(it)
    duplicate_message_ids: set = set()
    for k, members in by_key.items():
        if len(members) > 1:
            for m in members:
                duplicate_message_ids.add(m.message_id)

    # Read the flash cookie up front so we render once. The cookie
    # gets cleared on the response below regardless of whether it
    # was set, so a refresh after the toast disappears doesn't
    # re-show it.
    raw = request.cookies.get(_FLASH_COOKIE) or ""
    flash = ""
    if raw:
        from urllib.parse import unquote as _u
        try:
            flash = _u(raw)
        except Exception:
            flash = ""

    tpl = _env.get_template("admin.html")
    body = await tpl.render_async(
        items=items_all,
        catalogue_size=media_index.size(),
        stats=media_index.stats(),
        duplicate_message_ids=duplicate_message_ids,
        flash=flash,
        var=Var,
    )
    resp = _html(body)
    if raw:
        resp.del_cookie(_FLASH_COOKIE, path="/admin")
    return resp


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
    raise _redirect_with_flash(flash_msg)


@routes.get("/admin/tmdb-preview")
async def admin_tmdb_preview(request: web.Request) -> web.Response:
    """Preview a TMDB record by id so admin can confirm before applying.

    Hit by the Edit modal whenever the operator types a TMDB id. Returns
    poster path, title, year, overview, genres so the UI can render a
    small preview card next to the input.
    """
    _require_session(request)
    try:
        tmdb_id = int(request.query.get("id", ""))
    except ValueError:
        return web.json_response({"error": "id must be numeric"}, status=400)
    kind = (request.query.get("kind") or "movie").lower()
    if kind not in ("movie", "tv"):
        return web.json_response({"error": "kind must be movie or tv"}, status=400)

    from main.utils import tmdb
    if not tmdb.is_configured():
        return web.json_response({"error": "TMDB_API_KEY not set"}, status=503)
    hit = await tmdb.fetch_by_id(tmdb_id, kind)
    if hit is None:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response({
        "tmdb_id": hit.tmdb_id,
        "kind": hit.kind,
        "title": hit.title,
        "year": hit.year,
        "overview": hit.overview,
        "poster_path": hit.poster_path,
        "genres": hit.genres,
        "imdb_id": hit.imdb_id,
    })


@routes.get("/admin/tmdb-resolve-imdb")
async def admin_tmdb_resolve_imdb(request: web.Request) -> web.Response:
    """Resolve an IMDb tt-id to a TMDB (id, kind) pair via /find.

    Lets the Edit modal accept an IMDb URL/id and auto-fill the TMDB id
    + kind fields, sparing the operator a manual TMDB lookup.
    """
    _require_session(request)
    imdb_id = (request.query.get("imdb_id") or "").strip()
    # Accept either ``tt1234567`` or the full IMDb URL — pull the tt-id
    # out so the admin can paste either form.
    import re as _re
    m = _re.search(r"tt\d{6,10}", imdb_id)
    if not m:
        return web.json_response(
            {"error": "Provide an IMDb tt-id like tt1234567"}, status=400,
        )
    imdb_id = m.group(0)

    from main.utils import tmdb
    if not tmdb.is_configured():
        return web.json_response({"error": "TMDB_API_KEY not set"}, status=503)
    resolved = await tmdb.resolve_imdb_id(imdb_id)
    if resolved is None:
        return web.json_response(
            {"error": "No TMDB record for that IMDb id"}, status=404,
        )
    tmdb_id, kind = resolved
    return web.json_response({"tmdb_id": tmdb_id, "kind": kind, "imdb_id": imdb_id})


@routes.get("/admin/status")
async def admin_status(request: web.Request) -> web.Response:
    """JSON snapshot of the seed + enrichment progress. The admin page
    polls this every couple of seconds while either pipeline is active.
    """
    _require_session(request)
    from main.utils import codec_probe
    return web.json_response({
        "seed": media_index.seed_state(),
        "enrich": media_index.enrichment_state(),
        "reindex": media_index.reindex_state(),
        "probe": codec_probe.state(),
        "episode_fill": media_index.episode_fill_state(),
        "migrate": media_index.migrate_state(),
        "catalogue_size": media_index.size(),
    }, headers={"Cache-Control": "no-store"})


@routes.post("/admin/migrate-to-mongo")
async def admin_migrate_to_mongo(request: web.Request) -> web.Response:
    """Kick off a Mongo migration in the background. Progress flows
    through ``/admin/status`` (the same widget as the other long-
    running pipelines), so the admin page can keep updating the bar
    without holding an HTTP connection open.

    The endpoint itself only validates configuration + spawns the
    task. Real work happens in ``media_index.migrate_to_mongo``,
    which:
      * Builds a MongoStore + pings the cluster (connectivity check
        — bad URI / network / auth surfaces upfront, not mid-write).
      * Snapshots the in-memory dict under the lock so a parallel
        upload doesn't double-write.
      * Bulk-upserts in batches of 500 with done-counter bumps so
        the progress bar advances smoothly.
    """
    _require_session(request)
    import os
    if not os.environ.get("MONGO_URI"):
        raise _redirect_with_flash(
            "MONGO_URI env var is not set — configure Atlas first.",
        )

    if media_index.migrate_state().get("running"):
        if _is_htmx(request):
            return web.Response(status=204)
        raise _redirect_with_flash("Migration already running")

    db_name = os.environ.get("MONGO_DB") or "teledirect"
    items_coll = os.environ.get("MONGO_COLLECTION") or "items"
    meta_coll = os.environ.get("MONGO_META_COLLECTION") or "meta"

    import asyncio as _aio
    _aio.create_task(media_index.migrate_to_mongo(
        os.environ["MONGO_URI"], db_name, items_coll, meta_coll,
    ))
    if _is_htmx(request):
        return web.Response(status=204)
    raise _redirect_with_flash(
        f"Migration started against {db_name}.{items_coll} — watch the "
        "progress widget below.",
    )


@routes.post("/admin/dedupe")
async def admin_dedupe(request: web.Request) -> web.Response:
    """Find items that share a ``secure_hash`` (= same file uploaded
    multiple times) and delete the extras, keeping the lowest
    message_id (= the original upload).

    Quality variants of the same episode have DIFFERENT secure_hashes
    (different files, different file_unique_id) — they're untouched.
    This only collapses true duplicates: the same byte stream
    forwarded into BIN_CHANNEL more than once.
    """
    _require_session(request)
    # Joint key — secure_hash alone has too few effective bits (the
    # leading ~4 chars of file_unique_id are constant across all
    # bot-uploaded media) so hash-only matching false-positives
    # different files into the same group. file_size catches what
    # the hash misses.
    by_key: dict = {}
    for it in media_index._items.values():
        if not it.secure_hash or not it.file_size:
            continue
        by_key.setdefault((it.secure_hash, it.file_size), []).append(it)

    deleted = 0
    groups = 0
    for k, items in by_key.items():
        if len(items) <= 1:
            continue
        groups += 1
        # Keep the OLDEST upload (lowest message_id) as the canonical
        # entry; delete the rest from BIN + catalogue.
        keepers = sorted(items, key=lambda v: v.message_id)
        for extra in keepers[1:]:
            try:
                await StreamBot.delete_messages(Var.BIN_CHANNEL, extra.message_id)
            except Exception:
                logging.exception(
                    "admin: dedupe delete failed for bin:%d",
                    extra.message_id,
                )
                continue
            await media_index.remove(extra.message_id, bot=StreamBot)
            deleted += 1

    raise _redirect_with_flash(
        f"De-dup pass: {deleted} extra upload{'' if deleted == 1 else 's'} "
        f"removed across {groups} duplicate group{'' if groups == 1 else 's'}."
    )


@routes.post("/admin/prune-stale")
async def admin_prune_stale(request: web.Request) -> web.Response:
    """Remove index entries whose BIN_CHANNEL messages no longer exist.

    Stale entries accumulate when the bot misses deletion events (OOM
    crash, restart). Checks every indexed message_id against BIN_CHANNEL
    in batches of 100 and removes any that come back empty.
    """
    _require_session(request)
    ids = list(media_index._items.keys())
    removed = 0
    BATCH = 100
    for i in range(0, len(ids), BATCH):
        batch = ids[i: i + BATCH]
        try:
            msgs = await StreamBot.get_messages(Var.BIN_CHANNEL, batch)
        except Exception:
            logging.exception("admin: prune-stale batch fetch failed")
            continue
        for msg in msgs:
            if msg.empty:
                await media_index.remove(msg.id, bot=StreamBot)
                removed += 1
    raise _redirect_with_flash(
        f"Pruned {removed} stale entr{'y' if removed == 1 else 'ies'} "
        f"(checked {len(ids)} items)."
    )


@routes.post("/admin/fetch-episodes")
async def admin_fetch_episodes(request: web.Request) -> web.Response:
    """Backfill TMDB per-episode metadata (episode name + overview + still
    image) for TV rows where it's missing. One TMDB call per
    (tv_id, season) thanks to season-level caching, so even a 500-ep
    anime show only costs one call per season.
    """
    _require_session(request)
    import asyncio as _aio
    if not media_index.episode_fill_state().get("running"):
        _aio.create_task(media_index.fill_episode_details(bot=StreamBot))
    if _is_htmx(request):
        return web.Response(status=204)
    from urllib.parse import quote
    raise _redirect_with_flash('Episode details fetch queued')


@routes.post("/admin/probe-codecs")
async def admin_probe_codecs(request: web.Request) -> web.Response:
    """ffprobe every catalogue entry that hasn't been probed yet.

    Lets the watch page render the VLC-fallback overlay upfront for
    HEVC / 10-bit / AV1-in-MKV files instead of waiting for the
    browser to fail mid-playback. Bounded concurrency so we don't
    saturate Telegram's range endpoint.
    """
    _require_session(request)
    from main.utils import codec_probe
    import asyncio as _aio
    if not codec_probe.state().get("running"):
        _aio.create_task(codec_probe.probe_all_missing())
    if _is_htmx(request):
        return web.Response(status=204)
    from urllib.parse import quote
    raise _redirect_with_flash('Codec probe queued')



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
    raise _redirect_with_flash(flash)


@routes.post("/admin/action")
async def admin_action(request: web.Request) -> web.Response:
    _require_session(request)

    form = await request.post()
    action = form.get("action", "")
    ids = [int(x) for x in form.getall("ids") if str(x).isdigit()]
    if not ids:
        raise _redirect_with_flash("Nothing selected")

    if action == "delete":
        n = await _bulk_delete(ids)
        raise _redirect_with_flash(f"Deleted {n} entries")

    if action == "retag":
        tags = _normalise_tags(form.get("tags", ""))
        n = await _bulk_retag(ids, tags)
        raise _redirect_with_flash(f"Re-tagged {n} entries")

    if action == "quality":
        quality = (form.get("quality") or "").strip()
        if quality not in {"480p", "720p", "1080p", "4K"}:
            raise _redirect_with_flash("Invalid quality")
        n = await _bulk_quality(ids, quality)
        raise _redirect_with_flash(f"Updated quality on {n} entries")

    if action == "enrich":
        import asyncio as _aio
        force = bool(form.get("force"))

        async def _run(id_list: list) -> None:
            done = enriched = 0
            for mid in id_list:
                ok = await media_index.enrich_one(mid, bot=StreamBot)
                if ok:
                    enriched += 1
                done += 1
                await _aio.sleep(0)  # yield between TMDB calls
            logging.info("bulk enrich: %d/%d enriched", enriched, done)

        _aio.create_task(_run(ids))
        raise _redirect_with_flash(
            f"Enrichment queued for {len(ids)} items — TMDB results appear as they complete"
        )

    if action == "probe":
        from main.utils import codec_probe
        import asyncio as _aio

        async def _run_probe(id_list: list) -> None:
            done = found = 0
            for mid in id_list:
                item = media_index.get_item(mid)
                if item is None:
                    continue
                # Reset probed_at so codec_probe.probe_item runs even if
                # previously probed (user explicitly asked for a re-probe).
                item.probed_at = 0.0
                ok = await codec_probe.probe_item(item)
                if ok:
                    found += 1
                done += 1
                await _aio.sleep(0)
            logging.info("bulk probe: %d/%d had video streams", found, done)

        _aio.create_task(_run_probe(ids))
        raise _redirect_with_flash(
            f"Codec probe queued for {len(ids)} item(s) — duration + codec info updates as each completes"
        )

    raise _redirect_with_flash("Unknown action")


@routes.get("/admin/ai-models")
async def admin_ai_models(request: web.Request) -> web.Response:
    """Return models available for the configured GEMINI_API_KEY.

    Calls Google's model-list endpoint and filters to those that support
    generateContent (i.e. can be used with our suggest endpoint).
    Returns an empty list when the key is not configured.
    """
    _require_session(request)
    if not Var.GEMINI_API_KEY:
        return web.json_response([])

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models"
        f"?key={Var.GEMINI_API_KEY}&pageSize=200"
    )
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return web.json_response([])
                data = await r.json()

        models = []
        for m in data.get("models", []):
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            raw_name = m.get("name", "")          # "models/gemini-2.5-flash"
            model_id = raw_name.split("/")[-1] if "/" in raw_name else raw_name
            display  = m.get("displayName", model_id)
            models.append({"id": model_id, "name": display})

        return web.json_response(
            models,
            headers={"Cache-Control": "private, max-age=300"},
        )
    except Exception:
        logging.exception("admin: failed to list Gemini models")
        return web.json_response([])


async def _fetch_thumb_bytes(item) -> Optional[bytes]:
    """Fetch the video thumbnail via our own /thumb/ endpoint."""
    if not item.has_thumb:
        return None
    url = f"http://127.0.0.1:{Var.PORT}/thumb/{item.secure_hash}{item.message_id}.jpg"
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.read()
    except Exception:
        pass
    return None


# Temporary store for pending AI filename proposals.
# { token: {"expires": float, "proposals": [{"message_id", "current_file_name",
#            "current_title", "proposed_file_name", "proposed_title",
#            "proposed_year", "proposed_quality", "reasoning"}, ...]} }
_pending_proposals: dict = {}
_PROPOSAL_TTL = 600  # 10 minutes


def _prune_proposals() -> None:
    now = time.time()
    stale = [k for k, v in _pending_proposals.items() if v["expires"] < now]
    for k in stale:
        _pending_proposals.pop(k, None)


@routes.post("/admin/ai-review")
async def admin_ai_review(request: web.Request) -> web.Response:
    """Run Gemini on selected items and return an HTML review panel.

    Called via HTMX from the bulk action toolbar. Proposals are stored
    server-side keyed by a short-lived token; the review panel embeds
    the token so /admin/ai-apply knows which batch to commit.
    """
    _require_session(request)
    if not Var.GEMINI_API_KEY:
        return web.Response(
            text='<p class="text-red-400 text-sm p-4">GEMINI_API_KEY not configured.</p>',
            content_type="text/html",
        )

    form = await request.post()
    ids = [int(x) for x in form.getall("ids") if str(x).isdigit()]
    if not ids:
        return web.Response(
            text='<p class="text-slate-400 text-sm p-4">No items selected.</p>',
            content_type="text/html",
        )

    from main.utils import filename_ai as _fnai

    proposals = []
    for mid in ids:
        item = media_index.get_item(mid)
        if item is None or not item.file_name:
            continue
        result = await _fnai.parse_filename(item.file_name)
        if result is None:
            continue
        prop: dict = {
            "message_id": mid,
            "current_file_name": item.file_name,
            "current_title": item.title or "",
        }
        if result.get("is_device_generated"):
            prop["proposed_file_name"] = ""
            prop["proposed_title"] = item.title or ""
            prop["proposed_year"] = item.year or 0
            prop["proposed_quality"] = item.quality or ""
            prop["reasoning"] = result.get("reasoning", "Device-generated filename")
        else:
            prop["proposed_file_name"] = (result.get("clean_filename") or "").strip()
            prop["proposed_title"] = (result.get("title") or "").strip() or item.title or ""
            prop["proposed_year"] = result.get("year") or item.year or 0
            prop["proposed_quality"] = (result.get("quality") or "").strip() or item.quality or ""
            prop["reasoning"] = result.get("reasoning", "")
        # Skip if nothing would actually change
        if (prop["proposed_file_name"] == item.file_name
                and prop["proposed_title"] == (item.title or "")
                and prop["proposed_year"] == (item.year or 0)
                and prop["proposed_quality"] == (item.quality or "")):
            continue
        proposals.append(prop)

    if not proposals:
        return web.Response(
            text='<p class="text-slate-400 text-sm p-4">No changes suggested — filenames already look clean.</p>',
            content_type="text/html",
        )

    _prune_proposals()
    token = secrets.token_hex(12)
    _pending_proposals[token] = {
        "expires": time.time() + _PROPOSAL_TTL,
        "proposals": proposals,
    }

    rows_html = ""
    for p in proposals:
        fn_change = ""
        if p["proposed_file_name"] != p["current_file_name"]:
            fn_change = (
                f'<div class="flex items-baseline gap-1.5 flex-wrap">'
                f'<span class="text-slate-500 line-through text-[11px]">{p["current_file_name"] or "(blank)"}</span>'
                f'<span class="text-slate-400 text-[11px]">→</span>'
                f'<span class="text-violet-300 text-[11px]">{p["proposed_file_name"] or "(clear)"}</span>'
                f'</div>'
            )
        title_change = ""
        if p["proposed_title"] != p["current_title"]:
            title_change = (
                f'<div class="flex items-baseline gap-1.5 flex-wrap">'
                f'<span class="text-[10px] text-slate-600">Title:</span>'
                f'<span class="text-slate-500 line-through text-[11px]">{p["current_title"] or "(blank)"}</span>'
                f'<span class="text-slate-400 text-[11px]">→</span>'
                f'<span class="text-violet-200 text-[11px]">{p["proposed_title"]}</span>'
                f'</div>'
            )
        rows_html += (
            f'<label class="flex items-start gap-3 p-3 rounded-lg bg-ink-800/60 cursor-pointer'
            f' border border-white/5 hover:border-violet-400/30 transition-colors">'
            f'  <input type="checkbox" name="approve" value="{p["message_id"]}" checked'
            f'         class="mt-0.5 accent-violet-400 flex-shrink-0" />'
            f'  <div class="min-w-0 flex-1 space-y-0.5">'
            f'    {fn_change}{title_change}'
            f'    <p class="text-[10px] text-slate-600 italic">{p["reasoning"]}</p>'
            f'  </div>'
            f'</label>'
        )

    html = f"""
<div class="mt-6 rounded-xl border border-violet-400/20 bg-violet-500/5 p-5">
  <div class="flex items-center justify-between mb-4">
    <h3 class="text-sm font-semibold text-white">
      AI Filename Suggestions
      <span class="text-slate-400 font-normal ml-1">({len(proposals)} proposals)</span>
    </h3>
    <button type="button"
            onclick="document.getElementById('ai-review-panel').innerHTML=''"
            class="text-slate-500 hover:text-white text-sm transition-colors">✕</button>
  </div>
  <form method="post" action="/admin/ai-apply">
    <input type="hidden" name="token" value="{token}" />
    <div class="space-y-2 mb-5 max-h-96 overflow-y-auto pr-1">
      {rows_html}
    </div>
    <div class="flex items-center gap-2">
      <button type="submit"
              class="px-4 py-2 rounded-lg text-sm font-medium
                     bg-violet-500 hover:bg-violet-600 text-white transition-colors">
        Apply approved
      </button>
      <button type="button"
              onclick="document.getElementById('ai-review-panel').innerHTML=''"
              class="px-4 py-2 rounded-lg text-sm
                     bg-ink-800 text-slate-300 border border-white/5
                     hover:bg-ink-700 transition-colors">
        Dismiss
      </button>
    </div>
  </form>
</div>
"""
    return web.Response(text=html, content_type="text/html")


@routes.post("/admin/ai-apply")
async def admin_ai_apply(request: web.Request) -> web.Response:
    """Commit admin-approved AI filename proposals."""
    _require_session(request)
    form = await request.post()
    token = (form.get("token") or "").strip()
    approved = {int(x) for x in form.getall("approve") if str(x).isdigit()}

    batch = _pending_proposals.pop(token, None)
    if batch is None:
        raise _redirect_with_flash("Proposals expired or not found — run AI clean again")

    proposals = batch["proposals"]
    changed = 0
    for p in proposals:
        if p["message_id"] not in approved:
            continue
        item = media_index.get_item(p["message_id"])
        if item is None:
            continue
        if p["proposed_file_name"] != item.file_name:
            item.file_name = p["proposed_file_name"]
        if p["proposed_title"] and p["proposed_title"] != item.title:
            item.title = p["proposed_title"]
        if p["proposed_year"] and p["proposed_year"] != item.year:
            item.year = p["proposed_year"]
        if p["proposed_quality"] and p["proposed_quality"] != item.quality:
            item.quality = p["proposed_quality"]
        await media_index._store_upsert(item)
        changed += 1

    if changed:
        await media_index.persist_now()

    raise _redirect_with_flash(f"Applied {changed} of {len(approved)} approved proposals")


@routes.post(r"/admin/ai-suggest/{id:\d+}")
async def admin_ai_suggest(request: web.Request) -> web.Response:
    """Gemini Vision thumbnail analysis → structured metadata suggestions.

    Sends the video thumbnail + basic metadata to Gemini 2.0 Flash (free).
    Gemini reads any visible text in the thumbnail (course UI, show title,
    episode markers, watermarks, URLs) to identify the content and return
    pre-filled suggestions for title, series, season, episode, etc.
    """
    _require_session(request)
    if not Var.GEMINI_API_KEY:
        return web.json_response(
            {"error": "GEMINI_API_KEY not configured — get a free key at aistudio.google.com"},
            status=503,
        )
    message_id = int(request.match_info["id"])
    item = media_index.get_item(message_id)
    if item is None:
        return web.json_response({"error": "Item not found"}, status=404)

    thumb_bytes = await _fetch_thumb_bytes(item)

    meta_text = "\n".join([
        f"Filename: {item.file_name or '(none)'}",
        f"Current title: {item.title or '(none)'}",
        f"Current series: {item.series_title or '(none)'}",
        f"Duration: {item.duration}s" if item.duration else "Duration: unknown",
        f"File size: {humanbytes(item.file_size)}" if item.file_size else "",
    ])

    prompt = (
        "You are a media catalogue assistant. Analyse this video file and suggest "
        "accurate catalogue metadata.\n\n"
        "If a thumbnail image is provided, carefully read ALL visible text including:\n"
        "• Course / platform names (e.g. Three.js Journey, Udemy, YouTube)\n"
        "• Show or movie titles displayed on screen\n"
        "• Lesson / episode numbers or titles\n"
        "• Watermarks, UI elements, URLs, browser tabs\n"
        "• Any branding that identifies the content\n\n"
        f"File metadata:\n{meta_text}\n\n"
        "Rules:\n"
        "• Populate every field you can confidently identify.\n"
        "• For courses/series: set series_title, season (default 1), episode.\n"
        "• For movies: set title and year.\n"
        "• file_name: ALWAYS generate a descriptive filename based on what you found. "
        "Format: 'Series Name - Episode Title.mp4' for courses/episodes, "
        "'Movie Title (Year).mkv' for movies. Never leave file_name empty if you "
        "identified the content — it is the primary display label.\n"
        "• tags: at most 3 tags, space-separated, lowercase (e.g. 'action thriller 2020s').\n"
        "• Use 0 or empty string only for fields you truly cannot determine.\n"
        "• In 'reasoning' briefly explain what you found in the thumbnail."
    )

    parts = []
    if thumb_bytes:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(thumb_bytes).decode(),
            }
        })
    parts.append({"text": prompt})

    schema = {
        "type": "OBJECT",
        "properties": {
            "title":        {"type": "STRING"},
            "year":         {"type": "INTEGER"},
            "file_name":    {"type": "STRING"},
            "series_title": {"type": "STRING"},
            "season":       {"type": "INTEGER"},
            "episode":      {"type": "INTEGER"},
            "tags":         {"type": "STRING", "description": "Up to 3 most relevant space-separated tags, lowercase"},
            "description":  {"type": "STRING"},
            "reasoning":    {"type": "STRING"},
        },
        "required": ["title", "year", "file_name", "series_title",
                     "season", "episode", "tags", "description", "reasoning"],
    }

    import re as _re
    model = request.rel_url.query.get("model", "gemini-2.5-flash-lite")
    # Sanitise: only allow alphanumeric + hyphens + dots (no path traversal)
    if not _re.fullmatch(r"[a-zA-Z0-9][-a-zA-Z0-9._]*", model):
        model = "gemini-2.5-flash-lite"
    gemini_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models"
        f"/{model}:generateContent?key={Var.GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": schema,
        },
    }

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                gemini_url, json=payload,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as r:
                try:
                    resp_data = await r.json(content_type=None)
                except Exception:
                    body = await r.text()
                    return web.json_response(
                        {"error": f"Gemini returned non-JSON (HTTP {r.status}): {body[:200]}"},
                        status=502,
                    )
                if r.status != 200:
                    err = resp_data.get("error", {}).get("message", str(resp_data))
                    return web.json_response({"error": f"Gemini: {err}"}, status=502)

        candidates = resp_data.get("candidates") or []
        if not candidates:
            # Gemini blocked the response (safety filter, token limit, etc.)
            block_reason = (
                resp_data.get("promptFeedback", {}).get("blockReason")
                or resp_data.get("candidates", [{}])[0].get("finishReason")
                or "no candidates returned"
            )
            return web.json_response(
                {"error": f"Gemini blocked the response: {block_reason}"},
                status=502,
            )
        finish_reason = candidates[0].get("finishReason", "")
        if finish_reason not in ("STOP", "MAX_TOKENS", ""):
            return web.json_response(
                {"error": f"Gemini stopped early: {finish_reason}"},
                status=502,
            )
        try:
            text = candidates[0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            return web.json_response(
                {"error": f"Unexpected Gemini response structure: {e} — {str(resp_data)[:200]}"},
                status=502,
            )
        data = json.loads(text)

        # Drop zero / blank fields so the modal only fills what Gemini knows.
        clean = {
            k: v for k, v in data.items()
            if not (isinstance(v, int) and v == 0)
            and not (isinstance(v, str) and not v.strip())
        }
        # Cap tags at 3 regardless of what Gemini returned.
        if "tags" in clean:
            clean["tags"] = " ".join(clean["tags"].split()[:3])
        return web.json_response(clean)

    except asyncio.TimeoutError:
        return web.json_response(
            {"error": "Gemini timed out (90 s) — thumbnail may be too large; try a model without vision or re-try"},
            status=504,
        )
    except Exception:
        logging.exception("admin: Gemini suggest failed for bin:%d", message_id)
        return web.json_response(
            {"error": "Gemini request failed — check server logs"}, status=500
        )


@routes.post(r"/admin/clear-tmdb/{id:\d+}")
async def admin_clear_tmdb(request: web.Request) -> web.Response:
    """Wipe all TMDB-derived fields for a catalogue entry.

    Useful when auto-enrichment matched the wrong movie/show. Clears
    tmdb_id, poster, backdrop, genres, overview, imdb_id, and resets
    enriched_at so the next enrich pass will search fresh.
    """
    _require_session(request)
    message_id = int(request.match_info["id"])
    item = media_index.get_item(message_id)
    if item is None:
        from urllib.parse import quote
        raise _redirect_with_flash(f"bin:{message_id} not found")

    async with media_index._lock:
        item.tmdb_id = None
        item.tmdb_kind = ""
        item.imdb_id = ""
        item.poster_path = ""
        item.backdrop_path = ""
        item.overview = ""
        item.tmdb_genres = []
        item.enriched_at = 0.0
        item.episode_title = ""
        item.episode_overview = ""
        item.episode_still_path = ""
        item.episode_air_date = ""
        media_index._persist_unlocked()

    await media_index._store_upsert(item)

    from urllib.parse import quote
    raise _redirect_with_flash(f"TMDB enrichment cleared for bin:{message_id}")


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
            raise _redirect_with_flash('Year must be a number')
    new_tags = _normalise_tags(form.get("tags") or "")
    new_description = (form.get("description") or "").strip()
    new_file_name = (form.get("file_name") or "").strip()
    new_series_title = (form.get("series_title") or "").strip()
    season_raw = (form.get("season") or "").strip()
    episode_raw = (form.get("episode") or "").strip()
    new_season: Optional[int] = int(season_raw) if season_raw.isdigit() else None
    new_episode: Optional[int] = int(episode_raw) if episode_raw.isdigit() else None

    # Optional manual TMDB-id override. When present, it bypasses the
    # title-search path entirely — admin tells us which record to use
    # by its provider id (handy when titles are too generic for search
    # to disambiguate).
    tmdb_id_raw = (form.get("tmdb_id") or "").strip()
    tmdb_kind = (form.get("tmdb_kind") or "movie").strip().lower()
    if tmdb_kind not in ("movie", "tv"):
        tmdb_kind = "movie"
    manual_tmdb_id: Optional[int] = None
    if tmdb_id_raw:
        try:
            manual_tmdb_id = int(tmdb_id_raw)
        except ValueError:
            from urllib.parse import quote
            raise _redirect_with_flash('TMDB ID must be numeric')

    if not new_title:
        from urllib.parse import quote
        raise _redirect_with_flash('Title is required')

    # Capture what the operator typed so we can decide whether to
    # re-enrich after the caption write.
    item_before = media_index.get_item(message_id)
    title_changed = item_before and item_before.title != new_title
    year_changed = item_before and item_before.year != new_year

    def apply(entry, item):
        entry.title = new_title
        entry.year = new_year
        entry.tags = new_tags
        entry.description = new_description
        # file_name override — not in the caption format, applied directly.
        item.file_name = new_file_name
        # Series assignment — groups standalone videos into a series page.
        if new_series_title:
            item.series_title = new_series_title
            item.series_key = series_parse.slugify(new_series_title)
            item.season = new_season if new_season is not None else 1
            item.episode = new_episode
            item.movie_key = ""
        else:
            # Clearing series_title converts back to a standalone item.
            item.series_title = ""
            item.series_key = ""
            item.season = None
            item.episode = None
            if not item.movie_key:
                item.movie_key = compute_movie_key(
                    new_title, new_year, new_file_name or item.file_name
                )

    status, reason = await _rewrite_caption(message_id, apply)

    # Manual TMDB-id override wins over everything. Fetch the record
    # immediately (this isn't fire-and-forget — admin is waiting for
    # the result and a failure should be reported) and apply it,
    # which also writes the canonical metadata back to BIN.
    manual_tmdb_status = ""
    if (status in ("written", "local-only")
            and manual_tmdb_id is not None):
        ok = await media_index.enrich_with_tmdb_id(
            message_id, manual_tmdb_id, tmdb_kind, bot=StreamBot,
        )
        manual_tmdb_status = "applied" if ok else "failed"

    # If the title or year changed (and there's no manual override) and
    # TMDB is configured, retry the search-based lookup so misclassified
    # entries can be corrected by editing alone. Reset the existing
    # TMDB ID so enrich_one searches fresh by the new title rather than
    # just refreshing the old record. Skip for 'removed' / 'failed' —
    # there's nothing to re-enrich.
    elif status in ("written", "local-only") and (title_changed or year_changed):
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
        # Surface the specific Telegram error code so the operator
        # sees the real cause instead of a one-size diagnosis.
        # https://core.telegram.org/method/messages.editMessage
        if reason == "edit-time-expired":
            cause = (
                "Telegram returned MESSAGE_EDIT_TIME_EXPIRED. Bots can "
                "only edit their own messages within 48 hours; this "
                "message is older. The in-memory entry was updated."
            )
        elif reason == "author-required":
            cause = (
                "Telegram returned MESSAGE_AUTHOR_REQUIRED. Usually "
                "means the message was forwarded into BIN_CHANNEL (the "
                "'Forwarded from' header makes the caption non-editable "
                "even for the forwarder) or posted by another author. "
                "New uploads now use copy() instead of forward() to "
                "avoid this; pre-existing forwarded entries stay "
                "editable in memory only."
            )
        elif reason == "message-id-invalid":
            cause = (
                "Telegram returned MESSAGE_ID_INVALID but a probe "
                "confirmed the message still exists. Likely the bot "
                "lacks edit permission on this specific message. "
                "In-memory entry was updated."
            )
        else:
            cause = (
                "Telegram refused the caption edit. The in-memory "
                "entry was still updated."
            )
        msg = f"Updated bin:{message_id} in the catalogue. {cause}"
        if title_changed or year_changed:
            msg += " Re-enrich queued."
    elif status == "removed":
        msg = (
            f"bin:{message_id} doesn't exist on BIN_CHANNEL anymore — "
            "removed from the catalogue. Refresh to drop the row."
        )
    else:
        msg = f"Edit failed for bin:{message_id} (see server logs)"
    raise _redirect_with_flash(msg)


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
        await media_index.remove(mid, bot=StreamBot)
        deleted += 1
    return deleted


async def _rewrite_caption(message_id: int, mutate) -> Tuple[str, str]:
    """Fetch a BIN message, rebuild its IndexEntry, mutate, and persist.

    ``mutate(entry, item)`` modifies the IndexEntry in place. Returns
    ``(status, reason_code)``:

    Status:
      • ``"written"`` — caption was edited on Telegram.
      • ``"local-only"`` — Telegram refused the edit; in-memory only.
      • ``"removed"`` — the message truly no longer exists.
      • ``"failed"`` — unexpected error; no state changes.

    ``reason_code`` distinguishes WHY a local-only happened so the
    admin sees the right diagnosis. Per
    https://core.telegram.org/method/messages.editMessage the
    documented edit errors are MESSAGE_ID_INVALID,
    MESSAGE_AUTHOR_REQUIRED, MESSAGE_EDIT_TIME_EXPIRED, and a few
    others. We pass through the specific code so the flash message
    can name the cause instead of guessing.
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
    # MESSAGE_EDIT_TIME_EXPIRED — Telegram caps bot self-edits at 48h
    # for private/group chats. Channels normally don't apply this to
    # admin posts, but forwarded posts sometimes do.
    _EDIT_TIME_EXPIRED: tuple = ()
    try:
        from pyrogram.errors.exceptions.bad_request_400 import (
            MessageEditTimeExpired,
        )
        _EDIT_TIME_EXPIRED += (MessageEditTimeExpired,)
    except ImportError:
        pass

    item = media_index.get_item(message_id)
    if item is None:
        return "failed", "no-item"
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

    # Mongo holds the authoritative catalogue now, so the BIN caption
    # rewrite is redundant — and historically the source of the
    # MESSAGE_AUTHOR_REQUIRED errors on legacy forwarded entries.
    # Skip the Telegram edit entirely; apply locally + Mongo only.
    if media_index._store_active():
        _apply_local_only(message_id, entry)
        await media_index._store_upsert(media_index.get_item(message_id))
        return "written", ""

    try:
        await StreamBot.edit_message_caption(
            chat_id=Var.BIN_CHANNEL,
            message_id=message_id,
            caption=render(entry),
        )
    except MessageNotModified:
        return "written", ""
    except MessageIdInvalid:
        # MESSAGE_ID_INVALID is overloaded: it fires both when the
        # message truly is gone AND when the bot can't reach it (often
        # a forwarded post that the bot didn't originate). Probe via
        # get_messages — if it comes back non-empty, the message
        # exists and we just can't edit its caption.
        try:
            probe = await StreamBot.get_messages(Var.BIN_CHANNEL, message_id)
            still_exists = probe is not None and not getattr(probe, "empty", False)
        except Exception:
            still_exists = False
        if still_exists:
            logging.info(
                "admin: bin:%d MESSAGE_ID_INVALID but exists; in-memory only",
                message_id,
            )
            _apply_local_only(message_id, entry)
            return "local-only", "message-id-invalid"
        logging.info(
            "admin: bin:%d truly absent on Telegram; removing", message_id,
        )
        await media_index.remove(message_id, bot=StreamBot)
        return "removed", ""
    except _EDIT_TIME_EXPIRED as exc:
        logging.info(
            "admin: bin:%d MESSAGE_EDIT_TIME_EXPIRED (%s); in-memory only",
            message_id, exc.__class__.__name__,
        )
        _apply_local_only(message_id, entry)
        return "local-only", "edit-time-expired"
    except _UNEDITABLE as exc:
        logging.info(
            "admin: bin:%d caption read-only (%s); skipping caption write",
            message_id, exc.__class__.__name__,
        )
        _apply_local_only(message_id, entry)
        return "local-only", "author-required"
    except Exception:
        logging.exception("admin: edit_caption failed for bin:%d", message_id)
        return "failed", "unknown"

    # Refresh the in-memory entry from the rewritten caption.
    try:
        fresh = await StreamBot.get_messages(Var.BIN_CHANNEL, message_id)
        await media_index.add_from_message(fresh)
    except Exception:
        logging.exception("admin: post-edit refresh failed for bin:%d", message_id)
    return "written", ""


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
        status, _reason = await _rewrite_caption(mid, apply)
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
        status, _reason = await _rewrite_caption(mid, apply)
        if status in ("written", "local-only"):
            n += 1
    return n
