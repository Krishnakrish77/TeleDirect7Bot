import { useMemo, useState } from 'react';
import { BookmarkIcon, FilmIcon, SearchIcon, XIcon } from '../icons';
import { localAppHref } from '../navigation';
import type { HubCard, User, WatchlistItem, WatchlistPageResponse } from '../types';
import { ErrorPanel, LoadingRows } from './common';
import { MediaCard } from './mediaCard';

type WatchlistView = 'all' | 'movies' | 'series' | 'music' | 'videos';
type WatchlistSort = 'saved' | 'title' | 'year';

function watchHref(url: string): string {
  if (url.startsWith('/watch/')) return url.replace(/^\/watch\//, '/app/watch/');
  return localAppHref(url) || url;
}

function itemType(kind: string): HubCard['type'] {
  if (kind === 'movie' || kind === 'series' || kind === 'album') return kind;
  if (kind === 'audio') return 'track';
  return 'item';
}

function itemView(item: WatchlistItem): WatchlistView {
  if (item.kind === 'movie') return 'movies';
  if (item.kind === 'series') return 'series';
  if (item.kind === 'album' || item.kind === 'audio') return 'music';
  return 'videos';
}

function itemKindLabel(item: WatchlistItem): string {
  if (item.kind === 'audio') return 'Music';
  if (item.kind === 'album') return 'Album';
  if (!item.kind) return 'Saved';
  return item.kind.charAt(0).toUpperCase() + item.kind.slice(1);
}

function filterWatchlistItems(
  items: WatchlistItem[],
  query: string,
  view: WatchlistView,
  sort: WatchlistSort,
): WatchlistItem[] {
  const needle = query.trim().toLowerCase();
  const filtered = items.filter((item) => {
    if (view !== 'all' && itemView(item) !== view) return false;
    if (!needle) return true;
    return `${item.title} ${item.subtitle} ${item.year || ''} ${itemKindLabel(item)}`.toLowerCase().includes(needle);
  });
  if (sort === 'title') return [...filtered].sort((a, b) => a.title.localeCompare(b.title));
  if (sort === 'year') {
    return [...filtered].sort((a, b) => (b.year || 0) - (a.year || 0) || a.title.localeCompare(b.title));
  }
  return filtered;
}

export function watchlistCard(item: WatchlistItem): HubCard {
  const href = watchHref(item.url);
  const isMusic = item.kind === 'album' || item.kind === 'audio';
  const progress = item.cw_pct ? Math.round(item.cw_pct * 100) : 0;
  return {
    type: itemType(item.kind),
    itemId: item.item_id,
    title: item.title,
    subtitle: item.subtitle || '',
    year: item.year ?? null,
    mediaKind: isMusic ? 'audio' : 'video',
    posterUrl: item.poster,
    thumbUrl: item.poster,
    backdropUrl: item.poster,
    duration: 0,
    durationLabel: '',
    fileSize: 0,
    fileSizeLabel: '',
    quality: '',
    genres: [],
    tags: [],
    overview: '',
    artist: '',
    albumTitle: '',
    trailerKey: '',
    href,
    playHref: href,
    detailsHref: href,
    streamHref: '',
    watchKey: '',
    eyebrow: item.kind === 'audio' ? 'Music' : item.kind.charAt(0).toUpperCase() + item.kind.slice(1),
    badge: progress ? `${progress}%` : '',
    aspect: isMusic ? 'square' : 'poster',
  };
}

export function WatchlistPage({
  user,
  data,
  loading,
  error,
  onToggleSaved,
  onSignIn,
}: {
  user: User | null;
  data: WatchlistPageResponse | null;
  loading: boolean;
  error: string;
  onToggleSaved: (card: HubCard) => void;
  onSignIn: () => void;
}) {
  const [query, setQuery] = useState('');
  const [view, setView] = useState<WatchlistView>('all');
  const [sort, setSort] = useState<WatchlistSort>('saved');
  const items = data?.items ?? [];
  const counts = useMemo(() => {
    const next: Record<WatchlistView, number> = { all: items.length, movies: 0, series: 0, music: 0, videos: 0 };
    for (const item of items) {
      next[itemView(item)] += 1;
    }
    return next;
  }, [items]);
  const visibleItems = useMemo(() => filterWatchlistItems(items, query, view, sort), [items, query, sort, view]);

  if (!user) {
    return (
      <main className="page-main">
        <div className="empty-state">
          <BookmarkIcon />
          <strong>Sign in to view your watchlist</strong>
          <button type="button" className="primary-action" onClick={onSignIn}>Sign in</button>
        </div>
      </main>
    );
  }

  return (
    <main className="page-main">
      <section className="page-title">
        <div>
          <p className="eyebrow">Saved</p>
          <h1>Watchlist</h1>
        </div>
        <span>{visibleItems.length.toLocaleString()} of {items.length.toLocaleString()} saved</span>
      </section>

      {loading && <LoadingRows variant="grid" />}
      {error && <ErrorPanel message={error} />}

      {!loading && !error && data && (
        items.length ? (
          <>
            <section className="watchlist-tools" aria-label="Watchlist tools">
              <label className="watchlist-search">
                <SearchIcon />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.currentTarget.value)}
                  placeholder="Search saved titles"
                />
                {query && (
                  <button type="button" className="icon-button" aria-label="Clear watchlist search" onClick={() => setQuery('')}>
                    <XIcon />
                  </button>
                )}
              </label>
              <div className="watchlist-filter-row" aria-label="Watchlist types">
                {[
                  ['all', 'All'],
                  ['movies', 'Movies'],
                  ['series', 'Series'],
                  ['music', 'Music'],
                  ['videos', 'Videos'],
                ].map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={view === value ? 'active' : ''}
                    onClick={() => setView(value as WatchlistView)}
                  >
                    {label}
                    <span>{counts[value as WatchlistView]}</span>
                  </button>
                ))}
              </div>
              <label className="watchlist-sort">
                <span>Sort</span>
                <select value={sort} onChange={(event) => setSort(event.currentTarget.value as WatchlistSort)}>
                  <option value="saved">Saved order</option>
                  <option value="title">Title A-Z</option>
                  <option value="year">Newest year</option>
                </select>
              </label>
            </section>
            {visibleItems.length ? (
              <div className="media-grid saved-grid">
                {visibleItems.map((item, index) => {
                  const card = watchlistCard(item);
                  return (
                    <MediaCard
                      key={item.item_id}
                      card={card}
                      saved
                      priority={index < 8}
                      onToggleSaved={onToggleSaved}
                    />
                  );
                })}
              </div>
            ) : (
              <div className="empty-state">
                <FilmIcon />
                <strong>No saved titles match this view</strong>
                <span>Clear the search or switch filters to see more saved items.</span>
              </div>
            )}
          </>
        ) : (
          <div className="empty-state">
            <FilmIcon />
            <strong>Your watchlist is empty</strong>
          </div>
        )
      )}
    </main>
  );
}
