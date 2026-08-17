# TeleDirect — Feature Audit vs Industry
_Benchmarked against: Netflix, Disney+, Prime Video, Apple TV+, YouTube, JioHotstar, Spotify, Apple Music, YouTube Music_
_Last validated: 2026-08-01 (code, test, and current-main audit; production browser evidence below remains dated 2026-07-19)_

Legend: 🟢 Table stakes · 🟡 Differentiator · 🔵 Innovative · ✅ Have it · ⚠️ Partial · ❌ Missing

---

## CURRENT SR PM UPDATE — 2026-08-01

> **Decision rule:** this section is the current roadmap. The dated validation
> runs and delivery logs below are retained as useful historical evidence, not
> as an active list of work.

### Executive read

TeleDirect's product loop is now clearer and more defensible: **playable
library → play/resume → taste signals → grounded AI Picks → save, dismiss, or
request → return**. The recent work corrected the two issues most likely to
damage recommendation trust: old watched titles leaking into picks and fresh
episodes being counted as series replays.

The next constraint is no longer recommendation capability. It is **trust,
fulfilment, and measurement**: users need to understand the state of a pick,
know that requests close the loop, and give the team enough evidence to decide
what to improve next.

### Current feature corrections

| Area | Current status | PM assessment |
|---|---|---|
| AI Picks scope | ✅ **Movies and series only.** Music discovery lives in AI Mix. The agent uses up to five catalogue-tool calls by default (configurable, hard-capped at ten) within a bounded backend budget; it returns only playable, revalidated library titles. | This is the right product boundary. Do not mix music into the video recommendation promise. |
| AI Picks reliability | ✅ The panel now states whether picks are saved, AI-curated, library-matched, or fresh fallback titles; it shows freshness and gives a contextual retry on fallback. | This closes the previous "invisible reliability" gap. Next: validate these states in production and measure fallback rate. |
| AI recommendation feedback | ✅ Baseline feedback is live: impressions, opens, plays, saves, ratings, and dismissals feed the recommendation system with bounded retention. | The remaining control gap is direct **More like this / Less like this**, saved asks, and a user-visible explanation/history—not a feedback system from zero. |
| Watch exclusion and history | ✅ AI Picks now uses the full retained 200-title history window and revalidates watched, dismissed, hidden, deleted, and grouped content before display/cache. History is retained for 365 days; immutable completion events are separately capped. | Correct safety baseline. Add a user-facing reset/history control before collecting more personalization signals. |
| Stats replay semantics | ✅ A first-time multi-episode series watch is **Most played**, not **Most replayed**. Only plays beyond distinct episodes/files are replays. | Corrects a trust-breaking interpretation in the stats surface. |
| Beyond your library | ✅ Requestable TMDB titles now include overview, genres/runtime/rating, and a TMDB link; requests support season selection and admin state updates. | This is a real acquisition loop. The missing step is notifying requesters when a title/season becomes available. |
| CSS/build cleanup | ✅ The retired standalone server Tailwind build, `static_src/input.css`, and its Docker dependency are removed. React/Vite is the sole production CSS pipeline. | Lower operational complexity; not a standalone user-facing roadmap item. |

### Active roadmap — ordered by outcome

| Priority | Bet | Success metric | Why now |
|---|---|---|---|
| P0 | **Replace the legacy README and document secret-handling** | Current operator guide works from a clean setup and explicitly uses environment placeholders rather than deploy-link values. | Documentation and deployment clarity unblock every other investment. |
| P0 | **Product health instrumentation** | Weekly dashboard for play-start success, playback errors, AI request/fallback/open/play/save rates, search success, request creation, and request fulfilment. | The app records recommendation feedback but lacks the decision dashboard needed to prioritize objectively. |
| P1 | **Close the request loop** | % of fulfilled requests notified; time from request to available; repeat requester retention. | Requesting a title must produce a proactive outcome, not require users to revisit a status page. |
| P1 | **Tune my picks + privacy controls** | % of signed-in users setting taste controls; lower dismiss rate; reset/history actions work. | Passive history alone is slow and opaque, especially for households and changing moods. |
| P1 | **AI Picks production trust pass** | Fallback rate, stale-cache rate, watched-title leak rate, and successful retry rate stay within defined thresholds. | The UI now communicates state; validate that production behavior matches the contract. |
| P2 | **New since your last visit** | Click-through and completion rate from a concise in-app digest. | Strong retention value without premature push-notification complexity. |
| P2 | **Live TV EPG foundation** | Programme coverage, guide engagement, reminder creation. | EPG unlocks the largest remaining Live TV experience gap. |

### Deliberate non-bets

Do not add another broad media mode, social co-viewing, managed offline, or
true adaptive bitrate before the P0/P1 loop is measured and dependable. The
product already has feature breadth; it needs compounding quality.

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
| AI discovery | Spotify has moved from static recommendations into steerable recommendation controls such as DJ, AI Playlist, Smart Shuffle, hide/snooze, and autoplay controls. TeleDirect's Gemini-backed catalogue-grounded AI picks are now a real differentiator for a private library, not a side experiment. | Baseline feedback and reliability status are shipped. Invest next in direct preference controls, saved prompts, "why this", and cold-start onboarding. |
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
| AI picks | Gemini-backed, catalogue-grounded reranker for **movies and series**; comfort/discovery buckets; Ask, Refresh, rate limit, cache, safe fallback, hallucinated-ID rejection, feedback signals, and visible saved/AI/library/fallback status. | Differentiator. Next gaps are direct preference controls, saved asks, and cold-start education. |
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

## BOOKS LIBRARY UX REVIEW — 2026-08-14

_Evidence: Chrome DevTools MCP against production (`/books`, desktop + `390×844` mobile emulation, opened both seeded titles, inspected network/console) plus a source read of `booksPage.tsx`, `media_index.py`, and `spa_routes.py`._

### Current status — 2026-08-17

_Production revalidated after deployment of `a9adf67` (`Fix PDF image decoder assets`). Chrome DevTools was used against the deployed Koyeb application, including a cache-bypassed reload of the enriched **Build a Large Language Model (From Scratch)** PDF._

| Area | Status | Current evidence |
|---|---|---|
| PDF reader and embedded images | ✅ Fixed | PDF.js now receives its same-origin WebAssembly decoder directory. The affected JPEG-2000 diagram on PDF page 7 renders in production; the prior `openjpeg`/`wasmUrl` warnings are absent. |
| EPUB delivery and malformed archives | ✅ Fixed | The reader fetches EPUBs as binary data, repairs a non-compliant `mimetype` ZIP ordering when necessary, and opens the resulting buffer as binary rather than coercing it to a URL. |
| Reader experience | ✅ Shipped | Deep links, full-card open, responsive reader layout, PDF fit/100%/150% view controls, PDF/EPUB search, notes, bookmarks, reading progress, swipe/edge interactions, read-aloud voice and speed controls, and EPUB light/sepia/dark typography settings are live. |
| Library and book metadata | ✅ Shipped | Format/reading/sort filters, Continue Reading, subject browse, robust card fallbacks, Google Books enrichment, and same-origin cover proxying are live. Books remain excluded from video/music shelves. |
| Production health | ✅ Clean in this pass | The deployed reader loaded the latest app bundle, rendered PDF page 7 at `1393×1747` canvas resolution, and produced no console errors or warnings. |
| EPUB resource bounds | ✅ Added | The browser refuses EPUBs over 75 MiB, archives with more than 5,000 entries, unsafe ZIP paths, or more than 300 MiB expanded content. This protects the client-side repair/open path from obvious archive bombs. |
| Public-library scale | ✅ Added | `/api/app/books` is paginated (default 36, hard cap 60) with `total` and `nextOffset`; the library appends pages only when the reader requests more. |
| Curated discovery | ✅ Added | Admins can edit genres, collection name, and collection order per book. Public collection shelves take precedence over automatic genre/author shelves, while automatic shelves remain a fallback. |

**Current conclusion:** there is no known Books P0/P1 defect. The dated investigations below are retained as incident history and must not be read as the current feature state.

### Security and performance review — 2026-08-17

| Area | Result | Notes |
|---|---|---|
| Public access | ✅ Intentional | The library, book metadata, reader content, and download routes remain public by product decision. Protected reader-progress, notes, and admin endpoints still require authentication. |
| EPUB execution | ✅ Contained | EPUB content is rendered in a sandboxed iframe without script permission; book descriptions and metadata are rendered as React text, not injected HTML. |
| EPUB archive safety | ✅ Bounded | The client rejects oversized, high-entry-count, path-unsafe, and high-expansion EPUB archives before passing them to epub.js. These are browser-memory protections, not a substitute for server-side malware scanning. |
| External metadata/artwork | ✅ Bounded | Google Books lookup has a short server timeout; covers go through the same-origin, fixed-host proxy with a response cap. No browser API key is exposed. |
| Library payload | ✅ Bounded | Public results are paginated; a user can still deliberately load the full catalogue, but initial render/network work is limited to one page. |
| Remaining watch item | ⚪ Low | PDF.js worker/decoder assets are inherently large, but they remain route-lazy and are only fetched when a PDF is opened. |

### Remaining opportunity backlog

1. Add a PDF fixture containing JPEG-2000 artwork to automated browser coverage, preventing a missing-decoder regression.
2. Add bulk metadata/collection editing only when the catalogue size makes per-book curation onerous.
3. Consider reader highlights/annotations after real-library usage validates the need.
4. Keep server-side audiobook conversion out of scope; browser read-aloud is the free, privacy-preserving baseline for text PDFs and EPUBs.

### Historical bugs found live — superseded

| Severity | Finding | Evidence |
|---|---|---|
| 🔴 P0 | **EPUB rendering is broken in production, with no user-facing error.** Opening *The Silo Saga Omnibus* renders a fully blank page — no text, no spinner, no error message. Network trace shows epub.js requesting `book/{id}/META-INF/container.xml` → `404`. The reader hands epub.js a bare `/book/{id}/content` URL with no `.epub` extension, so its type-sniffer guesses "unpacked directory" instead of "zip archive" and never falls back; unlike the PDF error path, this failure never triggers the `role="alert"` message. | Reproduced twice (fresh load + reload) at desktop and mobile viewport. |
| 🟡 P1 | **Empty-search state is indistinguishable from empty-library state.** Searching `/books` for a non-matching term shows the same illustration/copy as a library with zero books: "No books in your library yet — check back soon for new titles." Should read "No books match '<query>'" with a clear-search action. | Reproduced on mobile viewport. |
| 🟡 P1 | **Reader footer promises sync it doesn't deliver for anonymous users.** The reader always shows "Your place, bookmarks, and notes sync to your library account," but `/api/app/books/progress` and `/api/app/books/{id}/reader-data` return `401` for signed-out sessions, so sync silently no-ops. Gate the message on auth state or prompt sign-in instead. | Confirmed via network trace: two `401`s on every book open while signed out. |
| ⚪ P2 | Book cover art occasionally paints as an empty placeholder on first render even when `coverUrl` is present (seen once on the PDF cover, desktop pass) — looks like a lazy-load/layout-shift timing issue, not a missing asset. | Single occurrence; did not reproduce on mobile pass. |

### VALIDATION — 2026-08-14 (post-fix, commits `2328da4`, `f020072`)

_Re-tested live on the same production URL after redeploy (`booksPage-Bd54asUW.js`, confirmed fresh bundle hash). All four items retested; one new defect surfaced during EPUB retest._

| Finding | Status | Evidence |
|---|---|---|
| Empty-search-state copy | ✅ Fixed | Searching `xyz-nonexistent` now shows `No books match "xyz-nonexistent"` / "Try another title, author, or filename." with a working **Clear search** button. |
| Anonymous sync message | ✅ Fixed | Reader footer now reads "Saved on this device. Sign in to sync across devices." while signed out; no longer claims sync it can't deliver. |
| Card metadata fallback | ✅ Fixed | `bookSummary()` fallback chain (`description → authors → publisher/language/pageCount/format → 'Book'`) replaces the raw-filename fallback; confirmed no filename text renders on either seeded card. |
| Cover placeholder flash (P2) | ✅ Fixed | Fallback gradient/icon now renders underneath the `<img>` at all times (`onError` hides the image instead of the fallback being conditional), so there's no blank-frame flash. |
| EPUB opens and renders | ⚠️ **Still broken — root cause changed** | The `openAs: 'epub'` fix does stop the `META-INF/container.xml` 404 misdetection, and the new `readerLoading`/12s-timeout/`role="alert"` now correctly surface a visible error ("This EPUB is taking too long to open...") instead of a silent blank page — that part works and is a real improvement. But the book **still never opens**: `book.opened` on `window.ePub(url, {openAs:'epub'})` hangs indefinitely (confirmed via direct in-page instrumentation, reproduced identically when epub.js is fed a pre-fetched `ArrayBuffer` instead of a URL, ruling out the request path). Byte-level inspection of the served file (`/book/{id}/content`, `200`, `content-type: application/epub+zip`, `6,645,048` bytes) shows a structurally valid ZIP (`PK\x03\x04` header, valid EOCD record) — but its **first zip entry is the `META-INF/` directory, not the required uncompressed `mimetype` file that the OCF/EPUB spec mandates be first**. This looks like a non-spec-compliant source EPUB (likely from the original conversion tool), and epub.js 0.3.93 appears to hang rather than reject when it can't find `mimetype` in the expected position. |

**New finding to track:** epub.js hangs (rather than fails fast) on this malformed EPUB, so the useful signal is the 12s timeout message — worth keeping, but consider: (a) verifying/repairing `mimetype`-first ordering at ingest/upload time for EPUBs so this class of file is caught before it reaches a reader, and (b) shortening the timeout or racing `book.opened` specifically (not just `rendition.display()`) so users see the error well before 12s. PDF path was regression-tested in the same pass and still opens correctly, including resuming at the last saved page.

### VALIDATION — 2026-08-14, round 2 (post-fix, commits `d5faef6`, `d9c781e`)

_Re-tested live after another redeploy (`booksPage-CqeEqscV.js`, fresh bundle hash confirmed). These commits target the exact root cause identified above: `d5faef6` adds `epubSource()`, which fetches the EPUB, uses JSZip to check whether `mimetype` is the first stored entry, and rebuilds the archive with `mimetype` first (uncompressed) if not; `d9c781e` adds a byte-level `hasStandardEpubMimetype()` fast path so compliant EPUBs skip the JSZip round-trip, plus a search debounce (250ms) and PDF render-task cancellation on rapid page changes._

| Finding | Status | Evidence |
|---|---|---|
| Repair logic itself | ✅ Correct | Manually replayed the repair algorithm in-page: `hasStandardEpubMimetype()` correctly identifies this file's first entry as `META-INF/` (not `mimetype`), so the JSZip repair path is taken as designed. |
| **EPUB still doesn't open — new regression** | 🔴 **Not fixed, different bug** | Network trace shows: `epub.min.js` loads, `book/{id}/content` `200`, `jszip.min.js` loads (repair path taken as expected) — then a request to literally `GET /[object%20ArrayBuffer]` → `404`. `epubSource()` now correctly returns a repaired `ArrayBuffer`, but the call site still passes `window.ePub(source, { openAs: 'epub' })`. `openAs: 'epub'` tells epub.js to treat `source` as a **URL string** to fetch, not as binary archive data; handed an `ArrayBuffer` instead, epub.js coerces it with implicit `String(arrayBuffer)` → the literal text `"[object ArrayBuffer]"` — confirmed by direct repro (`String(new ArrayBuffer(8))` → `"[object ArrayBuffer]"`) — then requests that string as a relative path, which 404s. The book still never renders; the UI still shows the same "This EPUB is taking too long to open" timeout message after 12s, so from a user's perspective the symptom is unchanged even though the underlying cause moved. |
| Fix direction | — | Use `{ openAs: 'binary' }` (or omit `openAs` entirely, letting epub.js auto-detect via `instanceof ArrayBuffer`) when passing the fetched/repaired buffer. Reserve `openAs: 'epub'` only for the case where `source` is still a bare URL string. |
| Search debounce | ✅ Working | Typing no longer fires a request per keystroke; requests fire ~250ms after the last change. |
| PDF regression check | ✅ No regression | `James Potter and the Hall of Elders' Crossing` still opens, renders, and shows "Opening PDF…" transiently — unaffected by the EPUB-path changes. |

**Net effect across both rounds:** the P0 EPUB-blank-page bug has been *chased correctly* twice (misdetected URL → fixed; malformed archive → fixed) but a small binding mismatch between `epubSource()`'s return type and `window.ePub()`'s `openAs` option keeps it broken. This is now a one-line fix (`openAs: 'binary'`), not a design problem — worth calling out so it doesn't get mistaken for a third distinct root cause.

### What's already good

- Server-side separation of books from movies/music is clean: `_is_book_message()` classifies by MIME/extension, `shelves()` explicitly excludes `media_kind in {"audio","book"}`, and a regression test (`test_books_are_not_returned_in_video_shelves`) guards it. Books never leak into `mediaCard` shelves.
- Gesture model (edge-tap, swipe-to-turn, drag threshold, auto-hide chrome, Esc/Arrow keys, continuous-scroll paging for PDF) is a genuine reading-first interaction pattern, not a media-player skin reused for documents.
- The reader-control contrast pass (solid orange/dark overrides instead of the app's default low-contrast ghost buttons) is the right call for controls floating over arbitrary page content.
- Whole-card click target on library entries (`role="button"`, Enter/Space handling) is correct and accessible.

### Historical gaps vs. Kindle / Apple Books / Google Play Books — largely resolved

| Area | Gap | Detail |
|---|---|---|
| Library grid | Sort/filter, fallback, Continue Reading, subjects | ✅ Resolved | Format/reading/sort controls, fallback metadata, progress surfacing, and subject browse have shipped. |
| Reader | EPUB typography, PDF zoom, book-wide search, deep links | ✅ Resolved | Settings, view controls, search, and `/books?book={id}` deep links are live. |
| Reader | Highlights and richer annotation | ⚪ Opportunity | Notes and bookmarks exist; text highlights are a future enhancement rather than a release blocker. |
| Library grid | Collections/series and density controls | ⚪ Opportunity | Worth revisiting with a larger book catalogue and real reading behaviour. |
| Reader | Auto-hide accessibility | ⚪ Monitor | Controls can be kept visible and are keyboard reachable. A dedicated assistive-technology pass remains worthwhile before calling this fully closed. |

### Historical recommended priority order — completed

The five recommendations above were delivered. Follow the current opportunity backlog instead.

---

## VIDEO / OTT

### Discovery & Browse

| Feature | Industry | TeleDirect | Priority |
|---------|----------|------------|----------|
| Personalized homepage with algorithmic rows | 🟢 All | ✅ Hero + genre shelves | — |
| Personalized reason rows | 🟢 All | ⚠️ Current code uses "Because you like..." affinity copy; user-reported live partial-play case still needs signed-in regression validation | High — partial starts must not be explained as completed watches |
| AI-guided recommendations | 🟡 Spotify/YouTube direction | ✅ Grounded movie/series AI Picks with comfort/discovery, Ask, Refresh, provenance/freshness, safe fallback, and baseline feedback | Medium — add direct preference controls, saved prompts, and better cold-start onboarding |
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
| Watch history | 🟢 All | ✅ Used as recommendation signal; 365-day retention, 200-title summary cap, bounded completion events | Medium — add clear/reset controls for the user |
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
| AI music discovery | 🟡 Spotify/YouTube Music | ✅ AI Mix builds music queues; AI Picks intentionally stays movie/series-only | Medium — add saved mixes/prompts and visible mood-station starts |

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
| **Gemini-powered AI picks** | ✅ Shipped | `/api/app/ai/recommendations` ranks only real, playable movie/series candidates, drops hallucinated IDs, splits comfort/discovery, supports Ask/Refresh/cache/rate limits, records bounded feedback, and visibly distinguishes saved AI curation from resilient library fallback. |
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
| 5 | **AI picks direct controls** | M | Baseline impression/open/play/save/dismiss feedback is shipped. Add "more like this", "less like this", saved prompts, and a user-visible explanation/history surface. |
| 6 | **Remaining production evidence pass** | Ops | Chrome DevTools now covers Home, Music, Live TV, Search, Person, and Artist. Finish signed-in/auth-gated checks for Watch, AI Picks, Liked Songs, Admin Dashboard, IPTV Admin, and recommendation reason rows. |

### 🟡 Medium — Clear user value, moderate effort

| # | Feature | Effort | Why it matters |
|---|---------|--------|----------------|
| 7 | **Language/regional metadata facet** | M | Important if the catalogue has Hindi/regional/international depth. India OTT apps make language a primary browse axis; tags are not enough long-term. |
| 8 | **Music station chips + smart queue requests** | M | Related radio exists. Add visible mood/genre/artist station starts and eventually let AI build a queue from a prompt. |
| 9 | **In-app reminders before push notifications** | M | Use EPG/new-content digest first. Native push remains complex and permission-heavy; prove reminder value in-app. |
| 10 | **Subtitle appearance/accessibility settings** | S/M | Captions work, but user control over size/contrast/background is an accessibility gap versus mature players. |
| 11 | **Usage telemetry for prioritization** | S/M | Track feature starts/completions for AI picks, radio, Live TV, subtitle search, playlists, and admin jobs. Avoid prioritizing by feature envy. |
| 12 | **Shared playlists/lists** | M/L | Spotify Jam and Apple Music collaboration make this mainstream for music, but it only matters if TeleDirect has real multi-user usage. |

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
