import { useState } from 'react';
import { HeartIcon, PlayIcon, ShuffleIcon } from '../icons';
import type { HubCard, User, WatchlistPageResponse } from '../types';
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
}: {
  user: User | null;
  data: WatchlistPageResponse | null;
  loading: boolean;
  error: string;
  onToggleSaved: (card: HubCard) => void;
  onSignIn: () => void;
}) {
  const [unlikeError, setUnlikeError] = useState('');

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
  const firstItem = data?.items[0] ? watchlistCard(data.items[0]) : null;

  const handleToggle = (card: HubCard) => {
    setUnlikeError('');
    try {
      onToggleSaved(card);
    } catch (err) {
      setUnlikeError(err instanceof Error ? err.message : 'Unable to unlike song');
    }
  };

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
          {count > 0 && (
            <div className="hero-actions">
              <a className="primary-action" href={firstItem?.href || '#'}>
                <PlayIcon />
                <span>Play</span>
              </a>
              <a className="secondary-action" href={`${firstItem?.href || '#'}?shuffle=1`}>
                <ShuffleIcon />
                <span>Shuffle</span>
              </a>
            </div>
          )}
        </div>
      </section>

      {loading && <LoadingRows variant="music-grid" />}
      {(error || unlikeError) && <ErrorPanel message={error || unlikeError} />}

      {!loading && !error && data && (
        data.items.length === 0 ? (
          <div className="empty-state">
            <HeartIcon />
            <strong>No liked songs yet</strong>
            <span>Tap the heart on any track to save it here.</span>
          </div>
        ) : (
          <div className="media-grid music-grid">
            {data.items.map((item, index) => {
              const card = watchlistCard(item);
              return (
                <MediaCard
                  key={item.item_id}
                  card={card}
                  saved
                  priority={index < 8}
                  onToggleSaved={handleToggle}
                />
              );
            })}
          </div>
        )
      )}
    </main>
  );
}
