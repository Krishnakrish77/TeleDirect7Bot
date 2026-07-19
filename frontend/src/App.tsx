import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FocusEvent, MouseEvent } from 'react';
import { deleteContinueEntry, dismissRecommendation, recordWatchHistory, setServerSyncEnabled, signOut } from './api';
import { appUrl, classicPathForApp, parseRoute, uiModeHref, useAppNavigation, useHubParams } from './navigation';
import { useAudioPlayer } from './hooks/audio';
import { useArtColor } from './hooks/artColor';
import { useAdmin, useAdminIptv, useDetail, useHub, useLikedSongs, useLiveTv, useMe, usePlaylistDetail, usePlaylists, useStats, useWatchlist, useWatchlistItems } from './hooks/data';
import { Header, PrimaryNav, ScrollToTop, SignInModal } from './components/layout';
import { FilterBar, FilterPage } from './components/filters';
import { HeroStage, ContinueWatching, RecommendationTeaser, ShelfRow, GridView, budgetHomeShelves } from './components/hub';
import { MiniPlayer, NowPlayingSheet } from './components/audioPlayer';
import { LoadingRows, ErrorPanel } from './components/common';
import { QueueDrawer } from './components/queueDrawer';
import { InstallPrompt } from './components/installPrompt';
import type { HubCard, HubFilters, RecommendationMeta, WatchTrack } from './types';

const loadDetailPage = () => import('./components/detail');
const loadWatchPage = () => import('./components/watch');
const loadWatchlistPage = () => import('./components/watchlistPage');
const loadLikedSongsPage = () => import('./components/likedSongsPage');
const loadAddToPlaylistSheet = () => import('./components/addToPlaylistSheet');
const loadPlaylistsPage = () => import('./components/playlistsPage');
const loadStatsPage = () => import('./components/statsPage');
const loadAdminPage = () => import('./components/adminPage');
const loadAdminDashboard = () => import('./components/adminDashboard');
const loadAdminTrendingGaps = () => import('./components/adminTrendingGaps');
const loadAdminIptvPage = () => import('./components/adminIptvPage');
const loadLiveTvPage = () => import('./components/liveTvPage');

const DetailPage = lazy(() => loadDetailPage().then((module) => ({ default: module.DetailPage })));
const WatchPage = lazy(() => loadWatchPage().then((module) => ({ default: module.WatchPage })));
const WatchlistPage = lazy(() => loadWatchlistPage().then((module) => ({ default: module.WatchlistPage })));
const LikedSongsPage = lazy(() => loadLikedSongsPage().then((module) => ({ default: module.LikedSongsPage })));
const AddToPlaylistSheet = lazy(() => loadAddToPlaylistSheet().then((module) => ({ default: module.AddToPlaylistSheet })));
const PlaylistDetailPage = lazy(() => loadPlaylistsPage().then((module) => ({ default: module.PlaylistDetailPage })));
const PlaylistsPage = lazy(() => loadPlaylistsPage().then((module) => ({ default: module.PlaylistsPage })));
const StatsPage = lazy(() => loadStatsPage().then((module) => ({ default: module.StatsPage })));
const AdminFrame = lazy(() => loadAdminPage().then((module) => ({ default: module.AdminFrame })));
const AdminPage = lazy(() => loadAdminPage().then((module) => ({ default: module.AdminPage })));
const AdminDashboard = lazy(() => loadAdminDashboard().then((module) => ({ default: module.AdminDashboard })));
const AdminTrendingGaps = lazy(() => loadAdminTrendingGaps().then((module) => ({ default: module.AdminTrendingGaps })));
const AdminIptvPage = lazy(() => loadAdminIptvPage().then((module) => ({ default: module.AdminIptvPage })));
const LiveTvPage = lazy(() => loadLiveTvPage().then((module) => ({ default: module.LiveTvPage })));

const preloadedRouteChunks = new Set<string>();

function preloadAppRoute(pathname: string) {
  const route = parseRoute(pathname);
  const key = route.kind;
  if (preloadedRouteChunks.has(key)) return;
  preloadedRouteChunks.add(key);

  switch (route.kind) {
    case 'detail':
      void loadDetailPage();
      break;
    case 'watch':
      void loadWatchPage();
      break;
    case 'watchlist':
      void loadWatchlistPage();
      break;
    case 'liked-songs':
      void loadLikedSongsPage();
      break;
    case 'playlists':
    case 'playlist':
      void loadPlaylistsPage();
      break;
    case 'stats':
      void loadStatsPage();
      break;
    case 'live-tv':
      void loadLiveTvPage();
      break;
    case 'admin':
      void loadAdminPage();
      break;
    case 'admin-dashboard':
      void loadAdminPage();
      void loadAdminDashboard();
      break;
    case 'admin-trending':
      void loadAdminPage();
      void loadAdminTrendingGaps();
      break;
    case 'admin-iptv':
      void loadAdminPage();
      void loadAdminIptvPage();
      break;
    default:
      break;
  }
}

const DEFAULT_FILTERS: HubFilters = {
  years: [],
  qualities: [],
  genres: [],
  tags: [],
  sortOptions: [
    { value: 'newest', label: 'Newest' },
    { value: 'oldest', label: 'Oldest' },
    { value: 'title_az', label: 'Title A-Z' },
    { value: 'title_za', label: 'Title Z-A' },
    { value: 'largest', label: 'Largest' },
  ],
  views: [
    { value: '', label: 'All' },
    { value: 'movies', label: 'Movies' },
    { value: 'series', label: 'Series' },
    { value: 'music', label: 'Music' },
  ],
};

function RouteFallback() {
  return (
    <main className="hub-main route-fallback">
      <LoadingRows variant="detail" />
    </main>
  );
}

function App() {
  const { location, navigate, onLinkClick } = useAppNavigation();
  const route = parseRoute(location.pathname);
  const isHubRoute = route.kind === 'hub';
  const isFilterRoute = route.kind === 'filters';
  const { params, update } = useHubParams(location.key, navigate);
  const { data, loading, error } = useHub(params, isHubRoute || isFilterRoute);
  const detail = useDetail(route, location.search);
  const { me, reload } = useMe();
  const user = me?.user ?? null;
  // Gate all server-side progress writes (continue-watching, history) on auth
  // so anonymous playback doesn't 401 every 30s — local td:cw still persists.
  useEffect(() => {
    setServerSyncEnabled(Boolean(user));
  }, [user]);
  const { saved, toggle, remove: removeSaved } = useWatchlist(user);
  const watchlistPage = useWatchlistItems(user, route.kind === 'watchlist');
  const likedSongs = useLikedSongs(user, route.kind === 'liked-songs');
  const playlistsPage = usePlaylists(user, route.kind === 'playlists');
  const playlistDetail = usePlaylistDetail(user, route.kind === 'playlist' ? route.playlistId : '', route.kind === 'playlist');
  const liveTv = useLiveTv(route.kind === 'live-tv');
  const statsPage = useStats(user, route.kind === 'stats');
  const adminPage = useAdmin(user, route.kind === 'admin', location.search);
  const adminIptv = useAdminIptv(user, route.kind === 'admin-iptv');
  const audio = useAudioPlayer();
  const audioRef = useRef(audio);
  audioRef.current = audio;
  const artColor = useArtColor(audio.player.track?.posterUrl || audio.player.track?.thumbUrl);

  useEffect(() => {
    const root = document.documentElement;
    if (artColor) {
      root.style.setProperty('--art-r', String(artColor[0]));
      root.style.setProperty('--art-g', String(artColor[1]));
      root.style.setProperty('--art-b', String(artColor[2]));
    } else {
      root.style.removeProperty('--art-r');
      root.style.removeProperty('--art-g');
      root.style.removeProperty('--art-b');
    }
  }, [artColor]);

  const [signInOpen, setSignInOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [nowPlayingOpen, setNowPlayingOpen] = useState(false);
  const [queueOpen, setQueueOpen] = useState(false);
  const [playlistTrack, setPlaylistTrack] = useState<WatchTrack | null>(null);
  const [query, setQuery] = useState(params.q);
  const searchRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => setQuery(params.q), [params.q]);

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(target.tagName) || target.isContentEditable)) return;
      if (event.key === '/' || ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k')) {
        event.preventDefault();
        searchRef.current?.focus();
        return;
      }
      // Audio shortcuts — skipped when the video player shell is in the DOM (it owns these keys)
      const a = audioRef.current;
      if (!a.player.track || document.querySelector('.video-shell')) return;
      if (event.key === ' ' || event.key.toLowerCase() === 'k') {
        event.preventDefault();
        a.togglePlayback();
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        a.seek(Math.max(0, a.player.currentTime - 10));
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        a.seek(a.player.currentTime + 10);
      } else if (event.key.toLowerCase() === 'm') {
        event.preventDefault();
        a.toggleMute();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const requireAuth = useCallback(() => setSignInOpen(true), []);
  const onMarkWatched = useCallback((keys: string[], title: string) => {
    const uniqueKeys = [...new Set(keys.filter(Boolean))];
    uniqueKeys.forEach((key) => {
      void deleteContinueEntry(key).catch(() => undefined);
    });
    if (user) {
      void Promise.allSettled(uniqueKeys.map((key) => recordWatchHistory(key, title)));
    }
  }, [user]);
  const onToggleSaved = useCallback((card: HubCard) => {
    if (!user) {
      requireAuth();
      return;
    }
    void toggle(card.itemId);
  }, [requireAuth, toggle, user]);
  const onRemoveFromWatchlistPage = useCallback((card: HubCard) => {
    if (!user) {
      requireAuth();
      return;
    }
    watchlistPage.removeItem(card.itemId);
    void removeSaved(card.itemId);
  }, [removeSaved, requireAuth, user, watchlistPage]);
  const onDismissRecommendation = useCallback((meta: RecommendationMeta) => {
    if (!user) {
      requireAuth();
      return;
    }
    void dismissRecommendation(meta.tmdbId, meta.kind);
  }, [requireAuth, user]);
  const onAddToPlaylist = useCallback((track: WatchTrack) => {
    if (!user) {
      requireAuth();
      return;
    }
    setPlaylistTrack(track);
  }, [requireAuth, user]);

  const activeView = params.view || '';
  const activeSection = route.kind === 'watchlist'
    ? 'watchlist'
    : route.kind === 'liked-songs'
      ? 'liked-songs'
      : route.kind === 'live-tv'
        ? 'live-tv'
        : route.kind === 'playlists' || route.kind === 'playlist'
          ? 'playlists'
          : route.kind === 'stats'
            ? 'stats'
        : isHubRoute
          ? (activeView === 'movies' || activeView === 'series' || activeView === 'music' ? activeView : 'home')
          : '';
  const activeFilters = Boolean(params.q || params.tag || params.quality || params.genre || params.year || params.view);
  const expectedHubMode = activeFilters ? 'grid' : 'shelves';
  const canRenderHubData = data?.mode === expectedHubMode;
  const currentHubData = data;
  const sortedHomeShelves = useMemo(
    () => currentHubData?.mode === 'shelves' ? budgetHomeShelves(currentHubData.shelves, currentHubData.homeShelfLimit) : [],
    [currentHubData],
  );
  const hubLoading = loading && !canRenderHubData;
  const filters = data?.filters ?? DEFAULT_FILTERS;
  const watchKey = route.kind === 'watch' ? route.key : '';
  const classicUiHref = uiModeHref('classic', classicPathForApp(location.pathname, location.search));
  const shellClass = [
    'app-shell',
    audio.player.track ? 'has-player' : '',
    route.kind === 'watch' ? 'watch-route' : '',
  ].filter(Boolean).join(' ');
  const onSearchSubmit = useCallback(() => {
    const nextQuery = query.trim();
    const nextParams = { ...params, q: nextQuery, offset: 0 };
    setQuery(nextQuery);
    navigate(appUrl(nextParams));
  }, [navigate, params, query]);

  const onSearchClear = useCallback(() => {
    setQuery('');
    if (!params.q) return;
    const nextParams = { ...params, q: '', offset: 0 };
    navigate(appUrl(nextParams, isFilterRoute ? '/filters' : ''), true);
  }, [isFilterRoute, navigate, params]);

  const onRoutePreload = useCallback((event: MouseEvent<HTMLDivElement> | FocusEvent<HTMLDivElement>) => {
    const target = event.target as Element | null;
    const anchor = target?.closest<HTMLAnchorElement>('a[href]');
    if (!anchor) return;
    const url = new URL(anchor.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/app')) return;
    preloadAppRoute(url.pathname);
  }, []);

  return (
    <div className={shellClass} onClick={onLinkClick} onMouseOver={onRoutePreload} onFocusCapture={onRoutePreload}>
      <Header
        me={me}
        user={user}
        query={query}
        setQuery={setQuery}
        searchRef={searchRef}
        accountOpen={accountOpen}
        setAccountOpen={setAccountOpen}
        classicUiHref={classicUiHref}
        onSearchSubmit={onSearchSubmit}
        onSearchClear={onSearchClear}
        onSuggestionNavigate={navigate}
        onSignIn={() => setSignInOpen(true)}
        onSignOut={async () => {
          try {
            await signOut();
          } finally {
            sessionStorage.removeItem('td:auth');
            reload();
          }
        }}
      />
      <PrimaryNav
        user={user}
        activeView={activeView}
        activeSection={activeSection}
      />
      <InstallPrompt enabled={Boolean(user)} />

      <Suspense fallback={<RouteFallback />}>
        {isHubRoute ? (
          <main className="hub-main">
            {currentHubData?.mode === 'shelves' && !activeFilters && currentHubData.heroes.length > 0 && (
              <HeroStage heroes={currentHubData.heroes} />
            )}

            <div className="hub-toolbar">
              {currentHubData?.mode === 'grid' && (
                <FilterBar
                  filters={filters}
                  catalogueSize={data?.catalogueSize ?? 0}
                  params={params}
                  query={params.q}
                  setQuery={setQuery}
                  update={update}
                />
              )}
            </div>

            {currentHubData?.mode === 'shelves' && !activeFilters && (
              <ContinueWatching serverSyncEnabled={Boolean(user)} />
            )}

            {currentHubData?.mode === 'shelves' && !activeFilters && me !== null && !user && (
              <RecommendationTeaser onSignIn={requireAuth} />
            )}

            {hubLoading && <LoadingRows />}
            {error && <ErrorPanel message={error} />}

            {!hubLoading && !error && currentHubData?.mode === 'shelves' && !activeFilters && (
              <div className="shelf-stack">
                {sortedHomeShelves.map((shelf) => (
                  <ShelfRow
                    key={shelf.name}
                    shelf={shelf}
                    saved={saved}
                    onToggleSaved={onToggleSaved}
                    onDismiss={onDismissRecommendation}
                  />
                ))}
              </div>
            )}

            {!hubLoading && !error && currentHubData?.mode === 'grid' && (
              <GridView
                data={currentHubData}
                saved={saved}
                params={params}
                update={update}
                onToggleSaved={onToggleSaved}
                loading={loading}
              />
            )}
          </main>
        ) : isFilterRoute ? (
          <FilterPage
            filters={filters}
            catalogueSize={data?.catalogueSize ?? 0}
            params={params}
            query={params.q}
            setQuery={setQuery}
            navigate={navigate}
          />
        ) : route.kind === 'detail' ? (
          <DetailPage
            route={route}
            data={detail.data}
            loading={detail.loading}
            error={detail.error}
            saved={saved}
            onToggleSaved={(itemId) => {
              if (!user) {
                requireAuth();
                return;
              }
              void toggle(itemId);
            }}
            navigate={navigate}
            playTrack={audio.playTrack}
            togglePlayback={audio.togglePlayback}
            addToQueue={audio.addToQueue}
            shuffleQueue={audio.shuffleQueue}
            player={audio.player}
            onAddToPlaylist={onAddToPlaylist}
            onMarkWatched={onMarkWatched}
            canDownload={Boolean(user)}
          />
        ) : route.kind === 'watchlist' ? (
          <WatchlistPage
            user={user}
            data={watchlistPage.data}
            loading={watchlistPage.loading}
            error={watchlistPage.error}
            onToggleSaved={onRemoveFromWatchlistPage}
            onSignIn={() => setSignInOpen(true)}
          />
        ) : route.kind === 'liked-songs' ? (
          <LikedSongsPage
            user={user}
            data={likedSongs.data}
            loading={likedSongs.loading}
            error={likedSongs.error}
            onToggleSaved={(card) => {
              if (!user) { requireAuth(); return; }
              likedSongs.removeItem(card.itemId);
              void removeSaved(card.itemId);
            }}
            onSignIn={() => setSignInOpen(true)}
          />
        ) : route.kind === 'playlists' ? (
          <PlaylistsPage
            user={user}
            data={playlistsPage.data}
            loading={playlistsPage.loading}
            error={playlistsPage.error}
            navigate={navigate}
            onSignIn={() => setSignInOpen(true)}
          />
        ) : route.kind === 'playlist' ? (
          <PlaylistDetailPage
            user={user}
            data={playlistDetail.data}
            loading={playlistDetail.loading}
            error={playlistDetail.error}
            setData={playlistDetail.setData}
            navigate={navigate}
            onSignIn={() => setSignInOpen(true)}
            player={audio.player}
            playTrack={audio.playTrack}
            togglePlayback={audio.togglePlayback}
            addToQueue={audio.addToQueue}
            shuffleQueue={audio.shuffleQueue}
            onAddToPlaylist={onAddToPlaylist}
          />
        ) : route.kind === 'stats' ? (
          <StatsPage
            user={user}
            data={statsPage.data}
            loading={statsPage.loading}
            error={statsPage.error}
            onSignIn={() => setSignInOpen(true)}
          />
        ) : route.kind === 'live-tv' ? (
          <LiveTvPage
            data={liveTv.data}
            loading={liveTv.loading}
            error={liveTv.error}
          />
        ) : route.kind === 'admin-dashboard' ? (
          <AdminFrame routeKind={route.kind} locationSearch={location.search}>
            <AdminDashboard user={user} onSignIn={() => setSignInOpen(true)} />
          </AdminFrame>
        ) : route.kind === 'admin-trending' ? (
          <AdminFrame routeKind={route.kind} locationSearch={location.search}>
            <AdminTrendingGaps user={user} onSignIn={() => setSignInOpen(true)} />
          </AdminFrame>
        ) : route.kind === 'admin-iptv' ? (
          <AdminFrame routeKind={route.kind} locationSearch={location.search}>
            <AdminIptvPage
              user={user}
              data={adminIptv.data}
              loading={adminIptv.loading}
              error={adminIptv.error}
              onSignIn={() => setSignInOpen(true)}
              reload={adminIptv.reload}
              setData={adminIptv.setData}
            />
          </AdminFrame>
        ) : route.kind === 'admin' ? (
          <AdminFrame routeKind={route.kind} locationSearch={location.search}>
            <AdminPage
              user={user}
              data={adminPage.data}
              loading={adminPage.loading}
              error={adminPage.error}
              locationSearch={location.search}
              navigate={navigate}
              onSignIn={() => setSignInOpen(true)}
              reload={adminPage.reload}
              updateData={adminPage.updateData}
            />
          </AdminFrame>
        ) : (
          <WatchPage
            watchKey={watchKey}
            audio={audio}
            onOpenQueue={() => setQueueOpen(true)}
            onAddToPlaylist={onAddToPlaylist}
            savedIds={saved}
            onToggleSaved={(itemId) => {
              if (!user) { requireAuth(); return; }
              void toggle(itemId);
            }}
            serverSyncEnabled={Boolean(user)}
            canDownload={Boolean(user)}
          />
        )}
      </Suspense>

      <SignInModal
        open={signInOpen}
        botUsername={me?.botUsername || ''}
        onClose={() => setSignInOpen(false)}
      />
      <audio ref={audio.audioRef} preload="metadata" />
      <audio ref={audio.bufferRef} preload="none" />
      <MiniPlayer
        player={audio.player}
        playRelative={audio.playRelative}
        playQueueIndex={audio.playQueueIndex}
        togglePlayback={audio.togglePlayback}
        seek={audio.seek}
        onExpand={() => setNowPlayingOpen(true)}
        onOpenQueue={() => setQueueOpen(true)}
        onDismiss={audio.dismissPlayer}
      />
      <NowPlayingSheet
        open={nowPlayingOpen}
        player={audio.player}
        playRelative={audio.playRelative}
        togglePlayback={audio.togglePlayback}
        seek={audio.seek}
        cycleRepeatMode={audio.cycleRepeatMode}
        setVolume={audio.setVolume}
        toggleMute={audio.toggleMute}
        confirmNext={audio.confirmNext}
        cancelNext={audio.cancelNext}
        onClose={() => setNowPlayingOpen(false)}
        onOpenQueue={() => setQueueOpen(true)}
      />
      <QueueDrawer
        open={queueOpen}
        player={audio.player}
        playQueueIndex={audio.playQueueIndex}
        togglePlayback={audio.togglePlayback}
        moveQueueItemToNext={audio.moveQueueItemToNext}
        removeFromQueue={audio.removeFromQueue}
        clearQueue={audio.clearQueue}
        moveQueueItem={audio.moveQueueItem}
        onClose={() => setQueueOpen(false)}
      />
      {playlistTrack && (
        <Suspense fallback={null}>
          <AddToPlaylistSheet
            open={Boolean(playlistTrack)}
            track={playlistTrack}
            user={user}
            onClose={() => setPlaylistTrack(null)}
            onSignIn={() => setSignInOpen(true)}
          />
        </Suspense>
      )}
      <ScrollToTop />
    </div>
  );
}


export default App;
