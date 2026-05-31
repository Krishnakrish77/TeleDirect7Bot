import {
  FormEvent,
  KeyboardEvent,
  MouseEvent,
  RefObject,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import {
  addWatchlist,
  fetchContinueItems,
  fetchHub,
  fetchMe,
  fetchSuggestions,
  fetchWatch,
  fetchWatchlist,
  hubSearchParams,
  removeWatchlist,
  signInTelegram,
  signOut,
} from './api';
import {
  BookmarkIcon,
  CheckIcon,
  ChartIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  FilmIcon,
  FilterIcon,
  LogOutIcon,
  MusicIcon,
  PauseIcon,
  PlayIcon,
  ShieldIcon,
  SearchIcon,
  SkipBackIcon,
  SkipForwardIcon,
  UserIcon,
  XIcon,
} from './icons';
import type {
  ContinueEntry,
  ContinueItem,
  HeroItem,
  HubCard,
  HubParams,
  HubResponse,
  MeResponse,
  Suggestion,
  TelegramAuthUser,
  User,
  ViewValue,
  WatchResponse,
  WatchTrack,
} from './types';

declare global {
  interface Window {
    onTeleDirectTelegramAuth?: (user: TelegramAuthUser) => void;
  }
}

const DEFAULT_PARAMS: HubParams = {
  q: '',
  tag: '',
  quality: '',
  genre: '',
  year: null,
  sort: 'newest',
  view: '',
  offset: 0,
  limit: 24,
};

function parseParams(): HubParams {
  const qs = new URLSearchParams(window.location.search);
  const yearRaw = qs.get('year');
  const offsetRaw = qs.get('offset');
  const limitRaw = qs.get('limit');
  const view = (qs.get('view') || '') as ViewValue;
  return {
    ...DEFAULT_PARAMS,
    q: qs.get('q') || '',
    tag: qs.get('tag') || '',
    quality: qs.get('quality') || '',
    genre: qs.get('genre') || '',
    year: yearRaw ? Number(yearRaw) || null : null,
    sort: qs.get('sort') || 'newest',
    view: ['', 'list', 'movies', 'series', 'music'].includes(view) ? view : '',
    offset: offsetRaw ? Math.max(0, Number(offsetRaw) || 0) : 0,
    limit: limitRaw ? Math.max(12, Math.min(60, Number(limitRaw) || 24)) : 24,
  };
}

function appBase(): string {
  return window.location.pathname.startsWith('/static/app') ? '/static/app/' : '/app';
}

function appUrl(params: Partial<HubParams>): string {
  const qs = hubSearchParams(params);
  const base = appBase();
  return qs.toString() ? `${base}?${qs}` : base;
}

function localAppHref(href: string | null): string | null {
  if (!href) return null;
  if (href === '/app') return appBase();
  if (href.startsWith('/app?')) return `${appBase()}${href.slice('/app'.length)}`;
  return href;
}

interface AppLocation {
  pathname: string;
  search: string;
  hash: string;
  key: string;
}

function readLocation(): AppLocation {
  const { pathname, search, hash } = window.location;
  return { pathname, search, hash, key: `${pathname}${search}${hash}` };
}

function normalizeAppHref(href: string): string {
  const url = new URL(href, window.location.origin);
  return `${url.pathname}${url.search}${url.hash}`;
}

function isReactAppPath(pathname: string): boolean {
  return pathname === '/app' || pathname.startsWith('/app/');
}

function useAppNavigation() {
  const [location, setLocation] = useState<AppLocation>(() => readLocation());

  useEffect(() => {
    const onPop = () => setLocation(readLocation());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((href: string, replace = false) => {
    const next = normalizeAppHref(href);
    if (replace) {
      window.history.replaceState(null, '', next);
    } else if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.pushState(null, '', next);
    }
    setLocation(readLocation());
    if (!replace) window.scrollTo({ top: 0, behavior: 'auto' });
  }, []);

  const onLinkClick = useCallback((event: MouseEvent<HTMLDivElement>) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.altKey || event.ctrlKey || event.shiftKey) {
      return;
    }
    const target = event.target as Element | null;
    const anchor = target?.closest<HTMLAnchorElement>('a');
    if (!anchor || anchor.target || anchor.hasAttribute('download')) return;
    const url = new URL(anchor.href);
    if (url.origin !== window.location.origin || !isReactAppPath(url.pathname)) return;
    event.preventDefault();
    navigate(`${url.pathname}${url.search}${url.hash}`);
  }, [navigate]);

  return { location, navigate, onLinkClick };
}

function parseRoute(pathname: string): { kind: 'hub' } | { kind: 'watch'; key: string } {
  const watch = pathname.match(/^\/app\/watch\/([^/?#]+)/);
  if (watch) return { kind: 'watch', key: decodeURIComponent(watch[1]) };
  return { kind: 'hub' };
}

function useHubParams(locationKey: string, navigate: (href: string, replace?: boolean) => void) {
  const [params, setParams] = useState<HubParams>(() => parseParams());

  useEffect(() => {
    setParams(parseParams());
  }, [locationKey]);

  const update = useCallback((patch: Partial<HubParams>, replace = false) => {
    setParams((current) => {
      const next: HubParams = { ...current, ...patch };
      if (
        patch.q !== undefined ||
        patch.tag !== undefined ||
        patch.quality !== undefined ||
        patch.genre !== undefined ||
        patch.year !== undefined ||
        patch.sort !== undefined ||
        patch.view !== undefined
      ) {
        next.offset = 0;
      }
      const url = appUrl(next);
      navigate(url, replace);
      return next;
    });
  }, [navigate]);

  return { params, update };
}

function useHub(params: HubParams, enabled = true) {
  const [data, setData] = useState<HubResponse | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      setError('');
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchHub(params, controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load the library');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, params]);

  return { data, loading, error };
}

function useMe() {
  const [me, setMe] = useState<MeResponse | null>(null);

  const reload = useCallback(() => {
    const controller = new AbortController();
    fetchMe(controller.signal).then(setMe).catch(() => setMe(null));
    return () => controller.abort();
  }, []);

  useEffect(() => reload(), [reload]);

  return { me, reload };
}

function useWatchlist(user: User | null | undefined) {
  const [saved, setSaved] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!user) {
      setSaved(new Set());
      return;
    }
    const controller = new AbortController();
    fetchWatchlist(controller.signal)
      .then(setSaved)
      .catch(() => setSaved(new Set()));
    return () => controller.abort();
  }, [user]);

  const toggle = useCallback(async (itemId: string) => {
    const wasSaved = saved.has(itemId);
    const next = new Set(saved);
    if (wasSaved) next.delete(itemId);
    else next.add(itemId);
    setSaved(next);
    try {
      if (wasSaved) await removeWatchlist(itemId);
      else await addWatchlist(itemId);
    } catch (_) {
      setSaved(saved);
    }
  }, [saved]);

  return { saved, toggle };
}

function useSuggestions(q: string) {
  const [items, setItems] = useState<Suggestion[]>([]);

  useEffect(() => {
    if (!q.trim()) {
      setItems([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetchSuggestions(q, controller.signal).then(setItems).catch(() => setItems([]));
    }, 160);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [q]);

  return items;
}

interface PlayerState {
  track: WatchTrack | null;
  queue: WatchTrack[];
  queueIndex: number;
  playing: boolean;
  currentTime: number;
  duration: number;
  error: string;
}

function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0:00';
  const whole = Math.floor(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}

function useAudioPlayer() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [player, setPlayer] = useState<PlayerState>({
    track: null,
    queue: [],
    queueIndex: -1,
    playing: false,
    currentTime: 0,
    duration: 0,
    error: '',
  });
  const playerRef = useRef(player);

  useEffect(() => {
    playerRef.current = player;
  }, [player]);

  const startAudio = useCallback((track: WatchTrack, reset = false) => {
    const audio = audioRef.current;
    if (!audio) return;
    const nextSrc = new URL(track.streamHref, window.location.origin).href;
    if (audio.src !== nextSrc) {
      audio.src = track.streamHref;
      reset = true;
    }
    if (reset) audio.currentTime = 0;
    const promise = audio.play();
    if (promise) {
      promise.catch(() => {
        setPlayer((current) => ({
          ...current,
          playing: false,
          error: 'Tap play to start audio.',
        }));
      });
    }
  }, []);

  const playTrack = useCallback((track: WatchTrack, queue?: WatchTrack[]) => {
    const current = playerRef.current;
    const nextQueue = queue?.length ? queue : current.queue.length ? current.queue : [track];
    const found = nextQueue.findIndex((item) => item.key === track.key);
    const queueIndex = found >= 0 ? found : 0;
    const sameTrack = current.track?.key === track.key;
    setPlayer((state) => ({
      ...state,
      track,
      queue: nextQueue,
      queueIndex,
      playing: true,
      currentTime: sameTrack ? state.currentTime : 0,
      duration: sameTrack ? state.duration : track.duration || 0,
      error: '',
    }));
    startAudio(track, !sameTrack);
  }, [startAudio]);

  const playRelative = useCallback((delta: number) => {
    const current = playerRef.current;
    const nextIndex = current.queueIndex + delta;
    const nextTrack = current.queue[nextIndex];
    if (!nextTrack) {
      setPlayer((state) => ({ ...state, playing: false }));
      return;
    }
    setPlayer((state) => ({
      ...state,
      track: nextTrack,
      queueIndex: nextIndex,
      playing: true,
      currentTime: 0,
      duration: nextTrack.duration || 0,
      error: '',
    }));
    startAudio(nextTrack, true);
  }, [startAudio]);

  const togglePlayback = useCallback((track?: WatchTrack, queue?: WatchTrack[]) => {
    const current = playerRef.current;
    if (track && current.track?.key !== track.key) {
      playTrack(track, queue);
      return;
    }
    const audio = audioRef.current;
    if (!audio || !current.track) return;
    if (audio.paused) {
      setPlayer((state) => ({ ...state, playing: true, error: '' }));
      const promise = audio.play();
      if (promise) {
        promise.catch(() => {
          setPlayer((state) => ({
            ...state,
            playing: false,
            error: 'Tap play to start audio.',
          }));
        });
      }
    } else {
      audio.pause();
    }
  }, [playTrack]);

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = seconds;
    setPlayer((state) => ({ ...state, currentTime: seconds }));
  }, []);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onTime = () => {
      setPlayer((state) => ({
        ...state,
        currentTime: audio.currentTime || 0,
        duration: audio.duration || state.duration || state.track?.duration || 0,
      }));
    };
    const onPlay = () => setPlayer((state) => ({ ...state, playing: true, error: '' }));
    const onPause = () => setPlayer((state) => ({ ...state, playing: false }));
    const onEnded = () => playRelative(1);
    const onError = () => {
      setPlayer((state) => ({
        ...state,
        playing: false,
        error: 'Audio could not be loaded.',
      }));
    };

    audio.addEventListener('timeupdate', onTime);
    audio.addEventListener('loadedmetadata', onTime);
    audio.addEventListener('durationchange', onTime);
    audio.addEventListener('play', onPlay);
    audio.addEventListener('pause', onPause);
    audio.addEventListener('ended', onEnded);
    audio.addEventListener('error', onError);
    return () => {
      audio.removeEventListener('timeupdate', onTime);
      audio.removeEventListener('loadedmetadata', onTime);
      audio.removeEventListener('durationchange', onTime);
      audio.removeEventListener('play', onPlay);
      audio.removeEventListener('pause', onPause);
      audio.removeEventListener('ended', onEnded);
      audio.removeEventListener('error', onError);
    };
  }, [playRelative]);

  return { audioRef, player, playTrack, playRelative, togglePlayback, seek };
}

function App() {
  const { location, navigate, onLinkClick } = useAppNavigation();
  const route = parseRoute(location.pathname);
  const isHubRoute = route.kind === 'hub';
  const { params, update } = useHubParams(location.key, navigate);
  const { data, loading, error } = useHub(params, isHubRoute);
  const { me, reload } = useMe();
  const user = me?.user ?? null;
  const { saved, toggle } = useWatchlist(user);
  const audio = useAudioPlayer();
  const [signInOpen, setSignInOpen] = useState(false);
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
      ) : (
        <WatchPage
          watchKey={watchKey}
          player={audio.player}
          playTrack={audio.playTrack}
          playRelative={audio.playRelative}
          togglePlayback={audio.togglePlayback}
          seek={audio.seek}
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
        togglePlayback={audio.togglePlayback}
        seek={audio.seek}
      />
    </div>
  );
}

function isWatchTrack(item: WatchResponse['item']): item is WatchTrack {
  return item.type === 'track' && 'appHref' in item;
}

function WatchPage({
  watchKey,
  player,
  playTrack,
  playRelative,
  togglePlayback,
  seek,
}: {
  watchKey: string;
  player: PlayerState;
  playTrack: (track: WatchTrack, queue?: WatchTrack[]) => void;
  playRelative: (delta: number) => void;
  togglePlayback: (track?: WatchTrack, queue?: WatchTrack[]) => void;
  seek: (seconds: number) => void;
}) {
  const [data, setData] = useState<WatchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchWatch(watchKey, controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load this item');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [watchKey]);

  if (loading) {
    return (
      <main className="watch-main">
        <LoadingRows />
      </main>
    );
  }

  if (error || !data) {
    return (
      <main className="watch-main">
        <ErrorPanel message={error || 'Unable to load this item'} />
      </main>
    );
  }

  if (!isWatchTrack(data.item)) {
    const card = data.item;
    return (
      <main className="watch-main">
        <section className="watch-fallback">
          <img src={card.posterUrl} alt="" />
          <div>
            <p className="eyebrow">{card.mediaKind || 'Media'}</p>
            <h1>{card.title}</h1>
            <p>{card.overview || card.subtitle || 'Open this item in the classic player.'}</p>
            <a className="primary-action" href={data.classicHref || card.href}>
              <PlayIcon />
              <span>Open player</span>
            </a>
          </div>
        </section>
      </main>
    );
  }

  const track = data.item;
  const queue = data.albumTracks?.length ? data.albumTracks : [track];
  const current = player.track?.key === track.key;
  const playing = current && player.playing;
  const duration = current ? player.duration || track.duration || 0 : track.duration || 0;
  const currentTime = current ? player.currentTime : 0;
  const rangeMax = Math.max(1, Math.round(duration));
  const prevAvailable = current
    ? player.queueIndex > 0
    : Boolean(data.prev);
  const nextAvailable = current
    ? player.queueIndex + 1 < player.queue.length
    : Boolean(data.next);

  return (
    <main className="watch-main">
      <section className="audio-watch">
        <div className="audio-art">
          <img src={track.posterUrl || track.thumbUrl} alt="" />
        </div>
        <div className="audio-details">
          <p className="eyebrow">{track.qualityLabel || track.format || 'Music'}</p>
          <h1>{track.title}</h1>
          <p className="audio-subtitle">
            {[track.artist, track.albumTitle].filter(Boolean).join(' - ')}
          </p>
          {track.overview && <p className="audio-overview">{track.overview}</p>}

          <div className="watch-controls">
            <button
              type="button"
              className="icon-button player-nav"
              onClick={() => (current ? playRelative(-1) : data.prev && playTrack(data.prev, queue))}
              disabled={!prevAvailable}
              aria-label="Previous track"
            >
              <SkipBackIcon />
            </button>
            <button
              type="button"
              className="player-play"
              onClick={() => togglePlayback(track, queue)}
              aria-label={playing ? 'Pause' : 'Play'}
            >
              {playing ? <PauseIcon /> : <PlayIcon />}
            </button>
            <button
              type="button"
              className="icon-button player-nav"
              onClick={() => (current ? playRelative(1) : data.next && playTrack(data.next, queue))}
              disabled={!nextAvailable}
              aria-label="Next track"
            >
              <SkipForwardIcon />
            </button>
          </div>

          <div className="watch-progress">
            <span>{formatClock(currentTime)}</span>
            <input
              type="range"
              min="0"
              max={rangeMax}
              value={Math.min(rangeMax, Math.round(currentTime))}
              onChange={(event) => seek(Number(event.currentTarget.value))}
              disabled={!current}
              aria-label="Playback position"
            />
            <span>{formatClock(duration)}</span>
          </div>
          {player.error && current && <p className="player-error">{player.error}</p>}
          <a className="section-link classic-link" href={track.classicHref}>
            <span>Classic player</span>
            <ChevronRightIcon />
          </a>
        </div>
      </section>

      {queue.length > 1 && (
        <section className="track-list-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Album</p>
              <h2>{track.albumTitle || 'Tracks'}</h2>
            </div>
          </div>
          <div className="track-list">
            {queue.map((item, index) => {
              const active = player.track?.key === item.key;
              return (
                <a key={item.key} className={active ? 'track-row active' : 'track-row'} href={item.appHref}>
                  <span className="track-number">{item.trackNumber || index + 1}</span>
                  <span className="track-title">
                    <strong>{item.title}</strong>
                    <span>{[item.artist, item.qualityLabel].filter(Boolean).join(' - ')}</span>
                  </span>
                  <button
                    type="button"
                    className="icon-button"
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      togglePlayback(item, queue);
                    }}
                    aria-label={active && player.playing ? 'Pause' : 'Play'}
                  >
                    {active && player.playing ? <PauseIcon /> : <PlayIcon />}
                  </button>
                </a>
              );
            })}
          </div>
        </section>
      )}
    </main>
  );
}

function MiniPlayer({
  player,
  playRelative,
  togglePlayback,
  seek,
}: {
  player: PlayerState;
  playRelative: (delta: number) => void;
  togglePlayback: (track?: WatchTrack) => void;
  seek: (seconds: number) => void;
}) {
  const track = player.track;
  if (!track) return null;
  const duration = player.duration || track.duration || 0;
  const rangeMax = Math.max(1, Math.round(duration));
  const hasPrev = player.queueIndex > 0;
  const hasNext = player.queueIndex + 1 < player.queue.length;

  return (
    <aside className="mini-player" aria-label="Audio player">
      <a className="mini-track" href={track.appHref}>
        <img src={track.posterUrl || track.thumbUrl} alt="" />
        <span>
          <strong>{track.title}</strong>
          <span>{[track.artist, track.albumTitle].filter(Boolean).join(' - ')}</span>
        </span>
      </a>
      <div className="mini-controls">
        <button type="button" className="icon-button" onClick={() => playRelative(-1)} disabled={!hasPrev} aria-label="Previous track">
          <SkipBackIcon />
        </button>
        <button type="button" className="player-play mini-play" onClick={() => togglePlayback()} aria-label={player.playing ? 'Pause' : 'Play'}>
          {player.playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <button type="button" className="icon-button" onClick={() => playRelative(1)} disabled={!hasNext} aria-label="Next track">
          <SkipForwardIcon />
        </button>
      </div>
      <div className="mini-progress">
        <span>{formatClock(player.currentTime)}</span>
        <input
          type="range"
          min="0"
          max={rangeMax}
          value={Math.min(rangeMax, Math.round(player.currentTime))}
          onChange={(event) => seek(Number(event.currentTarget.value))}
          aria-label="Playback position"
        />
        <span>{formatClock(duration)}</span>
      </div>
      {player.error && <p className="player-error mini-error">{player.error}</p>}
    </aside>
  );
}

function Header({
  me,
  user,
  query,
  setQuery,
  searchRef,
  onSearchSubmit,
  onSignIn,
  onSignOut,
}: {
  me: MeResponse | null;
  user: User | null;
  query: string;
  setQuery: (next: string) => void;
  searchRef: RefObject<HTMLInputElement | null>;
  onSearchSubmit: () => void;
  onSignIn: () => void;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const accountRef = useRef<HTMLDivElement | null>(null);
  const suggestions = useSuggestions(query);

  useEffect(() => {
    if (!accountOpen) return;
    const closeOnPointer = (event: PointerEvent) => {
      if (!accountRef.current?.contains(event.target as Node)) {
        setAccountOpen(false);
      }
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setAccountOpen(false);
    };
    document.addEventListener('pointerdown', closeOnPointer);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnPointer);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [accountOpen]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setOpen(false);
    onSearchSubmit();
  };

  const handleKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') setOpen(false);
  };

  return (
    <header className="app-header">
      <a className="brand" href="/app" aria-label="TeleDirect">
        <span className="brand-mark">
          <PlayIcon />
        </span>
        <span>TeleDirect</span>
      </a>

      <form className="top-search" role="search" onSubmit={handleSubmit}>
        <SearchIcon className="search-leading" />
        <input
          ref={searchRef}
          value={query}
          onChange={(event) => {
            setQuery(event.currentTarget.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKey}
          placeholder="Search library"
          autoComplete="off"
        />
        {query && (
          <button type="button" className="icon-button clear-search" onClick={() => setQuery('')} aria-label="Clear search">
            <XIcon />
          </button>
        )}
        {open && suggestions.length > 0 && (
          <SearchMenu suggestions={suggestions} onPick={() => setOpen(false)} />
        )}
      </form>

      <div className="header-actions">
        {user ? (
          <>
            <a className="icon-button" href="/watchlist" aria-label="Watchlist">
              <BookmarkIcon />
            </a>
            <div className="account-menu-wrap" ref={accountRef}>
              <button
                className="profile-chip"
                type="button"
                onClick={() => setAccountOpen((current) => !current)}
                aria-haspopup="menu"
                aria-expanded={accountOpen}
              >
                <span className="profile-avatar">
                  {user.photo ? (
                    <img src={user.photo} alt="" />
                  ) : (
                    <span>{(user.name || 'U')[0].toUpperCase()}</span>
                  )}
                </span>
                <strong>{user.name || user.username || 'User'}</strong>
                <ChevronDownIcon className="profile-chevron" />
              </button>
              {accountOpen && (
                <div className="account-menu" role="menu">
                  <a href="/watchlist" role="menuitem" onClick={() => setAccountOpen(false)}>
                    <BookmarkIcon />
                    <span>Watchlist</span>
                  </a>
                  <a href="/stats" role="menuitem" onClick={() => setAccountOpen(false)}>
                    <ChartIcon />
                    <span>Stats</span>
                  </a>
                  {user.is_admin && (
                    <a href="/admin" role="menuitem" onClick={() => setAccountOpen(false)}>
                      <ShieldIcon />
                      <span>Admin panel</span>
                    </a>
                  )}
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setAccountOpen(false);
                      onSignOut();
                    }}
                  >
                    <LogOutIcon />
                    <span>Sign out</span>
                  </button>
                </div>
              )}
            </div>
          </>
        ) : (
          <button className="signin-button" type="button" onClick={onSignIn} disabled={me === null}>
            <UserIcon />
            <span>Sign in</span>
          </button>
        )}
      </div>
    </header>
  );
}

function SearchMenu({ suggestions, onPick }: { suggestions: Suggestion[]; onPick: () => void }) {
  return (
    <div className="search-menu">
      {suggestions.map((item) => (
        <a key={item.url} href={item.url} className="suggestion" onClick={onPick}>
          <span className="suggestion-art">
            {item.poster_path ? (
              <img src={`https://image.tmdb.org/t/p/w92${item.poster_path}`} alt="" loading="lazy" />
            ) : (
              <img src={`/thumb/${item.secure_hash}${item.message_id}.jpg`} alt="" loading="lazy" />
            )}
          </span>
          <span className="suggestion-copy">
            <strong>{item.title}</strong>
            <span>{[item.year, item.kind].filter(Boolean).join(' - ')}</span>
          </span>
        </a>
      ))}
    </div>
  );
}

function HeroStage({ heroes }: { heroes: HeroItem[] }) {
  const [active, setActive] = useState(0);
  const hero = heroes[active] || heroes[0];

  useEffect(() => {
    if (heroes.length < 2) return;
    const timer = window.setInterval(() => {
      setActive((current) => (current + 1) % heroes.length);
    }, 7000);
    return () => window.clearInterval(timer);
  }, [heroes.length]);

  if (!hero) return null;

  const bg = hero.backdropUrl || hero.posterUrl;

  return (
    <section className="hero-stage" aria-label={hero.title}>
      <img className="hero-bg" src={bg} alt="" />
      <div className="hero-vignette" />
      <div className="hero-content">
        <p className="eyebrow">{hero.eyebrow}</p>
        <h1>{hero.title}</h1>
        {hero.overview && <p className="hero-overview">{hero.overview}</p>}
        <div className="hero-meta">
          {hero.meta.map((part) => <span key={part}>{part}</span>)}
        </div>
        <div className="hero-actions">
          <a className="primary-action" href={hero.playHref}>
            <PlayIcon />
            <span>Play</span>
          </a>
          <a className="secondary-action" href={hero.detailsHref}>
            <span>Details</span>
            <ChevronRightIcon />
          </a>
        </div>
      </div>
      {heroes.length > 1 && (
        <div className="hero-strip" aria-label="Featured titles">
          {heroes.map((item, index) => (
            <button
              key={item.itemId}
              type="button"
              className={index === active ? 'active' : ''}
              onClick={() => setActive(index)}
              aria-label={item.title}
            >
              <img src={item.posterUrl} alt="" loading="lazy" />
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function FilterBar({
  data,
  params,
  query,
  setQuery,
  update,
}: {
  data: HubResponse;
  params: HubParams;
  query: string;
  setQuery: (next: string) => void;
  update: (patch: Partial<HubParams>, replace?: boolean) => void;
}) {
  const clearAll = () => {
    setQuery('');
    update({
      q: '',
      tag: '',
      quality: '',
      genre: '',
      year: null,
      sort: 'newest',
      view: '',
      offset: 0,
    });
  };

  return (
    <div className="filter-bar">
      <div className="filter-heading">
        <FilterIcon />
        <span>{data.catalogueSize.toLocaleString()} titles</span>
      </div>
      <label>
        <span>Year</span>
        <select value={params.year || ''} onChange={(event) => update({ year: event.currentTarget.value ? Number(event.currentTarget.value) : null })}>
          <option value="">Any</option>
          {data.filters.years.map((year) => <option key={year} value={year}>{year}</option>)}
        </select>
      </label>
      <label>
        <span>Quality</span>
        <select value={params.quality} onChange={(event) => update({ quality: event.currentTarget.value })}>
          <option value="">Any</option>
          {data.filters.qualities.map((quality) => <option key={quality} value={quality}>{quality}</option>)}
        </select>
      </label>
      <label>
        <span>Genre</span>
        <select value={params.genre} onChange={(event) => update({ genre: event.currentTarget.value })}>
          <option value="">Any</option>
          {data.filters.genres.map((genre) => <option key={genre} value={genre}>{genre}</option>)}
        </select>
      </label>
      <label>
        <span>Sort</span>
        <select value={params.sort} onChange={(event) => update({ sort: event.currentTarget.value })}>
          {data.filters.sortOptions.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </label>
      {(query || params.tag || params.quality || params.genre || params.year || params.view || params.sort !== 'newest') && (
        <button className="text-button" type="button" onClick={clearAll}>Reset</button>
      )}
    </div>
  );
}

function ContinueWatching() {
  const [entries, setEntries] = useState<Array<ContinueEntry & ContinueItem>>([]);

  const load = useCallback(() => {
    let raw: Record<string, Omit<ContinueEntry, 'key'>> = {};
    try {
      raw = JSON.parse(localStorage.getItem('td:cw') || '{}') || {};
    } catch (_) {
      raw = {};
    }
    const local = Object.entries(raw)
      .map(([key, value]) => ({ key, ...value }))
      .filter((entry) => entry.dur > 0 && entry.pos / entry.dur > 0.02 && entry.pos / entry.dur < 0.95)
      .sort((a, b) => (b.t || 0) - (a.t || 0))
      .slice(0, 10);

    if (!local.length) {
      setEntries([]);
      return;
    }

    const controller = new AbortController();
    fetchContinueItems(local.map((entry) => entry.key), controller.signal)
      .then((items) => {
        const byKey = new Map(items.map((item) => [item.key, item]));
        setEntries(local.flatMap((entry) => {
          const item = byKey.get(entry.key);
          return item ? [{ ...entry, ...item }] : [];
        }));
      })
      .catch(() => setEntries([]));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const cleanup = load();
    const onStorage = () => load();
    window.addEventListener('storage', onStorage);
    return () => {
      if (cleanup) cleanup();
      window.removeEventListener('storage', onStorage);
    };
  }, [load]);

  if (!entries.length) return null;

  const forget = (key: string) => {
    try {
      const data = JSON.parse(localStorage.getItem('td:cw') || '{}') || {};
      delete data[key];
      localStorage.setItem('td:cw', JSON.stringify(data));
    } catch (_) {
      // local-only convenience state; ignore storage failures.
    }
    setEntries((current) => current.filter((entry) => entry.key !== key));
  };

  return (
    <section className="continue-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Resume</p>
          <h2>Continue playing</h2>
        </div>
      </div>
      <div className="continue-row">
        {entries.map((entry) => {
          const percent = Math.max(4, Math.min(94, Math.round((entry.pos / entry.dur) * 100)));
          const title = entry.series_title || entry.title;
          return (
            <a key={entry.key} href={entry.watch_url} className="continue-card">
              <img src={entry.poster_path ? `https://image.tmdb.org/t/p/w342${entry.poster_path}` : entry.thumb_url} alt="" loading="lazy" />
              <button
                type="button"
                className="forget-button"
                onClick={(event) => {
                  event.preventDefault();
                  forget(entry.key);
                }}
                aria-label="Remove"
              >
                <XIcon />
              </button>
              <span className="progress-track"><span style={{ width: `${percent}%` }} /></span>
              <strong>{title}</strong>
              <span>{entry.episode_label || entry.title}</span>
            </a>
          );
        })}
      </div>
    </section>
  );
}

function ShelfRow({
  shelf,
  saved,
  onToggleSaved,
}: {
  shelf: { name: string; href: string | null; items: HubCard[] };
  saved: Set<string>;
  onToggleSaved: (card: HubCard) => void;
}) {
  if (!shelf.items.length) return null;
  return (
    <section className="shelf-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Library</p>
          <h2>{shelf.name}</h2>
        </div>
        {shelf.href && (
          <a className="section-link" href={localAppHref(shelf.href) || shelf.href}>
            <span>See all</span>
            <ChevronRightIcon />
          </a>
        )}
      </div>
      <div className="card-row">
        {shelf.items.map((card) => (
          <MediaCard
            key={`${card.type}:${card.itemId}`}
            card={card}
            saved={saved.has(card.itemId)}
            onToggleSaved={onToggleSaved}
          />
        ))}
      </div>
    </section>
  );
}

function GridView({
  data,
  params,
  saved,
  update,
  onToggleSaved,
}: {
  data: HubResponse;
  params: HubParams;
  saved: Set<string>;
  update: (patch: Partial<HubParams>, replace?: boolean) => void;
  onToggleSaved: (card: HubCard) => void;
}) {
  return (
    <section className="grid-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{data.total.toLocaleString()} results</p>
          <h2>{params.q ? `Search: ${params.q}` : 'Browse'}</h2>
        </div>
      </div>
      {data.items.length ? (
        <>
          <div className="media-grid">
            {data.items.map((card) => (
              <MediaCard
                key={`${card.type}:${card.itemId}`}
                card={card}
                saved={saved.has(card.itemId)}
                onToggleSaved={onToggleSaved}
              />
            ))}
          </div>
          {data.nextOffset !== null && (
            <div className="load-more-wrap">
              <button
                type="button"
                className="secondary-action"
                onClick={() => update({ offset: data.nextOffset || 0 })}
              >
                <span>More</span>
                <ChevronRightIcon />
              </button>
            </div>
          )}
        </>
      ) : (
        <div className="empty-state">
          <FilmIcon />
          <strong>{data.emptyText}</strong>
        </div>
      )}
    </section>
  );
}

function MediaCard({
  card,
  saved,
  onToggleSaved,
}: {
  card: HubCard;
  saved: boolean;
  onToggleSaved: (card: HubCard) => void;
}) {
  const isMusic = card.type === 'track' || card.type === 'album';
  return (
    <a className={`media-card ${card.aspect === 'square' ? 'square' : 'poster'}`} href={card.href}>
      <span className="poster-wrap">
        <span className="poster-placeholder">
          {isMusic ? <MusicIcon /> : <FilmIcon />}
        </span>
        <img
          src={card.posterUrl}
          alt=""
          loading="lazy"
          onError={(event) => {
            event.currentTarget.style.display = 'none';
          }}
        />
        {card.badge && <span className="card-badge">{card.badge}</span>}
        <button
          type="button"
          className={saved ? 'save-button saved' : 'save-button'}
          onClick={(event: MouseEvent<HTMLButtonElement>) => {
            event.preventDefault();
            event.stopPropagation();
            onToggleSaved(card);
          }}
          aria-label={saved ? 'Remove from watchlist' : 'Add to watchlist'}
        >
          {saved ? <CheckIcon /> : <BookmarkIcon />}
        </button>
      </span>
      <span className="card-copy">
        <span className="eyebrow">{card.eyebrow}</span>
        <strong>{card.title}{card.year ? ` (${card.year})` : ''}</strong>
        {card.subtitle && <span>{card.subtitle}</span>}
      </span>
    </a>
  );
}

function LoadingRows() {
  return (
    <div className="loading-stack" aria-label="Loading">
      <span />
      <span />
      <span />
    </div>
  );
}

function ErrorPanel({ message }: { message: string }) {
  return (
    <div className="empty-state error-state">
      <FilmIcon />
      <strong>{message}</strong>
    </div>
  );
}

function SignInModal({
  open,
  botUsername,
  onClose,
}: {
  open: boolean;
  botUsername: string;
  onClose: () => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open || !botUsername || !rootRef.current) return;
    rootRef.current.innerHTML = '';
    setError('');

    window.onTeleDirectTelegramAuth = async (telegramUser: TelegramAuthUser) => {
      try {
        const data = await signInTelegram(telegramUser);
        if (data.token) sessionStorage.setItem('td:auth', data.token);
        window.location.reload();
      } catch (_) {
        setError('Sign in failed');
      }
    };

    const script = document.createElement('script');
    script.async = true;
    script.src = 'https://telegram.org/js/telegram-widget.js?22';
    script.setAttribute('data-telegram-login', botUsername.replace(/^@/, ''));
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-radius', '8');
    script.setAttribute('data-onauth', 'onTeleDirectTelegramAuth(user)');
    script.setAttribute('data-request-access', 'write');
    rootRef.current.appendChild(script);

    return () => {
      delete window.onTeleDirectTelegramAuth;
      if (rootRef.current) rootRef.current.innerHTML = '';
    };
  }, [botUsername, open]);

  if (!open) return null;

  return (
    <div className="modal-layer" role="dialog" aria-modal="true" aria-label="Sign in">
      <button className="modal-scrim" type="button" onClick={onClose} aria-label="Close" />
      <div className="modal-panel">
        <button className="icon-button modal-close" type="button" onClick={onClose} aria-label="Close">
          <XIcon />
        </button>
        <h2>Sign in</h2>
        <div className="telegram-slot" ref={rootRef} />
        {!botUsername && <p className="form-error">Telegram login unavailable</p>}
        {error && <p className="form-error">{error}</p>}
      </div>
    </div>
  );
}

export default App;
