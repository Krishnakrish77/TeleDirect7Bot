# TeleDirect — PM Audit
_Audited: 2026-05-24 · Last updated: 2026-05-26 · Live app: https://olympic-lorianne-kksoftsolutions-87c05347.koyeb.app_

---

## What's Genuinely Impressive

**Player quality is best-in-class for a personal streaming app.** Plyr + HLS.js with proper fallbacks, codec detection overlay, platform-specific VLC deep-links (Android intent, iOS vlc-x-callback, desktop vlc://), drag-and-drop subtitle upload with SRT→VTT in-browser conversion, subtitle restoration across refreshes, audio track switching — this is production-grade player engineering.

**The music UX rivals Spotify conceptually.** Persistent mini-player bar that survives navigation via `hx-preserve`, album art flip card with live synced lyrics from LRCLIB API (tap to seek, highlight scrolls), Spotify-style custom scrubber, album shuffle with a sessionStorage queue, auto-advance with a 5-second cancellable countdown, speed selector (¾×/1×/1.5×/2×), and repeat off/all/one modes with per-album state.

**Mobile gesture system is complete.** Double-tap left/right halves to seek ±10s, horizontal swipe to scrub timeline, right-half vertical swipe for volume, left-half vertical swipe for brightness (CSS filter). Three-dot overflow menu in landscape for Download/VLC/Subtitles. Closer to native app feel than most web players.

**Continue Watching is smart — and now cross-device.** Anonymous users get localStorage-only tracking (instant, private). Signed-in users get MongoDB-backed sync with 90-day TTL, 50-entry cap per user, and automatic merge on hub load so cross-device positions are always available.

**TMDB integration is well-executed.** Posters, descriptions, genre tags, trailer embeds (youtube-nocookie.com), IMDb links, air dates, episode stills, director/cast credits, and episode overviews. Admin panel's AI-assisted metadata enrichment is a genuine productivity multiplier.

**AI recommendations are architecturally sound.** Seeds from watch history + watchlist, scored by TMDB co-recommendation frequency, cross-referenced against the actual catalogue, cached 24h in MongoDB. Falls back gracefully when TMDB isn't configured or the user has no history.

**Skeleton fetch optimization** caches the first 2 MB and last 512 KB of each file in memory, eliminating Telegram round-trips for ffmpeg header/cue reads on HLS segments.

**PWA is fully implemented.** Service worker registered, PNG icons, manifest fixes, iOS safe-area insets, engagement-gated install prompt (3+ visits, 10s delay, once per session).

**Error pages are good.** Distinct icon per status code (403/404/5xx), actionable buttons, on-brand.

---

## Critical Issues

### 1. ~~Catalogue access control~~ ✅ Not applicable
The catalogue is intentionally public — anyone with the link can browse and watch. Telegram Login gates user-specific features (Watchlist, Recommendations, cross-device Continue Watching) without restricting discovery.

### 2. ~~Hash + sequential ID = High-severity enumeration risk~~ ✅ Fixed
Hash extended from 6 → 16 characters across all routes. Old 6-char links remain valid via backward-compatible regex. New uploads use 16-char hashes going forward.

### 3. ~~Internal error details leaked to users~~ ✅ Fixed
Both handlers now return `"A server error occurred."` generically. Full tracebacks still logged server-side via `logging.exception`.

### 4. ~~No rate limiting on stream endpoints~~ ✅ Fixed
Per-IP concurrent cap (default 4, env-configurable) + global cap (default 25). Returns 429/503 + `Retry-After` header. Only actual Telegram GetFile calls counted — skeleton cache hits are free. One minor issue: `_ip_active` dict never evicts zero-count entries; benign now but will grow in large deployments.

---

## High-Impact UX Gaps

### 5. ~~Always-visible VLC warning is alarming for normal playback~~ ✅ Fixed
The amber "Note: Player not loading?" banner is now `display:none` on page load — confirmed in live DOM. The VLC button itself stays accessible as a utility, which is the right call.

### 6. ~~No loading indicator during HTMX navigation~~ ✅ Fixed
`#_htmx-bar` orange top bar animates on every navigation, completes on settle. Confirmed in DOM.

### 7. ~~Duplicate search implementations~~ ✅ Fixed
Duplicate Alpine nav dropdown removed. Single global search modal (Cmd+K / `/`). Confirmed: `searchComponentCount: 0`, modal exists.

### 8. ~~Series "All seasons" loads all episodes at once~~ ✅ Fixed (partially)
`<details>/<summary>` accordion — Season 1 open by default, others collapsed. 85 Naruto episodes confirmed in 4 collapsed sections. Not paginated but DOM pressure relieved.

### 9. ~~Subtitle cache in localStorage is unbounded~~ ✅ Fixed
LRU eviction added — oldest subtitle entries evicted when quota is approached.

### 10. Cast/crew data not populated for existing catalogue items
The template code for Director/Cast is correctly implemented in `movie.html` and `series.html`. However, existing items in the catalogue were indexed before enrichment was added and don't have `meta.director` / `meta.cast` populated. These fields will only appear on newly added or admin-re-enriched items. **Action needed:** bulk re-enrichment pass via admin panel, or a background job.

### 11. Watchlist remove button inaccessible on mobile
The remove button on watchlist cards is `opacity-0 sm:group-hover:opacity-100` — hover-only, invisible to touch users. Mobile users have no way to remove individual items without navigating into each one.

---

## Feature Delivery Status

| Feature | Status | Notes |
|---------|--------|-------|
| Telegram Login / Auth | ✅ Live | JWT session, Telegram Login Widget, avatar dropdown |
| Watchlist / Bookmarks | ✅ Live | 194 bookmark buttons on hub; MongoDB-backed; auth-gated |
| Cross-device Continue Watching | ✅ Live | MongoDB sync for signed-in users; merge on hub load |
| AI Recommendations | ✅ Live | TMDB seeds + catalogue cross-ref; 24h cache; auth-gated |
| Watch history (recommendation signal) | ✅ Live | Completed views recorded as seeds |
| Rate limiting | ✅ Live | Per-IP + global concurrent stream caps |
| PWA Service Worker | ✅ Live | Registered; PNG icons; iOS safe-area |
| Search consolidated | ✅ Live | Single modal, no duplicate Alpine component |
| Audio speed selector | ✅ Live | ¾×/1×/1.5×/2×; persists to localStorage |
| Audio repeat modes | ✅ Live | Off/all/one; per-album key; badge on repeat-one |
| Share button | ✅ Live | Web Share API + clipboard fallback |
| Cast/crew display | ⚠️ Partial | Template correct; existing data not enriched |
| Playback speed for video | ✅ (Plyr) | Native Plyr speed control |
| Download progress feedback | ⬜ Open | No visual on mobile after tapping Download |
| Watchlist remove on mobile | ⚠️ Bug | Hover-only, not touchable |
| Recommendations visible without auth | ⬜ Open | No teaser / sign-in prompt to surface the feature |

---

## New Issues Found in This Audit

**Architecture / reliability:**
- `_ip_active` dict in rate limiter never evicts zero-count entries — memory leak risk at scale.
- CW store accepts `pos`/`dur` values without bounds validation (negative, NaN, or `pos > dur` all pass through server-side).
- Watchlist page resolves each item via 5–7 separate `media_index` lookups (N+1 pattern) — will feel slow for users with large watchlists.
- JWT secret auto-generated on cold start if `JWT_SECRET` env var not set — all sessions invalidated on every redeploy unless the var is pinned.

**UX:**
- Recommendations shelf only visible to signed-in users with no sign-in prompt near it — users don't know the feature exists.
- Watchlist page has no skeleton loader — renders fully or nothing, which looks broken on slow connections.
- `/watchlist` redirect to `/` on unauthenticated access gives no feedback about why the redirect happened.

---

## Polish-Level Issues (Remaining)

- **Footer "Powered by Telegram · @TeleDirect7Bot"** leaks infrastructure details to all users.
- **OG image in `req.html`** is a hardcoded GitHub Camo URL — will break if moved.
- **Movie variant page shows raw `file_name`** (e.g., `Movie.Name.2024.1080p.BluRay.x264.mkv`) — looks technical.
- **htmx + Alpine loaded from unpkg CDN** — single point of failure for the entire UI.
- ~~**Admin one-time token in URL**~~ ✅ Fixed — POST bridge with `history.replaceState()`.

---

## Scorecard

| Dimension | Initial | Previous | Current | Change |
|-----------|---------|----------|---------|--------|
| Player / streaming quality | ★★★★★ | ★★★★★ | ★★★★★ | — |
| Music UX | ★★★★☆ | ★★★★★ | ★★★★★ | — |
| Mobile experience | ★★★★☆ | ★★★★★ | ★★★★★ | — |
| Content discovery | ★★★★☆ | ★★★★☆ | ★★★★★ | ↑ Recommendations + search fixed |
| Security / access control | ★★☆☆☆ | ★★★☆☆ | ★★★★★ | ↑ Auth + rate limiting + catalogue public by design |
| Content organisation | ★★★★☆ | ★★★★☆ | ★★★★★ | ↑ Watchlist, cross-device CW |
| Reliability | ★★★☆☆ | ★★★☆☆ | ★★★★☆ | ↑ Rate limiting + service worker |

---

## Remaining Open Items (Priority Order)

1. **Cast/crew bulk re-enrichment** — data gap for existing catalogue; needs an admin job or background pass.
4. **Watchlist remove button on mobile** — touch users can't remove individual items.
5. **Recommendations discoverability** — no sign-in prompt or teaser for signed-out users.
6. **Watchlist N+1 resolution** — will feel slow at scale; needs batch lookup.
7. **CW store: validate pos/dur** — prevent broken playback calculations from bad client values.
8. **CDN dependency (unpkg)** — self-host htmx + Alpine for reliability.
9. **Raw file_name on movie cards** — polish issue; looks technical to end users.
