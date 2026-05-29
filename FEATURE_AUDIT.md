# TeleDirect — Feature Audit vs Industry
_Benchmarked against: Netflix, Disney+, Prime Video, Apple TV+, YouTube, JioHotstar, Spotify, Apple Music, YouTube Music_
_2026-05-26_

Legend: 🟢 Table stakes · 🟡 Differentiator · 🔵 Innovative · ✅ Have it · ⚠️ Partial · ❌ Missing

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
| Trending / Top content charts | 🟡 Netflix, YouTube, JioHotstar | ❌ | **High** — easy win; count CW or play events in MongoDB |
| Trailer auto-play on browse | 🟢 All | ⚠️ Trailers on movie/series page only, not on cards | Medium |
| Search by cast / crew name | 🟡 Most | ❌ Cast data exists in meta, not searchable | **High** — data already there, just needs search index |

### Playback

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Next episode auto-play + countdown | 🟢 All | ✅ 5s countdown card | — |
| Subtitles / CC | 🟢 All | ✅ Auto-inject + manual upload | — |
| Multiple audio tracks | 🟢 All | ✅ HLS audio track switcher | — |
| Picture-in-Picture | 🟢 All | ✅ Plyr native PiP | — |
| Playback speed | 🟡 Netflix, YouTube, Prime | ✅ Plyr speed on video | — |
| Skip intro button | 🟢 All SVOD | ✅ Admin sets timestamps; button appears only within intro window | — |
| Skip recap button | 🟡 Netflix, Disney+, JioHotstar | ❌ | Medium |
| "Are you still watching?" prompt | 🟢 All | ❌ | Medium — simple JS timer, saves Telegram quota |
| Video chapters / timestamps | 🟡 YouTube | ❌ | Medium — Plyr supports chapter markers natively; admin annotates |
| Per-title thumbs / rating | 🟡 Prime Video, YouTube | ✅ Up/down, toggle-off, auth-gated, feeds recommendations | — |
| Keyboard shortcuts | 🟢 All web apps | ✅ Plyr global keys | — |
| Adaptive quality selector | 🟢 All | ⚠️ Manual variant picker (not truly adaptive) | Low — architecture limitation |
| X-Ray style cast overlay during playback | 🔵 Prime Video exclusive | ⚠️ TMDB info section below player | Low — partial coverage adequate |

### Personal Library

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Watchlist / My List | 🟢 All | ✅ MongoDB-backed, 1000-item cap | — |
| Watch history | 🟢 All | ✅ Used as recommendation signal | — |
| Cross-device sync | 🟢 All | ✅ MongoDB for signed-in users | — |
| Viewing stats / activity page | 🟡 Netflix, YouTube | ✅ `/stats` — hours watched, heatmap, streaks, top titles | — |
| Per-title ratings visible on library | 🟡 Most | ❌ | Medium — needed to make ratings useful |

### Social / Sharing

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Share link to title | 🟢 All | ✅ Web Share API + clipboard fallback | — |
| Watch party / co-viewing | 🟡 Disney+ (GroupWatch), Apple TV+ (SharePlay) | ❌ | Low — complex WebSocket infra; not worth it at personal-app scale |
| Shared / collaborative watchlist | 🟡 Spotify-style (music), some OTT | ❌ | Medium — share a list with a specific user |

### Notifications

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| New episode / content push notification | 🟢 All | ❌ | **High** — service worker already registered; one push per new upload |
| Recommendation push notification | 🟢 All | ❌ | Medium — weekly digest via service worker |

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
| 1 | **Push notifications for new content** | Low | Service worker already registered; one VAPID push per upload. Users currently have no way to know when new content arrives. |
| 2 | ~~**Skip intro button**~~ | ✅ Done | Admin timestamps + timeupdate JS; button appears only within intro window. |
| 3 | ~~**Per-title thumbs up/down**~~ | ✅ Done | Up/down toggle, auth-gated, aggregate counts shown, feeds recommendation engine. |
| 4 | ~~**Artist page**~~ | ✅ Done | `/artist/{slug}` with multi-credit splitting; primary artist linked from player. |
| 5 | **Trending / Top charts shelf** | Low | Count play-start events in CW store or a `plays` collection. A "Most watched this week" shelf costs one MongoDB aggregation query. **← only remaining High item.** |
| 6 | ⚠️ **Searchable cast / crew** | Code done, **data blocked** | Search index extended for `cast[]`/`director` — but live searches for "nolan"/"arnold"/"dicaprio" return 0 results because existing catalogue items were never enriched with cast/crew. Same blocker as person pages. **Needs a TMDB credits backfill pass.** |

### 🟡 Medium — Clear user value, moderate effort

| # | Feature | Effort | Why it matters |
|---|---------|--------|----------------|
| 7 | ~~**Gapless playback + crossfade**~~ | ✅ Done | Dual-buffer bgAudio/bgAudio2, 3s crossfade, pre-loads 30s before end. |
| 8 | ~~**Manual queue ("Play next")**~~ | ✅ Done | Play next + Add to queue on track rows; toast feedback. |
| 9 | **"Are you still watching?" prompt** | Low | Saves Telegram API quota and bandwidth. Simple: if paused > 45 min, show prompt and pause stream. |
| 10 | **Video chapters** | Medium | Plyr supports chapter markers via a WebVTT chapters track. Admin annotates timestamps; displayed as seekable chapter marks in the progress bar. |
| 11 | ~~**Viewing / listening stats page**~~ | ✅ Done | Streaks, video/audio time split, day-of-week heatmap, top artists/genres, play counts, "personality" card. Auth-gated `/stats`. |
| 12 | **Watchlist remove on mobile** | Low | Currently hover-only. A long-press or visible X on touch devices fixes this. |
| 13 | **Recommendations discoverability** | Low | Show a teaser shelf ("Sign in to see recommendations") for signed-out users. Currently invisible. |

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
| Discovery | ★★★★☆ — Cast search built but data-blocked; missing trending | ★★★★☆ — Artist page shipped; missing radio/charts |
| Personal library | ★★★★★ — CW + watchlist + playlists + stats + FLAC quality badge | ★★★★★ — Playlists + stats + listening insights shipped |
| Notifications | ★★☆☆☆ — Zero push notifications | ★★☆☆☆ — Same gap |
| Social | ★★★☆☆ — Share works, nothing else | ★★★☆☆ — Share works, nothing else |
| Mobile UX | ★★★★★ — Gestures, PWA, safe-area, no phantom scrollbars | ★★★★★ — Mini-player, landscape, back-nav fixed |
| Auth / profiles | ★★★★☆ — Telegram Login; single profile | N/A |
