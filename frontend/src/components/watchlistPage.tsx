import { BookmarkIcon, FilmIcon } from '../icons';
import { localAppHref } from '../navigation';
import type { HubCard, User, WatchlistItem, WatchlistPageResponse } from '../types';
import { ErrorPanel, LoadingRows } from './common';
import { MediaCard } from './mediaCard';

function watchHref(url: string): string {
  if (url.startsWith('/watch/')) return url.replace(/^\/watch\//, '/app/watch/');
  return localAppHref(url) || url;
}

function itemType(kind: string): HubCard['type'] {
  if (kind === 'movie' || kind === 'series' || kind === 'album') return kind;
  if (kind === 'audio') return 'track';
  return 'item';
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
        <span>{data?.items.length ?? 0} saved</span>
      </section>

      {loading && <LoadingRows variant="grid" />}
      {error && <ErrorPanel message={error} />}

      {!loading && !error && data && (
        data.items.length ? (
          <div className="media-grid saved-grid">
            {data.items.map((item, index) => {
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
            <strong>Your watchlist is empty</strong>
          </div>
        )
      )}
    </main>
  );
}
