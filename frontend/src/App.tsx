import { useCallback, useEffect, useRef, useState } from 'react';
import { dismissRecommendation, signOut } from './api';
import { appUrl, classicPathForApp, parseRoute, uiModeHref, useAppNavigation, useHubParams } from './navigation';
import { useAudioPlayer } from './hooks/audio';
import { useAdmin, useDetail, useHub, useMe, usePlaylistDetail, usePlaylists, useStats, useWatchlist, useWatchlistItems } from './hooks/data';
import { Header, PrimaryNav, ScrollToTop, SignInModal } from './components/layout';
import { FilterBar, FilterPage } from './components/filters';
import { HeroStage, ContinueWatching, ShelfRow, GridView } from './components/hub';
import { DetailPage } from './components/detail';
import { WatchPage } from './components/watch';
import { WatchlistPage } from './components/watchlistPage';
import { AddToPlaylistSheet } from './components/addToPlaylistSheet';
import { PlaylistDetailPage, PlaylistsPage } from './components/playlistsPage';
import { StatsPage } from './components/statsPage';
import { AdminPage } from './components/adminPage';
import { AdminDashboard } from './components/adminDashboard';
import { AdminTrendingGaps } from './components/adminTrendingGaps';
import { MiniPlayer, NowPlayingSheet } from './components/audioPlayer';
import { LoadingRows, ErrorPanel } from './components/common';
import { QueueDrawer } from './components/queueDrawer';
import type { HubCard, HubFilters, RecommendationMeta, WatchTrack } from './types';

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
  const { saved, toggle, remove: removeSaved } = useWatchlist(user);
  const watchlistPage = useWatchlistItems(user, route.kind === 'watchlist');
  const playlistsPage = usePlaylists(user, route.kind === 'playlists');
  const playlistDetail = usePlaylistDetail(user, route.kind === 'playlist' ? route.playlistId : '', route.kind === 'playlist');
  const statsPage = useStats(user, route.kind === 'stats');
  const adminPage = useAdmin(user, route.kind === 'admin', location.search);
  const audio = useAudioPlayer();
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
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (event.key === '/' || ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k')) {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const requireAuth = useCallback(() => setSignInOpen(true), []);
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
    : isHubRoute
      ? (activeView === 'movies' || activeView === 'series' || activeView === 'music' ? activeView : 'home')
      : '';
  const activeFilters = Boolean(params.q || params.tag || params.quality || params.genre || params.year || params.view);
  const expectedHubMode = activeFilters ? 'grid' : 'shelves';
  const canRenderHubData = data?.mode === expectedHubMode;
  const currentHubData = data;
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

  return (
    <div className={shellClass} onClick={onLinkClick}>
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
            <ContinueWatching />
          )}

          {hubLoading && <LoadingRows />}
          {error && <ErrorPanel message={error} />}

          {!hubLoading && !error && currentHubData?.mode === 'shelves' && !activeFilters && (
            <div className="shelf-stack">
              {currentHubData.shelves.map((shelf) => (
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
      ) : route.kind === 'admin-dashboard' ? (
        <AdminDashboard user={user} onSignIn={() => setSignInOpen(true)} />
      ) : route.kind === 'admin-trending' ? (
        <AdminTrendingGaps user={user} onSignIn={() => setSignInOpen(true)} />
      ) : route.kind === 'admin' ? (
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
      ) : (
        <WatchPage
          watchKey={watchKey}
          player={audio.player}
          playTrack={audio.playTrack}
          playRelative={audio.playRelative}
          playQueueIndex={audio.playQueueIndex}
          addToQueue={audio.addToQueue}
          shuffleQueue={audio.shuffleQueue}
          togglePlayback={audio.togglePlayback}
          seek={audio.seek}
          setSpeed={audio.setSpeed}
          cycleRepeatMode={audio.cycleRepeatMode}
          setVolume={audio.setVolume}
          toggleMute={audio.toggleMute}
          confirmNext={audio.confirmNext}
          cancelNext={audio.cancelNext}
          onOpenQueue={() => setQueueOpen(true)}
          onAddToPlaylist={onAddToPlaylist}
        />
      )}

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
        setSpeed={audio.setSpeed}
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
        removeFromQueue={audio.removeFromQueue}
        clearQueue={audio.clearQueue}
        moveQueueItem={audio.moveQueueItem}
        onClose={() => setQueueOpen(false)}
      />
      <AddToPlaylistSheet
        open={Boolean(playlistTrack)}
        track={playlistTrack}
        user={user}
        onClose={() => setPlaylistTrack(null)}
        onSignIn={() => setSignInOpen(true)}
      />
      <ScrollToTop />
    </div>
  );
}


export default App;
