import { HeartIcon, PlayIcon, ShuffleIcon } from '../icons';
import { localAppHref } from '../navigation';
import type { HubCard, User, WatchlistItem, WatchlistPageResponse, WatchTrack } from '../types';
import { ErrorPanel, LoadingRows } from './common';
import { MediaCard } from './mediaCard';
import { watchlistCard } from './watchlistPage';

export function LikedSongsPage({
  user,
  data,
  loading,
  error,
  onToggleSaved,
  onSignIn,
  playTrack,
  shuffleQueue,
}: {
  user: User | null;
  data: WatchlistPageResponse | null;
  loading: boolean;
  error: string;
  onToggleSaved: (card: HubCard) => void;
  onSignIn: () => void;
  playTrack: (track: WatchTrack, queue?: WatchTrack[]) => void;
  shuffleQueue: (queue: WatchTrack[]) => void;
}) {
  if (!user) {
    return (
      <main className="page-main">
        <div className="empty-state">
          <HeartIcon />
          <strong>Sign in to see your liked songs</strong>
          <button type="button" className="primary-action" onClick={onSignIn}>Sign in</button>
        </div>
      </main>
    );
  }

  const count = data?.items.length ?? 0;

  return (
    <main className="page-main">
      <section className="liked-songs-hero">
        <div className="liked-songs-art" aria-hidden="true">
          <HeartIcon filled />
        </div>
        <div className="liked-songs-copy">
          <p className="eyebrow">Music</p>
          <h1>Liked Songs</h1>
          <p style={{ color: 'var(--muted)', fontSize: '0.9rem' }}>
            {count} {count === 1 ? 'song' : 'songs'}
          </p>
        </div>
      </section>

      {loading && <LoadingRows variant="music-grid" />}
      {error && <ErrorPanel message={error} />}

      {!loading && !error && data && (
        data.items.length === 0 ? (
          <div className="empty-state">
            <HeartIcon />
            <strong>No liked songs yet</strong>
            <span>Tap the heart on any track to save it here.</span>
          </div>
        ) : (
          <div className={`media-grid music-grid`}>
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
        )
      )}
    </main>
  );
}
