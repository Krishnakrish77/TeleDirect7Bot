# TeleDirect — Feature Audit vs Industry
_Benchmarked against: Netflix, Disney+, Prime Video, Apple TV+, YouTube, JioHotstar, Spotify, Apple Music, YouTube Music_
_2026-05-26_

Legend: 🟢 Table stakes · 🟡 Differentiator · 🔵 Innovative · ✅ Have it · ⚠️ Partial · ❌ Missing

---

## PERFORMANCE OBSERVATIONS — 2026-07-02

_Evidence: Webwright runs `outputs/perf_audit_live/final_runs/run_3`, `run_4`, `run_5`, and `run_6`, local Vite/Playwright smoke checks, and production Vite build output._

| Area | Before | After | Observed improvement |
|------|--------|-------|----------------------|
| React initial JS | `425.67 KB` raw / `119.15 KB` gzip main bundle | `290.82 KB` raw / `87.67 KB` gzip main bundle after route-level lazy chunks | `-134.85 KB` raw (`31.7%` smaller), `-31.48 KB` gzip (`26.4%` smaller). Heavy routes now split into watch, detail, Live TV, admin, playlists, stats, watchlist, liked songs, and add-to-playlist chunks. |
| Live TV initial load | Deployed direct `/app/live-tv`: `15.461s` to network idle, `250` resources, `245` image requests, `9` warnings/failed requests; first channel stream/autoplay started on route load. | Latest deployed direct `/app/live-tv` run 6: `7.016s` to network idle, `31` resources, `24` image requests, `0` console warnings/errors, `0` failed requests, `0` HTTP errors, no video autoplay. | `-8.445s` network-idle time (`54.6%` faster), `-219` resources (`87.6%` fewer), and `-221` image requests (`90.2%` fewer) versus the original deployed baseline. |
| Live TV logo loading | Public Live TV responses returned raw third-party logo URLs, so browsers opened remote image requests directly; the deployed audit saw `245` image requests on `/app/live-tv`. | Public channel list/detail responses now rewrite every non-empty `logoUrl` to same-origin `/api/live-tv/logo/{id}?v={hash}` URLs. The proxy validates public hosts, caps images at `512 KB`, caches valid logos for `24h`, returns cacheable local SVG placeholders for bad upstream logos, and negative-caches failures for `6h`. | Browser-side direct third-party logo requests for public channel logos dropped to `0`. Run 6 reconfirmed `24` proxied logo images, `0` direct third-party images, and `0` logo HTTP errors. |
| SPA Home `/api/hub` | Webwright deployed Home: `4.196s` to network idle on stable run; earlier cold run observed `14.773s`. Run 5 `/api/hub` transfer was `68,614 B` and rebuilt per request. | Backend now caches anonymous default Home JSON for `30s`, caches filter metadata for `30s`, logs slow `/api/hub` section timings, invalidates on media-index changes, and sends compact hub card payloads. Run 6 Home was `5.560s` to network idle with `/api/hub` at `1.540s`, `21,312 B` transfer, and `154,013 B` decoded body. | `/api/hub` transfer dropped from `68,614 B` to `21,312 B`, a `-47,302 B` reduction (`69.0%` smaller). Repeated anonymous Home requests still skip shelf construction, trending/top-play waits, card serialization, and JSON encoding during the TTL. |
| Anonymous Continue Watching | Run 5 Home still made `/api/cw` while signed out, producing `1` avoidable `401` response on every anonymous Home load. | Continue Watching now receives the signed-in state from App. Anonymous sessions use only local `td:cw` resume data and still hydrate local cards through `/api/items`; signed-in sessions keep server sync. | Run 6 Home recorded `cw_requests=0` and `http_errors=0`, confirming `-1` request and `-1` HTTP error for anonymous Home. |
| TMDB artwork loading | Run 5 Home loaded `11` browser-side third-party TMDB image resources. | SPA API TMDB artwork URLs now point to same-origin `/api/tmdb-image/{size}/{path}`. The proxy accepts only known TMDB image sizes and image-looking relative paths, caps responses at `2 MB`, caches successful images for `24h`, and caches local placeholders for failed images for `6h`. | Run 6 Home recorded `tmdb_proxy_images=11`, `direct_tmdb_images=0`, and `third_party_images=0`, confirming direct browser-side TMDB image requests dropped from `11` to `0`. |
| React TMDB artwork coverage | After the Home fix, Stats, Watchlist/Liked Songs, and top-search suggestions could still send direct browser requests to `image.tmdb.org`. | TMDB image proxy logic now lives in a shared server helper. Stats payloads, Watchlist/Liked Songs payloads, and React search suggestion thumbnails now all emit `/api/tmdb-image/{size}/{path}` URLs. | Run 6 search-suggestion probe rendered suggestions and recorded `direct_tmdb_images=0`, `third_party_images=0`, and `tmdb_proxy_images=13`, confirming search artwork stayed same-origin. |
| SPA hub card payload | Home `/api/hub` still sent detail/watch-only fields on every shelf and grid card, including duplicate thumbnail/backdrop URLs, file-size metadata, raw IDs, tags, overview, IMDb links, stream URLs, and legacy eyebrow/badge fields. | `/api/hub` shelf/grid cards now use a compact card payload that keeps only fields consumed by the React hub renderer. Hero, detail, watch, playlist, and library payloads remain unchanged. | Local representative card JSON shrank from `895 B` to `475 B` (`-420 B`, `46.9%` smaller) by dropping `16` unused fields per hub card. Deployed `/api/hub` transfer dropped `68,614 B` -> `21,312 B` (`-47,302 B`, `69.0%` smaller). |

---

## LIVE TV UX OBSERVATIONS — 2026-07-03

_Evidence: Live TV code audit plus frontend tests for channel category, favorite, recent, and search states._

| Area | Audit finding | UX improvement |
|------|---------------|----------------|
| Channel rail orientation | The optimized Live TV page was fast, but the channel rail did not summarize the active category/search view. Empty favorite, recent, and no-match states all collapsed into generic copy. | The rail now shows the current result count and active view, gives a one-click clear-filter recovery path, and uses specific empty-state copy for search misses, favorites, and recents. |
| Category navigation semantics | Category controls were visually tab-like inside a tablist but exposed as generic buttons. | Category controls now expose `role="tab"` with `aria-selected`, making the current channel view easier to understand for assistive tech. |
| Playback state clarity | The now-playing header did not distinguish a selected channel from an actively playing channel. | The current channel row now shows a compact `Selected` or `Playing` state chip. |

---

## VIDEO / OTT

### Discovery & Browse

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Personalized homepage with algorithmic rows | 🟢 All | ✅ Hero + genre shelves | — |
| "Because you watched…" recommendations | 🟢 All | ✅ TMDB-seeded, auth-gated | — |
| Continue Watching row | 🟢 All | ✅ Cross-device CW | — |
| Search with autocomplete | 🟢 All | ✅ Live suggest, keyboard shortcuts | — |
| Genre / tag filtering | 🟢 All | ✅ Year / quality / genre dropdowns | — |
| Trending / Top content charts | 🟡 Netflix, YouTube, JioHotstar | ✅ Trending + Most Played Home shelves | — |
| Trailer auto-play on browse | 🟢 All | ⚠️ Trailers on movie/series page only, not on cards | Medium |
| Search by cast / crew name | 🟡 Most | ⚠️ Built, dormant until credits backfill | **Ops** — run Admin → Ops → Backfill credits, then validate live cast/director searches |

### Playback

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Next episode auto-play + countdown | 🟢 All | ✅ 5s countdown card | — |
| Subtitles / CC | 🟢 All | ✅ Auto-inject + manual upload | — |
| Multiple audio tracks | 🟢 All | ✅ HLS audio track switcher | — |
| Picture-in-Picture | 🟢 All | ✅ React player PiP control | — |
| Playback speed | 🟡 Netflix, YouTube, Prime | ✅ React player speed selector | — |
| Skip intro button | 🟢 All SVOD | ✅ Admin sets timestamps; button appears only within intro window | — |
| Skip recap button | 🟡 Netflix, Disney+, JioHotstar | ✅ Admin sets timestamps; button appears only within recap window | — |
| "Are you still watching?" prompt | 🟢 All | ✅ React player pauses unattended playback after 45 minutes and offers a resume action | — |
| Video chapters / timestamps | 🟡 YouTube | ✅ Admin line-entry chapters with React progress markers and chapter list | — |
| Per-title thumbs / rating | 🟡 Prime Video, YouTube | ✅ Up/down, toggle-off, auth-gated, feeds recommendations | — |
| Keyboard shortcuts | 🟢 All web apps | ✅ React player global keys | — |
| Adaptive quality selector | 🟢 All | ⚠️ Manual variant picker (not truly adaptive) | Low — architecture limitation |
| X-Ray style cast overlay during playback | 🔵 Prime Video exclusive | ⚠️ TMDB info section below player | Low — partial coverage adequate |

### Personal Library

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Watchlist / My List | 🟢 All | ✅ MongoDB-backed, 1000-item cap | — |
| Watch history | 🟢 All | ✅ Used as recommendation signal | — |
| Cross-device sync | 🟢 All | ✅ MongoDB for signed-in users | — |
| Viewing stats / activity page | 🟡 Netflix, YouTube | ✅ `/stats` — hours watched, heatmap, streaks, top titles | — |
| Per-title ratings visible on library | 🟡 Most | ✅ Aggregate thumbs-up/down counts now appear on rated React library cards | — |

### Social / Sharing

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Share link to title | 🟢 All | ✅ Web Share API + clipboard fallback | — |
| Watch party / co-viewing | 🟡 Disney+ (GroupWatch), Apple TV+ (SharePlay) | ❌ | Low — complex WebSocket infra; not worth it at personal-app scale |
| Shared / collaborative watchlist | 🟡 Spotify-style (music), some OTT | ❌ | Medium — share a list with a specific user |

### Notifications

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| New episode / content push notification | 🟢 All | ❌ | Medium — product decision; needs Push API, VAPID keys, subscription storage, opt-in UX, and a server sender |
| Recommendation push notification | 🟢 All | ❌ | Low — only after opt-in notification infrastructure exists |

### Profiles & Access

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| User profiles | 🟢 All | ⚠️ Auth via Telegram, single profile per user | Low — Telegram = identity; no need for sub-profiles |
| Parental controls | 🟢 All | ❌ | Low — public app, not family-oriented |

### Offline

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Download to device | 🟢 All | ⚠️ Direct download link; no offline playback | Low — stream URLs work without app; offline player complex |
| PWA caching of UI shell | 🟡 Progressive apps | ✅ Service worker registered | — |

---

## MUSIC

### Discovery & Browse

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Album / artist pages | 🟢 All | ✅ Album page with track list + art | — |
| Mood / genre radio stations | 🟢 All | ❌ | Medium — seed from genre tags; shuffle filtered playlist |
| Artist page (all tracks by one artist) | 🟢 All | ✅ `/artist/{slug}`; splits multi-credit correctly; primary artist linked | — |
| Recently played row (music) | 🟢 All | ⚠️ Covered by Continue Watching but not music-specific | Medium |
| Charts (top tracks in library) | 🟡 All streaming apps | ❌ | Medium — play-count from CW events |

### Playback

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Shuffle + repeat modes | 🟢 All | ✅ Per-album shuffle + repeat off/all/one | — |
| Playback speed selector | 🟡 YouTube Music (full), others podcasts only | ✅ ¾×/1×/1.5×/2× | — |
| Persistent mini-player | 🟢 All | ✅ hx-preserve bottom bar | — |
| Synced lyrics | 🟢 All | ✅ LRCLIB with scroll + tap-to-seek | — |
| Crossfade between tracks | 🟢 All | ✅ 3s crossfade via dual bgAudio buffers, volume ramp | — |
| Gapless playback | 🟢 All | ✅ Dual-buffer (bgAudio + bgAudio2), pre-loads 30s before end | — |
| Equalizer | 🟢 All | ❌ | Low — Web Audio API EQ possible but complex |
| Karaoke / vocal isolation | 🔵 Apple Music Sing only | ❌ | Low — requires server-side stem separation |

### Queue Management

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Manual queue ("Play next" / "Add to queue") | 🟢 All | ✅ "Play next" + "Add to queue" on track rows; playlist queue; toast feedback | — |
| Smart Shuffle (injects recommendations into queue) | 🟡 Spotify | ❌ | Low — needs recommendation quality to be high first |

### Personal Library

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Liked / favourite tracks | 🟢 All | ⚠️ Watchlist covers this but not music-optimised | Medium — dedicated "Liked songs" auto-playlist |
| Listening stats (Spotify Wrapped-style) | 🟡 Spotify (iconic) | ✅ `/stats` — streaks, top artists/genres, play counts, personality card | — |
| Smart playlists from library | 🟡 Spotify, Apple Music | ❌ | Low |

### Social

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Share track (Web Share) | 🟢 All | ✅ | — |
| Collaborative playlist / shared album | 🟡 Spotify, Apple Music | ❌ | Low |
| Friend activity / what's playing | 🟡 Spotify | ❌ | Low |

### Lyrics

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Synced lyrics | 🟢 All | ✅ LRCLIB | — |
| Lyrics translation | 🟡 Apple Music Sing | ❌ | Low |
| Karaoke mode | 🔵 Apple Music exclusive | ❌ | Low |

---

## DELIVERY LOG — 2026-05-29 (session 2 additions)

Large batch shipped since the last audit. Validated against the live deployment (catalogue ~492 items):

| Feature | Status | Validation |
|---------|--------|------------|
| **Playlists** | ✅ Shipped | `/playlists` + `/playlist/{id}` (302 auth-gated), `/api/playlists` 401 unauth. Create/rename/delete, add/remove tracks, Play all/Shuffle, per-track play-from-position, watch-page picker with inline "New playlist". 50 playlists / 500 tracks per user cap. XSS-hardened (name via `|tojson`), secure_hash re-validated on enrich. **Needs signed-in manual pass to confirm UI flows.** |
| **Stats / listening insights** | ✅ Shipped | `/stats` 302 auth-gated. Current + longest streak, video/audio hours split, 12-week day heatmap (UTC-correct), top-3 artists, genres, play counts, "personality" card (gated ≥10 plays), most-played grouped by title. |
| **Person (cast/crew) pages** | ⚠️ Built, dormant | `/person/{slug}` route registered (bad slug → 404, not 500). Template + `items_by_cast_slug`/`items_by_director_slug` done. **But every real person 404s and no `/person/` links render — existing catalogue has no enriched cast/crew data.** |
| **Searchable cast/crew** | ⚠️ Built, dormant | Search index extended for `cast[]`/`director`. Live "nolan"/"arnold"/"dicaprio" → 0 results. Same data blocker as above. |
| **Admin custom thumbnail** | ✅ Shipped | Edit-modal URL field downloads + stores to thumb cache; `__clear__` sentinel reverts to auto-detect. Modal raised to z-50 so mini-player no longer hides Save. |
| **Security & perf hardening** | ✅ Shipped & verified | Live response headers confirmed: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`, HSTS `max-age=31536000`. HMAC `compare_digest` for hash check, VLC token 64→128-bit. |
| **AI suggest (music)** | ✅ Shipped | Music-specific prompt + schema for audio items in admin enrichment. |
| **App icon + PWA orientation** | ✅ Shipped | Redesigned icon (content-hash versioned URLs, SW bumped to td-v2); portrait manifest + JS landscape lock for fullscreen video. |

**Single biggest unlock:** a one-time **TMDB credits backfill** on the existing catalogue would light up *three* already-built features at once — person pages, cast/crew search, and cast links on detail pages. The code is all shipped and waiting on data.

⚠️ **Note on testing:** the app restarts frequently (Koyeb) — I caught it mid-redeploy and saw a transient `/playlists` 500 + missing security headers; both were clean once the deploy settled. Validate during a stable window.

---

## DELIVERY LOG — 2026-05-29 (session 1 — earlier in day)

| Feature | Status | Validation |
|---------|--------|------------|
| **Playlists** | ✅ Shipped | `/playlists` + `/playlist/{id}`, full CRUD API. 50/500 caps. |
| **Stats / listening insights** | ✅ Shipped | `/stats` auth-gated, heatmap UTC-correct, top artists/genres. |
| **Security hardening** | ✅ Shipped | `X-Content-Type-Options`, `X-Frame-Options`, HSTS, `hmac.compare_digest`. |
| **FLAC / audio quality badge** | ✅ Shipped | ffprobe probes audio stream for codec/sample_rate/bit_depth; watch page shows "✦ FLAC · 24-bit · 96 kHz · Lossless" chip. |
| **Admin_locked — protect manual edits** | ✅ Shipped | Admin edits auto-lock title/year/series_title; enrichment skips locked fields; amber 🔒 chip in edit modal with per-field ✕ unlock. |
| **Back-navigation ghost content** | ✅ Fixed | `htmx:historyRestore` now strips stale x-for snapshot nodes (stops at `<template>`) and uses `hasAttribute()` for `:style` removal (previous `querySelectorAll('[\\:style]')` was a SyntaxError that crashed the entire handler). |
| **Phantom scrollbars** | ✅ Fixed | `html{overflow-x:hidden}` (hero w-screen overflow), `#main-content{min-height:100dvh}` (iOS 100vh phantom scroll). |
| **Playlist race condition** | ✅ Fixed | `add_track` replaced two-step `$pull`+`$push` with single atomic aggregation-pipeline update. |

---

## PRIORITISED BACKLOG

### 🔴 High — Table stakes gap or very high return for low effort

| # | Feature | Effort | Why it matters |
|---|---------|--------|----------------|
| 1 | **TMDB credits backfill + validation** | Ops | Admin → Ops → Backfill credits fills missing cast/director from existing TMDB IDs without title rematching. Run after deploy, then verify cast search and person pages with real catalogue names. |
| 2 | ~~**Skip intro button**~~ | ✅ Done | Admin timestamps + timeupdate JS; button appears only within intro window. |
| 3 | ~~**Per-title thumbs up/down**~~ | ✅ Done | Up/down toggle, auth-gated, aggregate counts shown, feeds recommendation engine. |
| 4 | ~~**Artist page**~~ | ✅ Done | `/artist/{slug}` with multi-credit splitting; primary artist linked from player. |
| 5 | ~~**Trending / Top charts shelf**~~ | ✅ Done | Trending and Most Played Home shelves now cover active discovery from watch/listen signals. |
| 6 | ⚠️ **Searchable cast / crew** | Code done, **backfill-dependent** | Search index and person pages are implemented. They become useful once existing catalogue items have `cast[]`/`director` from the metadata backfill. |

### 🟡 Medium — Clear user value, moderate effort

| # | Feature | Effort | Why it matters |
|---|---------|--------|----------------|
| 7 | ~~**Gapless playback + crossfade**~~ | ✅ Done | Dual-buffer bgAudio/bgAudio2, 3s crossfade, pre-loads 30s before end. |
| 8 | ~~**Manual queue ("Play next")**~~ | ✅ Done | Play next + Add to queue on track rows; toast feedback. |
| 9 | ~~**"Are you still watching?" prompt**~~ | ✅ Done | React player pauses unattended playback after 45 minutes, shows a fullscreen-safe prompt, and resumes through the normal video play path. |
| 10 | ~~**Video chapters**~~ | ✅ Done | Admin line-entry chapters render as progress markers and a seekable chapter list in the React player. |
| 11 | ~~**Viewing / listening stats page**~~ | ✅ Done | Streaks, video/audio time split, day-of-week heatmap, top artists/genres, play counts, "personality" card. Auth-gated `/stats`. |
| 12 | ~~**Watchlist remove on mobile**~~ | ✅ Done | Saved cards expose a visible tick/check button with `Remove from watchlist`; mobile CSS keeps card actions visible. |
| 13 | ~~**Recommendations discoverability**~~ | ✅ Done | Signed-out Home users now see a personalized recommendations teaser that opens sign-in. |
| 14 | **Push notifications for new content** | Product decision | Not a quick gap: service worker exists for caching/navigation only. Push requires VAPID config, PushManager subscription UI, persistence, opt-out, and server-side delivery. |

### ⚪ Low — Nice-to-have or constrained by architecture

| # | Feature | Why low |
|---|---------|---------|
| Adaptive bitrate | Architecture uses Telegram as CDN; true ABR requires a media server |
| Parental controls | Not a family-oriented use case |
| Watch party / co-viewing | WebSocket infra complexity not justified at personal-app scale |
| Karaoke / vocal isolation | Requires server-side stem separation (Demucs etc.) |
| Equalizer | Web Audio API; high complexity, niche use |
| Lossless / spatial audio | Depends entirely on source file quality |
| Offline playback | Stream URLs work without the app; native offline is complex |
| Collaborative playlists | Low social graph size at personal-app scale |

---

## TeleDirect vs Industry Summary

| Dimension | vs Netflix/Disney+ | vs Spotify/Apple Music |
|-----------|-------------------|----------------------|
| Core playback | ★★★★★ — Skip intro + PiP + captions + speed | ★★★★★ — Gapless + crossfade + queue + lyrics |
| Discovery | ★★★★☆ — Trending shipped; cast search built but data-blocked | ★★★★☆ — Artist page shipped; missing radio/charts |
| Personal library | ★★★★★ — CW + watchlist + playlists + stats + FLAC quality badge | ★★★★★ — Playlists + stats + listening insights shipped |
| Notifications | ★★☆☆☆ — Zero push notifications | ★★☆☆☆ — Same gap |
| Social | ★★★☆☆ — Share works, nothing else | ★★★☆☆ — Share works, nothing else |
| Mobile UX | ★★★★★ — Gestures, PWA, safe-area, no phantom scrollbars | ★★★★★ — Mini-player, landscape, back-nav fixed |
| Auth / profiles | ★★★★☆ — Telegram Login; single profile | N/A |

---

## DELIVERY LOG — 2026-06-02 (React UI — admin console + video options menu + lyrics flip card)

_Audited via live Vite dev server + Chrome DevTools MCP. Viewports tested: 500px (Chromium default) and 1280×800._

| Feature | Status | Notes |
|---------|--------|-------|
| **React admin console** (`/app/admin`) | ✅ Shipped | Auth gate, hero metrics (movies/episodes/tracks/cleanup), live pipeline status with progress bars (6 workers), maintenance action grid, bulk-select bar with tag/quality/series/TMDB fields, paginated item list with hide/unhide. 2.5s polling auto-starts when any worker is running. Classic admin link preserved. |
| **LyricsFlipCard in audio watch** | ✅ Shipped | Album art replaced with flip card; "Lyrics" badge overlay triggers flip to synced lyrics panel. Track art + lyrics side-by-side on desktop, stacked on mobile. LRCLIB lyrics load on demand only when flipped. |
| **Video options menu (⋮)** | ✅ Shipped | All secondary controls (Autoplay, Captions, Volume, Load Subtitles, Audio, Source, Speed, AirPlay, Classic, VLC, Download, Share) moved from standalone `video-actions` section into an overlay menu triggered by `MoreVerticalIcon`. 2-column grid layout at ≥780px wide. |

### Bugs found

| # | Severity | Issue | Fix |
|---|----------|-------|-----|
| 1 | ✅ Fixed | **Video options menu: top 4 rows unreachable on all phones.** At `max-width: 680px`, `max-height: min(54svh, 24rem)` sized the menu against viewport height instead of the clipped 16:9 shell. | Fixed with `max-height: min(54svh, 24rem, calc(100% - 5.5rem))` at the mobile breakpoint. |
| 2 | ✅ Fixed | **Subtitle status shown twice.** `subtitleStatus` rendered both inside the menu and as a standalone `.subtitle-status` node. | Fixed: subtitle upload status now renders only as `.video-menu-status`; covered by `watch.test.tsx`. |
| 3 | ✅ Fixed | **LyricsFlipCard: hidden face exposed to screen readers.** The flip used visual hiding only. | Fixed with `aria-hidden` on the inactive flip face; covered by `lyrics.test.tsx`. |

### Minor observations

- ✅ **Quality shown twice in video titlebar** — fixed with a `displaySubtitle` guard and covered by `watch.test.tsx`.
- ✅ **Admin status-poll timer recreates on every response** — fixed by depending on `statusRunning(data?.status)` instead of the mutable status object; covered by `adminPage.test.tsx`.
- ✅ **`.subtitle-status { flex-basis: 100% }` is dead CSS** — stale selector removed; uploaded subtitle status now renders only inside the video options menu and is covered by `watch.test.tsx`.

---

## React Frontend UI/UX Audit — `/app`
_Tested: 2026-05-31 · Viewports: 1440px desktop, 768px tablet, 390px mobile_
_Tool: Chrome DevTools MCP — live screenshots + DOM inspection_

### Responsive Behaviour

| Viewport | Layout | Assessment |
|---|---|---|
| 1440px desktop | Left sidebar (icon+label) + top nav | Works but sidebar duplicates top nav |
| 768px tablet | Top header only, bottom nav | Clean, good |
| 390px mobile | Top header, bottom nav, 2-col cards | Solid |

---

### 🔴 Critical

| # | Issue | Detail |
|---|---|---|
| 1 | ⚠️ **Hero title hygiene is data-dependent** | React hero now uses `series_title` for series and `dir="auto"` on the heading. Validate production after metadata backfill; fix catalogue data if dirty `series_title` values remain. |
| 2 | ✅ **Shelf overflow affordance fixed** | Desktop shelf rows now render rail controls; old scrollbar-only observation is stale. |
| 3 | ✅ **Duplicate desktop sidebar removed** | Current React shell has one primary nav, not the old left sidebar + top-nav duplication. |
| 4 | ✅ **Episode identifier removed from hero code path** | Hero payload uses series title for series items; any remaining raw `SxxEyy` display should be treated as dirty indexed data, not missing UI logic. |
| 5 | ⚠️ **Album thumbnail watermarks are fallback/data-dependent** | TMDB poster URLs already take precedence over file thumbnails. Watermarks can still appear when no clean remote artwork exists. |

---

### 🟡 High Priority

| # | Issue | Detail |
|---|---|---|
| 6 | ✅ **Eyebrow labels fixed** | Current `.eyebrow` text is 13px, mixed-case, and muted instead of all-caps orange. |
| 7 | ✅ **Sparse search footer fixed** | Grid views now show a result footer such as "Showing all X results" when pagination is exhausted. |
| 8 | ✅ **Card file-size hierarchy fixed** | Home/grid cards derive display metadata from genre/year/duration/quality/rating. File size remains only where it is useful, such as version/detail rows. |
| 9 | 🟢 **Badge overlay consistency** | Polish-only. No current evidence that this blocks usability. |
| 10 | ✅ **Classic button label fixed** | React detail/watch surfaces now say "Classic player" and the detail action has an explanatory title. |
| 11 | 🟢 **Partial episode count badge** | Not actionable without a reliable total-episode source. Current badge reflects indexed episodes, not the complete show catalogue. |

---

### 🟠 Medium Priority

| # | Issue | Detail |
|---|---|---|
| 12 | ✅ **Sidebar active-state note stale** | The React shell no longer has the old desktop sidebar. |
| 13 | ✅ **Desktop search width capped** | Header search is capped around 30rem instead of the older oversized 44rem width. |
| 14 | 🟢 **Tablet category/nav placement** | Keep as visual QA only; current hero height is reduced and primary nav remains separate from shelf content. |
| 15 | ✅ **Skeleton loading states shipped** | Shared loading skeletons cover hub/admin/list-style transitions. |
| 16 | ✅ **Orange overuse reduced** | Eyebrows and passive labels are muted; orange is less overloaded as decorative metadata. |

---

### 🟢 Low Priority / Polish

| # | Issue |
|---|---|
| 17 | "Sign in" as bottom-nav 5th item — auth is not a destination; keep it top-right header only |
| 18 | File size units inconsistent: `1.22 GiB` vs `893.77 MiB` — normalise to GiB or remove entirely |
| 19 | Hero description has no `line-clamp` on mobile — long descriptions push Play button off-screen |
| 20 | No active/pressed state on card touch — hover-only feedback doesn't work on touchscreens |
| 21 | "LIBRARY" label above every shelf is decorative noise; removing it tightens visual rhythm |

---

### ✅ What's Working Well

- Movie detail page: excellent backdrop, poster, genre chips, cast/director, version picker, related shelf
- Mobile 2-column grid: correctly sized with good touch targets
- Dark theme: colours, shadows, and backgrounds are cohesive throughout
- Bottom navigation: properly spaced with safe-area insets on mobile
- Search results: filter bar + grid renders correctly, empty-state for no-results works
- Hero carousel: thumbnail strip provides good wayfinding

---

### Recommended Fix Priority for the Other Agent

**Quick wins (1–2 hours):**
1. Fix RTL hero title — sanitise `series_title` before passing to hero component
2. Remove sidebar on desktop ≥1024px — hide via CSS `display:none` in the media query
3. Replace file sizes in card subtitles with year/duration
4. Add `line-clamp-3` to hero description on mobile

**Medium effort (half-day):**
5. Standardise badge component — one spec, one component, used everywhere
6. Add scroll arrows to shelves on desktop
7. Rename/explain the "Classic" button
8. Add skeleton loading screens for navigation transitions
