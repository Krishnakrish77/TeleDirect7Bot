# TeleDirect — Feature Audit vs Industry
_Benchmarked against: Netflix, Disney+, Prime Video, Apple TV+, YouTube, JioHotstar, Spotify, Apple Music, YouTube Music_
_Last validated: 2026-07-19_

Legend: 🟢 Table stakes · 🟡 Differentiator · 🔵 Innovative · ✅ Have it · ⚠️ Partial · ❌ Missing

---

## SR PM VALIDATION — 2026-07-19

_Evidence: current `main` branch code audit, latest 25 commits, existing backend/frontend tests, and official competitor docs/help pages._

### Executive read

TeleDirect has crossed from "Telegram file streamer" into a credible private OTT + music + Live TV hub. The strongest shipped surface is no longer just playback; it is the full loop around playback: personalized Home, cross-device resume, watchlist, playlists, stats, ratings, AI picks, Live TV, and an increasingly serious admin/metadata operations console.

The current product is competitive for a private catalogue and power-user deployment. It is not yet trying to be a family-scale consumer streamer, so gaps like profiles, parental controls, native offline, and social co-viewing should stay scoped unless the product direction changes. The highest-return work is now compounding quality: metadata coverage, discovery explainability, Live TV guide/reminders, and proactive "what's new" loops.

### Benchmark corrections

| Area | Sr PM validation | Decision |
|------|------------------|----------|
| Personalization copy | The old audit over-indexed on "Because you watched..." as generic copy. Current code now prefers `Because you like <genre>` for mixed/partial signals, which is the right trust-preserving direction. However, the user-reported live case `Because you watched The Invisible Guest` after a partial play means this needs signed-in production/cache validation before being called fully closed. | Keep affinity/genre copy. Add regression tests and cache invalidation checks so partial starts never explain as completed watches. |
| AI discovery | Spotify has moved from static recommendations into steerable recommendation controls such as DJ, AI Playlist, Smart Shuffle, hide/snooze, and autoplay controls. TeleDirect's Gemini-backed catalogue-grounded AI picks are now a real differentiator for a private library, not a side experiment. | Invest in polish: feedback, saved prompts, "why this", and better cold-start onboarding. |
| Co-viewing | Disney+ currently supports SharePlay on eligible Apple devices; Apple Music also supports SharePlay sessions. This is still platform-constrained and not universal table stakes. | Keep low unless TeleDirect grows multi-user household usage. |
| Music collaboration | Spotify Jam and Apple Music collaborative playlists make shared queue/list editing mainstream in music. | Medium only if TeleDirect becomes social; low for single-user/private use. |
| Live TV | India benchmarks are stronger than the old audit captured: JioTV/JioTV+ emphasize 7-day catch-up, TV guide, reminders, smart search, Continue Watching, multi-audio/subtitles, PiP, and parental controls. | Add EPG/reminders as the main Live TV gap; catch-up/multi-cam remain source-dependent. |
| Profiles/parental controls | Netflix/Prime/Disney profiles and maturity controls remain table stakes for household streaming. | Low for current Telegram-user identity model; becomes High if shared family accounts are a goal. |
| Offline | Netflix still invests in mobile downloads, including season download. TeleDirect has direct download links but no managed offline library. | Low unless mobile-first travel use becomes a target segment. |

### Current shipped capability validation

| Product area | Current TeleDirect status | PM assessment |
|--------------|---------------------------|---------------|
| Core OTT playback | React video player with HLS, direct/native fallback, subtitles, uploaded sidecars, user subtitle search, audio-track switching, PiP, speed, AirPlay/VLC/download/share, skip intro/recap, chapters, next episode countdown, still-watching prompt, episode navigator. | Strong. Remaining gaps are ABR architecture and subtitle appearance customization. |
| Discovery Home | Hero, shelf budget governance, personalized recommendations, personal genre shelves, trending, most-played, new episodes, music entry, filters, autocomplete, compact payloads, same-origin artwork proxy, responsive poster srcsets. | Strong. Main gaps are proactive surfacing of newly added content, richer language/provider facets, and signed-in QA for recommendation reason copy. |
| AI picks | Gemini-backed, catalogue-grounded RAG reranker; comfort/discovery buckets; chat query; refresh; per-user rate limit; cache; fallback to trending/candidates; hallucinated IDs dropped. | Differentiator. Needs user feedback loop and better empty/cold-start education. |
| Multi-device resume | Signed-in two-way CW sync, local anonymous fallback, stale write rejection, delete/completion tombstones, device labels, auth-gated server writes. | Table-stakes quality. Keep regression tests around conflict cases. |
| Music | Mini-player, Now Playing sheet, queue drawer, Play Next/Add to queue, playlist queues, liked songs, artist/album pages, synced lyrics, crossfade, gapless prebuffering, repeat/shuffle, endless related radio. | Strong private-music-library surface. Mood stations/charts/collaboration remain optional gaps. |
| Live TV / IPTV | Public channel list, channel categories/search/favorites/recents, selected/playing state, admin CRUD, M3U text/URL import, stream test, custom headers/extras, SSRF-safe imports, logo proxy/cache/placeholders. | Useful and much stronger than a basic stream list. Needs EPG/reminders before it feels like a modern TV app. |
| Admin / catalogue ops | React admin console, dashboard, metadata health score, TMDB coverage, backfill actions, codec/storage health, duplicate/poster/subtitle filters, item editor, TMDB resolve/preview/clear, AI suggest, subtitle upload/delete, series merge, trending gaps. | This is now a product pillar. Prioritize ops quality because it unlocks user-facing discovery. |
| Performance / reliability | Route-level lazy chunks, compact `/api/hub`, same-origin TMDB and Live TV logo proxying, static immutable cache tests, PWA shell/assets tests, anonymous 401 reduction. | Healthy. Continue to watch Home/Live TV network-waterfall regressions. |

### External benchmark references

- Netflix: [profiles and personalized suggestions](https://help.netflix.com/en/node/10421?ui_action=kb-article-popular-categories), [Top 10 rows](https://help.netflix.com/en/node/116472), ["Are you still watching?"](https://help.netflix.com/en/node/114059), [season download](https://about.netflix.com/en/news/introducing-the-season-download-button).
- Disney+: [SharePlay](https://help.disneyplus.com/article/disneyplus-share-play), [parental controls](https://www.disneyplus.com/explore/articles/parental-controls-guide-disney-plus).
- Prime Video: [profiles/help surface](https://www.primevideo.com/help?language=en_US), [X-Ray](https://www.aboutamazon.com/news/entertainment/what-is-x-ray-on-prime-video).
- YouTube: [chapters](https://support.google.com/youtube/answer/9884579?hl=en), [keyboard shortcuts](https://support.google.com/youtube/answer/7631406?hl=en), [Premium continue watching/download/queue controls](https://support.google.com/youtube/answer/6308116?co=GENIE.Platform%3DDesktop&hl=en).
- Spotify: [recommendation controls](https://www.spotify.com/us/safetyandprivacy/understanding-recommendations), [Smart Shuffle](https://newsroom.spotify.com/2023-03-08/smart-shuffle-new-life-spotify-playlists/), [Jam](https://newsroom.spotify.com/2023-09-26/spotify-jam-personalized-collaborative-listening-session-free-premium-users/).
- Apple Music: [collaborative playlists](https://support.apple.com/en-us/118494), [SharePlay music sessions](https://support.apple.com/en-us/108767), [Apple Music Sing](https://support.apple.com/guide/iphone/sing-along-with-apple-music-iphe16e0f316/ios).
- Jio / JioHotstar live-TV benchmarks: [JioTV features](https://www.jio.com/apps/jiotv/), [JioTV+ features](https://www.jio.com/jiohome/services/jiotvplus/), [JioHotstar streaming/search positioning](https://ads.hotstar.com/about-us/).

---

## LIVE CHROME DEVTOOLS VALIDATION — 2026-07-19

_Tool: Chrome DevTools MCP against production._

| Route | Evidence | Result |
|-------|----------|--------|
| `/app` | A11y snapshot showed hero `(500) Days of Summer`, Continue Playing, signed-out recommendation teaser, New in your library, New episodes, Trending now, Music, Series, New movies, and Worth a look shelves. Network showed `/api/hub` `200`, `/api/me` `200`, `/api/items` `200`, TMDB proxy images `200`, audio range `206`, and `directThirdPartyImages=[]`. | ✅ Home is content-rich and functional. ⚠️ Performance watch: this pass saw `/api/hub` around `3.5s` and some cold TMDB proxy image loads around `5-8s`. |
| `/app?view=music` | A11y snapshot showed the Music filter active, `20 results`, album/song cards, like actions, and mini-player. Network showed `/api/hub?view=music` `200` in about `0.75s`, no failed requests, and no direct third-party images. | ✅ Music browse is live and fast. ⚠️ Copy/routing polish: the filter header still says `942 titles`, and the `Forever` song card resolved to a movie-style path in the snapshot. |
| `/app/live-tv` | A11y snapshot showed selected channel `ADN TV+ (720p)`, `1,000 CHANNELS`, player region, favorites action, channel search, category tabs, and channel rows. Network showed `/api/live-tv/channels` `200`, logo proxy calls `200`, no failed requests, and `directThirdPartyImages=[]`. | ✅ Public Live TV is usable and logo proxying works. ⚠️ PM gaps: `/api/live-tv/channels` took about `9s` on this pass, and category tabs expose raw compound labels such as `Animation;Kids;Religious`. |
| `/app?q=Leonardo+DiCaprio` | Search route returned `2 results`: `The Departed` and `Inception`. `/api/hub?q=Leonardo+DiCaprio`, `/search/suggest`, `/api/me`, and TMDB proxy images all returned `200`; no direct third-party images. | ⚠️ Search transport works, but discovery is incomplete: no person result/card appeared for the actor query. |
| `/app/person/leonardo-dicaprio` | Direct person route loaded `h1` = `Leonardo DiCaprio`, `Actor - 2 titles`, `As Actor`, and title links to `Inception` and `The Departed`. `/api/app/person/leonardo-dicaprio` returned `200` in about `0.23s`; no direct third-party images. | ✅ TMDB person route is live after the backfill. ⚠️ Gap is search surfacing, not the person-page route itself. |
| `/app/artist/chris-brown` | A11y snapshot showed `h1` = `Chris Brown`, artist summary, `1 track`, `Play all`, `Autoplay`, `All songs`, track link `Forever`, and track actions `Play next`, `Add to queue`, `Add to playlist`. | ✅ Artist route is live and usable. Backfill/music metadata is no longer theoretical for this artist page. |
| `/api/app/artist/chris-brown` | Live fetch returned `200`, `kind: artist`, `title: Chris Brown`, `tracks.length = 1`, sample track `Forever`, same-origin poster/thumb URLs. | ✅ React route and API contract match. |
| Desktop layout `1440×900` | DevTools geometry check: header `1440×76`, primary nav `1440×53`, artist hero `1371×310`, `h1` `734×58`, mini-player `1440×72`, `hasHorizontalOverflow=false`. | ✅ No desktop overflow observed on the validated artist route. |
| Network | 15 total resources. Route document `200`, app chunks `200`, `/api/app/artist/chris-brown` `200`, `/api/me` `200`, thumbnails `200`, audio range request `206`, `directThirdPartyImages=[]`. | ✅ No failed requests and no direct third-party image leakage observed on this route. |
| Console | Across checked routes, no JavaScript errors were observed. Repeated messages were one PWA install info message and one DevTools issue: a form field lacks `id`/`name`. DOM checks point at the header search input; the range input has `aria-label`. | ⚠️ Minor accessibility/devtools polish: give header search an explicit `id`/`name`/`aria-label`; keep route-specific form fields labelled as new pages are added. |
| State carryover | A persistent mini-player was visible with prior track `Kutti Story (From "Master")` while viewing Chris Brown. | ✅ Confirms persistent audio state, but it adds screenshot noise during route validation. Use fresh/isolated context for release screenshots. |

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

## HOME SHELF GOVERNANCE — 2026-07-04

_Evidence: Home shelf assembly audit, SPA payload tests, and React shelf-order tests._

| Area | Audit finding | UX decision |
|------|---------------|-------------|
| Shelf sprawl | Home could stack base shelves, personalized shelves, Trending, Most Played, Music, and up to three genre rows, creating 10+ horizontal rails before users reached lower content. | React Home is now capped by `HUB_HOME_SHELVES` (`7` by default) and ranked by intent: recommendations, personal "Because you..." rows, fresh content, new episodes, trending, most played, and one music entry point. |
| Music discovery rows | Recently played music and top tracks are useful, but adding them as more global Home shelves would compete with existing Continue Watching, Most Played, Music, and Stats surfaces. | Keep music-specific discovery inside Music/Stats flows instead of expanding global Home shelf count. |

---

## VIDEO / OTT

### Discovery & Browse

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Personalized homepage with algorithmic rows | 🟢 All | ✅ Hero + genre shelves | — |
| Personalized reason rows | 🟢 All | ⚠️ Current code uses "Because you like..." affinity copy; user-reported live partial-play case still needs signed-in regression validation | High — partial starts must not be explained as completed watches |
| AI-guided recommendations | 🟡 Spotify/YouTube direction | ✅ Gemini-backed, catalogue-grounded AI picks with comfort/discovery buckets and chat query | Medium — add feedback, saved prompts, and better cold-start onboarding |
| Continue Watching row | 🟢 All | ✅ Cross-device CW with tombstones/stale-write protection | — |
| Search with autocomplete | 🟢 All | ✅ Live suggest, keyboard shortcuts | — |
| Genre / tag filtering | 🟢 All | ✅ Year / quality / genre dropdowns | — |
| Trending / Top content charts | 🟡 Netflix, YouTube, JioHotstar | ✅ Trending + Most Played Home shelves | — |
| "Not interested" / recommendation feedback | 🟡 Spotify, YouTube, Netflix | ✅ Dismiss recommendation + thumbs up/down signals | Medium — expose a clearer undo/history surface later |
| Language / regional browse | 🟢 India OTT | ⚠️ Tags can approximate it; no normalized language facet | Medium — only if catalogue has meaningful regional/language depth |
| New-content digest / release alerts | 🟢 All | ❌ No in-app "new since last visit" or notification loop | High — start with in-app digest before native push |
| Trailer auto-play on browse | 🟢 All | ⚠️ Trailers on movie/series page only, not on cards | Medium |
| Search by cast / crew name | 🟡 Most | ⚠️ Backfill run; direct person page works, but live search for `Leonardo DiCaprio` returned title cards only | High — add person results/direct actor cards to search and validate more cast/director names |

### Playback

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Next episode auto-play + countdown | 🟢 All | ✅ 5s countdown card | — |
| Subtitles / CC | 🟢 All | ✅ Auto-inject + manual upload | — |
| User subtitle search / attach | 🟡 Power users | ✅ Backend-proxied Wyzie search with quotas/cache | — |
| Multiple audio tracks | 🟢 All | ✅ HLS audio track switcher | — |
| Picture-in-Picture | 🟢 All | ✅ React player PiP control | — |
| Playback speed | 🟡 Netflix, YouTube, Prime | ✅ React player speed selector | — |
| Skip intro button | 🟢 All SVOD | ✅ Admin sets timestamps; button appears only within intro window | — |
| Skip recap button | 🟡 Netflix, Disney+, JioHotstar | ✅ Admin sets timestamps; button appears only within recap window | — |
| "Are you still watching?" prompt | 🟢 All | ✅ React player pauses unattended playback after 45 minutes and offers a resume action | — |
| Video chapters / timestamps | 🟡 YouTube | ✅ Admin line-entry chapters with React progress markers and chapter list | — |
| Per-title thumbs / rating | 🟡 Prime Video, YouTube | ✅ Up/down, toggle-off, auth-gated, feeds recommendations | — |
| Keyboard shortcuts | 🟢 All web apps | ✅ React player global keys | — |
| Episode picker / season navigator | 🟢 OTT series apps | ✅ Season tabs + current episode state in React watch route | — |
| External playback fallback | 🟡 Power users | ✅ Classic player, VLC, AirPlay, direct download actions | — |
| Subtitle appearance settings | 🟡 Netflix/YouTube | ❌ Uses browser/player defaults | Low — useful accessibility polish, not core blocker |
| Adaptive quality selector | 🟢 All | ⚠️ Manual variant picker (not truly adaptive) | Low — architecture limitation |
| X-Ray style cast overlay during playback | 🔵 Prime Video exclusive | ⚠️ TMDB info section below player | Low — partial coverage adequate |

### Personal Library

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Watchlist / My List | 🟢 All | ✅ MongoDB-backed, 1000-item cap | — |
| Watch history | 🟢 All | ✅ Used as recommendation signal | — |
| Cross-device sync | 🟢 All | ✅ CW, watch history, watchlist, playlists, ratings via MongoDB for signed-in users | — |
| Viewing stats / activity page | 🟡 Netflix, YouTube | ✅ `/stats` — hours watched, heatmap, streaks, top titles | — |
| Per-title ratings visible on library | 🟡 Most | ✅ Aggregate thumbs-up/down counts now appear on rated React library cards | — |

### Social / Sharing

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Share link to title | 🟢 All | ✅ Web Share API + clipboard fallback | — |
| Watch party / co-viewing | 🟡 Disney+ SharePlay, Apple SharePlay | ❌ | Low — platform/WebSocket complexity; revisit only for household/social usage |
| Shared / collaborative watchlist | 🟡 Music apps and some OTT | ❌ | Medium — more valuable as shared playlists/lists than synchronized video playback |

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

## LIVE TV / IPTV

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Channel catalogue | 🟢 Live TV apps | ✅ Public `/app/live-tv` with categories/search/favorites/recents | — |
| Channel admin CRUD | 🟢 Admin table stakes | ✅ React IPTV admin, add/edit/delete, enable/disable, ordering | — |
| M3U import | 🟡 Power users | ✅ Text and URL import with size/time limits and SSRF guardrails | — |
| Stream validation | 🟡 Power users | ✅ Admin test endpoint for stream URL + custom headers | — |
| Logo handling | 🟡 Performance/security | ✅ Same-origin proxy, image validation, TTL cache, placeholder fallback | — |
| TV guide / EPG | 🟢 JioTV/JioTV+ | ❌ No programme guide or now/next data | High for Live TV — unlocks reminders, search by show, and richer channel context |
| Programme reminders | 🟢 Live TV apps | ❌ | Medium — depends on EPG first; can start as in-app reminder, push later |
| Catch-up TV | 🟡 JioTV | ❌ | Low/Medium — source-provider dependent, not feasible from arbitrary IPTV URLs |
| Live sports moments / multi-cam | 🔵 JioHotstar/JioTV direction | ❌ | Low — not practical without rights/feed metadata |
| DVR / recording | 🟡 TV apps | ❌ | Low — storage/legal/source complexity is high |

---

## MUSIC

### Discovery & Browse

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Album / artist pages | 🟢 All | ✅ Album page with track list + art | — |
| Mood / genre radio stations | 🟢 All | ⚠️ Related track/artist radio exists; no named mood stations | Medium — add browsable station chips from genres/tags |
| Artist page (all tracks by one artist) | 🟢 All | ✅ `/artist/{slug}`; splits multi-credit correctly; primary artist linked | — |
| Recently played row (music) | 🟢 All | ⚠️ Covered by Continue Watching and Stats, not a music-specific Home rail | Medium |
| Charts (top tracks in library) | 🟡 All streaming apps | ⚠️ Stats has top artists/genres/titles; no music discovery chart surface | Medium |
| AI music discovery | 🟡 Spotify/YouTube Music | ⚠️ AI picks cover music candidates; not a music-only DJ/playlist builder | Medium — reuse AI picks for "make me a queue" |

### Playback

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Shuffle + repeat modes | 🟢 All | ✅ Per-album shuffle + repeat off/all/one | — |
| Playback speed selector | 🟡 YouTube Music (full), others podcasts only | ✅ ¾×/1×/1.5×/2× | — |
| Persistent mini-player | 🟢 All | ✅ React mini-player + Now Playing sheet | — |
| Synced lyrics | 🟢 All | ✅ LRCLIB with scroll + tap-to-seek | — |
| Crossfade between tracks | 🟢 All | ✅ 3s crossfade via dual bgAudio buffers, volume ramp | — |
| Gapless playback | 🟢 All | ✅ Dual-buffer (bgAudio + bgAudio2), pre-loads 30s before end | — |
| Endless autoplay / radio | 🟢 Spotify/YouTube Music | ✅ Queue refills near tail from related track/artist radio | — |
| Equalizer | 🟢 All | ❌ | Low — Web Audio API EQ possible but complex |
| Karaoke / vocal isolation | 🔵 Apple Music Sing only | ❌ | Low — requires server-side stem separation |

### Queue Management

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Manual queue ("Play next" / "Add to queue") | 🟢 All | ✅ "Play next" + "Add to queue" on track rows; playlist queue; toast feedback | — |
| Smart Shuffle (injects recommendations into queue) | 🟡 Spotify | ⚠️ Endless radio appends related tracks; not explicit Smart Shuffle inside user playlists | Low/Medium — useful once station quality is proven |

### Personal Library

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Liked / favourite tracks | 🟢 All | ✅ Dedicated Liked Songs page with search, sort, start/shuffle | — |
| Listening stats (Spotify Wrapped-style) | 🟡 Spotify (iconic) | ✅ `/stats` — streaks, top artists/genres, play counts, personality card | — |
| Smart playlists from library | 🟡 Spotify, Apple Music | ❌ | Low |

### Social

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Share track (Web Share) | 🟢 All | ✅ | — |
| Collaborative playlist / shared album | 🟡 Spotify Jam, Apple Music collaborative playlists | ❌ | Medium if multi-user/social; low for private single-user deployment |
| Friend activity / what's playing | 🟡 Spotify | ❌ | Low |

### Lyrics

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Synced lyrics | 🟢 All | ✅ LRCLIB | — |
| Lyrics translation | 🟡 Apple Music Sing | ❌ | Low |
| Karaoke mode | 🔵 Apple Music exclusive | ❌ | Low |

---

## DELIVERY LOG — 2026-07-19 (validated recent deliveries)

| Feature | Status | PM validation |
|---------|--------|---------------|
| **Gemini-powered AI picks** | ✅ Shipped | `/api/app/ai/recommendations` ranks only real catalogue candidates, drops hallucinated IDs, splits comfort/discovery, supports user query, refresh, cache, rate limit, and fallback. This is now a differentiator versus static recommendation rails. |
| **Multi-device Continue Watching** | ✅ Shipped | Local/server two-way sync, tombstones, stale-write rejection, signed-in focus/login merge, completion propagation, device labels, and anonymous server-write suppression. This is table-stakes quality, not just a row on Home. |
| **Recommendation trust polish** | ⚠️ Improved; verify live | Current code uses "Because you like..." semantics for personal shelves and reasons, but a user-reported live case still showed `Because you watched The Invisible Guest` after a partial play. Treat this as a signed-in/cache regression to reproduce and close. |
| **React audio now-playing revamp** | ✅ Shipped | Mini-player + Now Playing sheet use reusable controls/sliders, queue access, lyrics, shuffle/repeat, error recovery, and responsive track art. |
| **Endless related radio** | ✅ Shipped | Queue refills near the tail from track/artist-related candidates with repeat protection. This covers the first version of autoplay radio, but not named mood stations or Smart Shuffle in playlists. |
| **Dedicated Liked Songs** | ✅ Shipped | `/app/liked-songs` separates music saves from video watchlist, with search/sort/start/shuffle flows. |
| **Live TV / IPTV management** | ✅ Shipped | Public Live TV is backed by admin channel CRUD, M3U text/URL import, stream tests, custom headers/extras, logo proxy/cache, favorites/recents/search/category states. EPG is now the clear next layer. |
| **React admin dashboard** | ✅ Shipped | Metadata health, TMDB coverage, codec/storage health, duplicates/posters/thumbs/unenriched issue links, backfill actions, and cleanup actions give admins an ops cockpit. |
| **Trending gap radar** | ✅ Shipped | `/app/admin/trending-gaps` compares TMDB trending/popular candidates against the local catalogue and refreshes cache. Strong acquisition/planning tool for a private library. |
| **Subtitle operations** | ✅ Shipped | User subtitle search/attach exists through the backend provider proxy; admin sidecar upload/delete and subtitle coverage filters exist. |
| **TMDB details/credits backfill** | ✅ Run | Backfill has been executed. Chrome DevTools confirmed `/app/person/leonardo-dicaprio` works with two catalogue titles; remaining PM work is search surfacing, more known cast/director checks, detail-page credit links, and metadata health deltas. |
| **Artwork/performance hardening** | ✅ Shipped | Responsive poster srcsets, same-origin TMDB image proxy, immutable static-asset cache, compact hub payload, and Live TV logo proxy reduce third-party/network churn. |
| **Route-level SPA maturity** | ✅ Shipped | App chunks are lazy-loaded by route; important routes include Home/filters, detail, watch, watchlist, liked songs, playlists, stats, Live TV, admin, dashboard, trending gaps, and IPTV admin. |

### Remaining validation asks

| Ask | Why |
|-----|-----|
| Validate the completed TMDB metadata/credits backfill with more real catalogue names. | One person route now checks out, but actor search did not expose a person result. Validate more actor/director names, detail-page credit links, and metadata health deltas. |
| Finish auth-gated live validation: Watch, AI Picks, Liked Songs, Admin Dashboard, IPTV Admin, and signed-in recommendation rows. | The audit now has live DevTools evidence for Home, Music, Live TV, Search, Person, and Artist. Remaining risk is gated UX and signed-in personalization state. |
| Reproduce the partial-play recommendation reason case. | Confirm whether stale cache, deployed asset skew, or another route still says `Because you watched <title>` before completion. |
| Add an analytics/light telemetry checkpoint for AI picks, radio starts, Live TV usage, subtitle search, and admin backfill completion. | Next prioritization should be usage-led instead of feature-count-led. |

---

## DELIVERY LOG — 2026-05-29 (session 2 additions)

Large batch shipped since the last audit. Validated against the live deployment (catalogue ~492 items):

| Feature | Status | Validation |
|---------|--------|------------|
| **Playlists** | ✅ Shipped | `/playlists` + `/playlist/{id}` (302 auth-gated), `/api/playlists` 401 unauth. Create/rename/delete, add/remove tracks, Play all/Shuffle, per-track play-from-position, watch-page picker with inline "New playlist". 50 playlists / 500 tracks per user cap. XSS-hardened (name via `|tojson`), secure_hash re-validated on enrich. **Needs signed-in manual pass to confirm UI flows.** |
| **Stats / listening insights** | ✅ Shipped | `/stats` 302 auth-gated. Current + longest streak, video/audio hours split, 12-week day heatmap (UTC-correct), top-3 artists, genres, play counts, "personality" card (gated ≥10 plays), most-played grouped by title. |
| **Person (cast/crew) pages** | ⚠️ Built; backfill now run | Historical finding: `/person/{slug}` route existed but production had no enriched cast/crew data. Superseded by the completed backfill; now needs live spot-check on known people. |
| **Searchable cast/crew** | ⚠️ Built; backfill now run | Historical finding: search index covered `cast[]`/`director` but live names returned 0 results. Superseded by the completed backfill; now validate representative actor/director searches. |
| **Admin custom thumbnail** | ✅ Shipped | Edit-modal URL field downloads + stores to thumb cache; `__clear__` sentinel reverts to auto-detect. Modal raised to z-50 so mini-player no longer hides Save. |
| **Security & perf hardening** | ✅ Shipped & verified | Live response headers confirmed: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`, HSTS `max-age=31536000`. HMAC `compare_digest` for hash check, VLC token 64→128-bit. |
| **AI suggest (music)** | ✅ Shipped | Music-specific prompt + schema for audio items in admin enrichment. |
| **App icon + PWA orientation** | ✅ Shipped | Redesigned icon (content-hash versioned URLs, SW bumped to td-v2); portrait manifest + JS landscape lock for fullscreen video. |

**Superseded 2026-07-19:** the one-time **TMDB credits/details backfill has now been run**. The unlock should be validated through person pages, cast/crew search, and cast links on detail pages rather than treated as pending ops work.

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

### 🔴 High — Highest return / unlocks multiple surfaces

| # | Feature | Effort | Why it matters |
|---|---------|--------|----------------|
| 1 | **TMDB metadata coverage validation + search surfacing** | S/Ops | Backfill has been run and a direct person page works. Search still returned only title cards for `Leonardo DiCaprio`, so add person results/direct actor cards and validate known actor/director searches, detail-page credit links, and metadata health. |
| 2 | **Recommendation reason trust QA** | S/M | Partial progress can be a valid taste signal, but UI copy must not imply completion. Reproduce the `Because you watched The Invisible Guest` case, clear/update stale recommendation caches if needed, and add tests around partial-play wording. |
| 3 | **New-content digest** | M | Build an in-app "New since your last visit" / "Recently added for you" surface before native push. This captures the main value of notifications without VAPID/browser-permission complexity. |
| 4 | **Live TV EPG foundation** | M/L | EPG/now-next data unlocks guide, programme search, reminders, richer channel rows, and future catch-up eligibility. This is the clearest gap versus JioTV/JioTV+. |
| 5 | **AI picks feedback loop** | M | Add "more like this", "less like this", saved prompts, and clearer "why this" handling. TeleDirect now has AI discovery; the next step is trust/control. |
| 6 | **Remaining production evidence pass** | Ops | Chrome DevTools now covers Home, Music, Live TV, Search, Person, and Artist. Finish signed-in/auth-gated checks for Watch, AI Picks, Liked Songs, Admin Dashboard, IPTV Admin, and recommendation reason rows. |

### 🟡 Medium — Clear user value, moderate effort

| # | Feature | Effort | Why it matters |
|---|---------|--------|----------------|
| 6 | **Language/regional metadata facet** | M | Important if the catalogue has Hindi/regional/international depth. India OTT apps make language a primary browse axis; tags are not enough long-term. |
| 7 | **Music station chips + smart queue requests** | M | Related radio exists. Add visible mood/genre/artist station starts and eventually let AI build a queue from a prompt. |
| 8 | **In-app reminders before push notifications** | M | Use EPG/new-content digest first. Native push remains complex and permission-heavy; prove reminder value in-app. |
| 9 | **Subtitle appearance/accessibility settings** | S/M | Captions work, but user control over size/contrast/background is an accessibility gap versus mature players. |
| 10 | **Usage telemetry for prioritization** | S/M | Track feature starts/completions for AI picks, radio, Live TV, subtitle search, playlists, and admin jobs. Avoid prioritizing by feature envy. |
| 11 | **Shared playlists/lists** | M/L | Spotify Jam and Apple Music collaboration make this mainstream for music, but it only matters if TeleDirect has real multi-user usage. |

### ⚪ Low — Nice-to-have or constrained by scope/architecture

| # | Feature | Why low |
|---|---------|---------|
| Adaptive bitrate | Architecture uses Telegram as CDN; true ABR requires a media server or pre-transcoded ladder. |
| Profiles / parental controls | Low under Telegram single-user identity; becomes High only if household/shared-account use is explicit. |
| Watch party / co-viewing | SharePlay-like value is real but platform/WebSocket complexity is not justified for current private-app scale. |
| Native offline playback | Direct download exists; managed offline library adds storage, expiry, and platform complexity. |
| Catch-up TV / DVR | Depends on source rights/stream archive availability; arbitrary IPTV URLs cannot reliably support it. |
| Karaoke / vocal isolation | Requires server-side stem separation and licensing/product scope clarity. |
| Equalizer | Possible via Web Audio API, but niche compared with discovery/metadata gaps. |
| Lossless / spatial audio | Depends mostly on source files and playback devices; not a product UX unlock by itself. |

---

## TeleDirect vs Industry Summary

| Dimension | vs OTT / Live TV apps | vs Music apps |
|-----------|-----------------------|---------------|
| Core playback | ★★★★★ — HLS, captions, PiP, speed, intro/recap, chapters, next episode, still-watching; ABR remains architectural | ★★★★★ — Gapless, crossfade, queue, lyrics, mini-player, Now Playing sheet |
| Discovery | ★★★★☆ — AI picks, personal rails, trending, most played, filters; metadata coverage validation and new-content digest are next | ★★★★☆ — Artist/album pages, liked songs, related radio, AI picks; missing mood stations/charts/collab |
| Personal library | ★★★★★ — CW sync, watchlist, playlists, ratings, stats, direct downloads | ★★★★★ — Playlists, liked songs, stats, listening insights, radio queue |
| Live TV | ★★★☆☆ — Channel UX and admin IPTV are strong; EPG/reminders/catch-up are the gap | N/A |
| Admin / ops | ★★★★★ — Metadata dashboard, TMDB tools, AI suggest, subtitles, trending gaps are a real advantage for private catalogues | ★★★★☆ — Music metadata tooling exists; smart playlist ops still light |
| Notifications | ★★☆☆☆ — No digest, reminders, or push yet | ★★☆☆☆ — Same gap |
| Social / collaboration | ★★☆☆☆ — Share works; no co-viewing/shared lists | ★★☆☆☆ — Share works; no collaborative playlists/Jam |
| Mobile UX | ★★★★★ — PWA, safe-area, route chunks, responsive cards/player, Live TV polish | ★★★★★ — Mini-player, queue drawer, now-playing sheet, touch-friendly controls |
| Auth / profiles | ★★★★☆ — Telegram Login is clean for single-user identity; no household profiles | N/A |

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
| 1 | ⚠️ **Hero title hygiene is data-dependent** | React hero now uses `series_title` for series and `dir="auto"` on the heading. Validate production after the completed metadata backfill; fix catalogue data if dirty `series_title` values remain. |
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
| 22 | Header search input lacks explicit `id`/`name`/`aria-label`; Chrome DevTools flags the same form-field issue across the checked live routes |
| 23 | Music filter header says `942 titles` while showing `20 results`, which can read like an unfiltered total instead of Music-specific scope |
| 24 | Live TV category tabs expose raw compound labels such as `Animation;Kids;Religious`; normalize or group taxonomy for scanability |

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
