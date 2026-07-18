import { useMemo, useState } from 'react';
import { BookmarkIcon, FilmIcon, PlayIcon, SearchIcon, XIcon } from '../icons';
import { localAppHref } from '../navigation';
import type { HubCard, User, WatchlistItem, WatchlistPageResponse } from '../types';
import { ErrorPanel, LoadingRows } from './common';
import { MediaCard } from './mediaCard';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';

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
    progressPct: progress,
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
  const continueItems = useMemo(() => items.filter((item) => {
    const progress = Number(item.cw_pct || 0) * 100;
    return progress >= 3 && progress < 95;
  }).slice(0, 8), [items]);

  if (!user) {
    return (
      <main className="page-main">
        <div className="empty-state">
          <BookmarkIcon />
          <strong>Sign in to view your watchlist</strong>
          <Button type="button" onClick={onSignIn}>Sign in</Button>
        </div>
      </main>
    );
  }

  return (
    <main className="page-main">
      <section className="watchlist-hero">
        <div>
          <p className="eyebrow">Saved</p>
          <h1>Watchlist</h1>
          <p>Your personal shelf for the next great watch or listen.</p>
        </div>
        <div className="watchlist-hero-stats" aria-label="Watchlist summary">
          <strong>{items.length.toLocaleString()}</strong>
          <span>saved title{items.length === 1 ? '' : 's'}</span>
          {continueItems.length > 0 && <span><PlayIcon /> {continueItems.length} ready to resume</span>}
        </div>
      </section>

      {loading && <LoadingRows variant="grid" />}
      {error && <ErrorPanel message={error} />}

      {!loading && !error && data && (
        items.length ? (
          <>
            {continueItems.length > 0 && !query && view === 'all' && (
              <section className="watchlist-continue" aria-labelledby="watchlist-continue-title">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">Pick up where you left off</p>
                    <h2 id="watchlist-continue-title">Continue watching</h2>
                  </div>
                  <span>{continueItems.length} in progress</span>
                </div>
                <div className="watchlist-continue-rail">
                  {continueItems.map((item, index) => (
                    <MediaCard key={item.item_id} card={watchlistCard(item)} saved priority={index < 4} onToggleSaved={onToggleSaved} />
                  ))}
                </div>
              </section>
            )}
            <section className="watchlist-tools" aria-label="Watchlist tools">
              <label className="watchlist-search">
                <SearchIcon />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.currentTarget.value)}
                  placeholder="Search saved titles"
                />
                {query && (
                  <Button type="button" variant="ghost" size="icon-sm" aria-label="Clear watchlist search" onClick={() => setQuery('')}>
                    <XIcon />
                  </Button>
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
                  <Button
                    key={value}
                    type="button"
                    variant={view === value ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setView(value as WatchlistView)}
                  >
                    {label}
                    <span>{counts[value as WatchlistView]}</span>
                  </Button>
                ))}
              </div>
              <label className="watchlist-sort">
                <span>Sort</span>
                <Select value={sort} onValueChange={(value) => setSort(value as WatchlistSort)}>
                  <SelectTrigger aria-label="Sort"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="saved">Saved order</SelectItem>
                    <SelectItem value="title">Title A-Z</SelectItem>
                    <SelectItem value="year">Newest year</SelectItem>
                  </SelectContent>
                </Select>
              </label>
            </section>
            {visibleItems.length ? (
              <section className="watchlist-library" aria-labelledby="watchlist-library-title">
                <div className="section-heading compact-heading">
                  <div>
                    <p className="eyebrow">Your collection</p>
                    <h2 id="watchlist-library-title">Saved titles</h2>
                  </div>
                  <span>{visibleItems.length.toLocaleString()} shown</span>
                </div>
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
              </section>
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
