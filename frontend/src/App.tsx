import { useCallback, useEffect, useRef, useState } from 'react';
import { signOut } from './api';
import { useAppNavigation, parseRoute, useHubParams } from './navigation';
import { useAudioPlayer } from './hooks/audio';
import { useDetail, useHub, useMe, useWatchlist } from './hooks/data';
import { Header, BottomNav, SignInModal } from './components/layout';
import { HeroStage, FilterBar, ContinueWatching, ShelfRow, GridView } from './components/hub';
import { DetailPage } from './components/detail';
import { WatchPage } from './components/watch';
import { MiniPlayer, NowPlayingSheet, QueueDrawer } from './components/audioPlayer';
import { LoadingRows, ErrorPanel } from './components/common';
import type { HubCard, ViewValue } from './types';

function App() {
  const { location, navigate, onLinkClick } = useAppNavigation();
  const route = parseRoute(location.pathname);
  const isHubRoute = route.kind === 'hub';
  const { params, update } = useHubParams(location.key, navigate);
  const { data, loading, error } = useHub(params, isHubRoute);
  const detail = useDetail(route, location.search);
  const { me, reload } = useMe();
  const user = me?.user ?? null;
  const { saved, toggle } = useWatchlist(user);
  const audio = useAudioPlayer();
  const [signInOpen, setSignInOpen] = useState(false);
  const [nowPlayingOpen, setNowPlayingOpen] = useState(false);
  const [queueOpen, setQueueOpen] = useState(false);
  const [query, setQuery] = useState(params.q);
  const searchRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => setQuery(params.q), [params.q]);

  useEffect(() => {
    if (!isHubRoute) return;
    const timer = window.setTimeout(() => {
      if (query !== params.q) update({ q: query }, true);
    }, 260);
    return () => window.clearTimeout(timer);
  }, [isHubRoute, query, params.q, update]);

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

  const activeView = params.view || '';
  const activeFilters = Boolean(params.q || params.tag || params.quality || params.genre || params.year || params.view);
  const watchKey = route.kind === 'watch' ? route.key : '';
  const onBottomSearch = useCallback(() => {
    navigate('/app');
    window.setTimeout(() => searchRef.current?.focus(), 30);
  }, [navigate]);
  const onSearchSubmit = useCallback(() => {
    update({ q: query.trim(), offset: 0 });
  }, [query, update]);

  return (
    <div className={audio.player.track ? 'app-shell has-player' : 'app-shell'} onClick={onLinkClick}>
      <Header
        me={me}
        user={user}
        query={query}
        setQuery={setQuery}
        searchRef={searchRef}
        activeView={activeView}
        onSearchSubmit={onSearchSubmit}
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

      {isHubRoute ? (
        <main className="hub-main">
          {data?.mode === 'shelves' && data.heroes.length > 0 && (
            <HeroStage heroes={data.heroes} />
          )}

          <div className="hub-toolbar">
            <div className="hub-tabs" role="tablist" aria-label="Library views">
              {(data?.filters.views || [
                { value: '', label: 'All' },
                { value: 'movies', label: 'Movies' },
                { value: 'series', label: 'Series' },
                { value: 'music', label: 'Music' },
              ]).map((view) => (
                <button
                  key={view.value || 'all'}
                  type="button"
                  role="tab"
                  aria-selected={activeView === view.value}
                  className={activeView === view.value ? 'tab active' : 'tab'}
                  onClick={() => update({ view: view.value as ViewValue })}
                >
                  {view.label}
                </button>
              ))}
            </div>

            {data && (
              <FilterBar
                data={data}
                params={params}
                query={query}
                setQuery={setQuery}
                update={update}
              />
            )}
          </div>

          {data?.mode === 'shelves' && !activeFilters && (
            <ContinueWatching />
          )}

          {loading && <LoadingRows />}
          {error && <ErrorPanel message={error} />}

          {!loading && !error && data?.mode === 'shelves' && (
            <div className="shelf-stack">
              {data.shelves.map((shelf) => (
                <ShelfRow
                  key={shelf.name}
                  shelf={shelf}
                  saved={saved}
                  onToggleSaved={onToggleSaved}
                />
              ))}
            </div>
          )}

          {!loading && !error && data?.mode === 'grid' && (
            <GridView
              data={data}
              saved={saved}
              params={params}
              update={update}
              onToggleSaved={onToggleSaved}
            />
          )}
        </main>
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
          player={audio.player}
        />
      ) : (
        <WatchPage
          watchKey={watchKey}
          player={audio.player}
          playTrack={audio.playTrack}
          playRelative={audio.playRelative}
          playQueueIndex={audio.playQueueIndex}
          addToQueue={audio.addToQueue}
          togglePlayback={audio.togglePlayback}
          seek={audio.seek}
          onOpenQueue={() => setQueueOpen(true)}
        />
      )}

      <SignInModal
        open={signInOpen}
        botUsername={me?.botUsername || ''}
        onClose={() => setSignInOpen(false)}
      />
      <audio ref={audio.audioRef} preload="metadata" />
      <MiniPlayer
        player={audio.player}
        playRelative={audio.playRelative}
        playQueueIndex={audio.playQueueIndex}
        togglePlayback={audio.togglePlayback}
        seek={audio.seek}
        onExpand={() => setNowPlayingOpen(true)}
        onOpenQueue={() => setQueueOpen(true)}
      />
      <NowPlayingSheet
        open={nowPlayingOpen}
        player={audio.player}
        playRelative={audio.playRelative}
        togglePlayback={audio.togglePlayback}
        seek={audio.seek}
        onClose={() => setNowPlayingOpen(false)}
        onOpenQueue={() => setQueueOpen(true)}
      />
      <QueueDrawer
        open={queueOpen}
        player={audio.player}
        playQueueIndex={audio.playQueueIndex}
        togglePlayback={audio.togglePlayback}
        onClose={() => setQueueOpen(false)}
      />
      <BottomNav
        user={user}
        activeView={activeView}
        onSearch={onBottomSearch}
        onAccount={() => setSignInOpen(true)}
      />
    </div>
  );
}


export default App;
