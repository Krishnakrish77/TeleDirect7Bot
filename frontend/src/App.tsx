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
  fetchWatchlist,
  hubSearchParams,
  removeWatchlist,
  signInTelegram,
  signOut,
} from './api';
import {
  BookmarkIcon,
  CheckIcon,
  ChevronRightIcon,
  FilmIcon,
  FilterIcon,
  LogOutIcon,
  MusicIcon,
  PlayIcon,
  SearchIcon,
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

function useHubParams() {
  const [params, setParams] = useState<HubParams>(() => parseParams());

  useEffect(() => {
    const onPop = () => setParams(parseParams());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

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
      if (replace) {
        window.history.replaceState(null, '', url);
      } else {
        window.history.pushState(null, '', url);
      }
      return next;
    });
  }, []);

  return { params, update };
}

function useHub(params: HubParams) {
  const [data, setData] = useState<HubResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
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
  }, [params]);

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

function App() {
  const { params, update } = useHubParams();
  const { data, loading, error } = useHub(params);
  const { me, reload } = useMe();
  const user = me?.user ?? null;
  const { saved, toggle } = useWatchlist(user);
  const [signInOpen, setSignInOpen] = useState(false);
  const [query, setQuery] = useState(params.q);
  const searchRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => setQuery(params.q), [params.q]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (query !== params.q) update({ q: query }, true);
    }, 260);
    return () => window.clearTimeout(timer);
  }, [query, params.q, update]);

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
  const activeFilters = Boolean(
    params.q || params.tag || params.quality || params.genre || params.year || params.view || params.sort !== 'newest',
  );
  const initialLoading = loading && !data;
  const showContent = Boolean(data) && !error;

  return (
    <div className="app-shell">
      <Header
        me={me}
        user={user}
        query={query}
        setQuery={setQuery}
        searchRef={searchRef}
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

      <main className="hub-main" aria-busy={loading}>
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

        {initialLoading && <LoadingRows />}
        {error && <ErrorPanel message={error} />}

        {showContent && data?.mode === 'shelves' && (
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

        {showContent && data?.mode === 'grid' && (
          <GridView
            data={data}
            saved={saved}
            params={params}
            update={update}
            onToggleSaved={onToggleSaved}
          />
        )}
      </main>

      <SignInModal
        open={signInOpen}
        botUsername={me?.botUsername || ''}
        onClose={() => setSignInOpen(false)}
      />
    </div>
  );
}

function Header({
  me,
  user,
  query,
  setQuery,
  searchRef,
  onSignIn,
  onSignOut,
}: {
  me: MeResponse | null;
  user: User | null;
  query: string;
  setQuery: (next: string) => void;
  searchRef: RefObject<HTMLInputElement | null>;
  onSignIn: () => void;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const suggestions = useSuggestions(query);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setOpen(false);
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
            <div className="profile-chip">
              {user.photo ? (
                <img src={user.photo} alt="" />
              ) : (
                <span>{(user.name || 'U')[0].toUpperCase()}</span>
              )}
              <strong>{user.name || user.username || 'User'}</strong>
            </div>
            <button className="icon-button" type="button" onClick={onSignOut} aria-label="Sign out">
              <LogOutIcon />
            </button>
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
  const hasFilters = Boolean(query || params.tag || params.quality || params.genre || params.year || params.view || params.sort !== 'newest');
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
      <button
        className={hasFilters ? 'text-button' : 'text-button filter-reset-placeholder'}
        type="button"
        onClick={clearAll}
        disabled={!hasFilters}
        aria-hidden={!hasFilters}
        tabIndex={hasFilters ? undefined : -1}
      >
        Reset
      </button>
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
