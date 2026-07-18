import { useMemo, useState } from 'react';
import { HeartIcon, PlayIcon, SearchIcon, ShuffleIcon, XIcon } from '../icons';
import type { HubCard, User, WatchlistPageResponse } from '../types';
import { ErrorPanel, LoadingRows } from './common';
import { MediaCard } from './mediaCard';
import { watchlistCard } from './watchlistPage';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';

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
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<'saved' | 'title' | 'artist'>('saved');
  const visibleItems = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = (data?.items ?? []).filter((item) => !needle || `${item.title} ${item.subtitle} ${item.year || ''}`.toLowerCase().includes(needle));
    if (sort === 'title') return [...filtered].sort((a, b) => a.title.localeCompare(b.title));
    if (sort === 'artist') return [...filtered].sort((a, b) => a.subtitle.localeCompare(b.subtitle) || a.title.localeCompare(b.title));
    return filtered;
  }, [data?.items, query, sort]);

  if (!user) {
    return (
      <main className="page-main">
        <div className="empty-state">
          <HeartIcon />
          <strong>Sign in to see your liked songs</strong>
          <Button type="button" onClick={onSignIn}>Sign in</Button>
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
          <p>Your personal rotation, ready whenever you need it.</p>
          <div className="liked-songs-stats"><strong>{count}</strong><span>{count === 1 ? 'song saved' : 'songs saved'}</span></div>
          {count > 0 && (
            <div className="hero-actions">
              <Button asChild><a href={firstItem?.href || '#'}><PlayIcon /><span>Start listening</span></a></Button>
              <Button asChild variant="outline"><a href={`${firstItem?.href || '#'}?shuffle=1`}><ShuffleIcon /><span>Shuffle</span></a></Button>
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
          <>
            <section className="liked-songs-tools" aria-label="Liked songs tools">
              <label className="liked-songs-search">
                <SearchIcon />
                <Input value={query} onChange={(event) => setQuery(event.currentTarget.value)} placeholder="Search liked songs" />
                {query && <Button type="button" variant="ghost" size="icon-sm" aria-label="Clear liked songs search" onClick={() => setQuery('')}><XIcon /></Button>}
              </label>
              <label className="liked-songs-sort">
                <span>Sort</span>
                <Select value={sort} onValueChange={(value) => setSort(value as 'saved' | 'title' | 'artist')}>
                  <SelectTrigger aria-label="Sort liked songs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="saved">Liked order</SelectItem>
                    <SelectItem value="title">Title A-Z</SelectItem>
                    <SelectItem value="artist">Artist A-Z</SelectItem>
                  </SelectContent>
                </Select>
              </label>
            </section>
            {visibleItems.length ? (
            <section className="liked-songs-library" aria-labelledby="liked-songs-library-title">
              <div className="section-heading compact-heading">
                <div><p className="eyebrow">Your collection</p><h2 id="liked-songs-library-title">In rotation</h2></div>
                <span>{visibleItems.length} shown</span>
              </div>
            <div className="media-grid music-grid">
            {visibleItems.map((item, index) => {
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
            </section>
            ) : (
              <div className="empty-state"><SearchIcon /><strong>No liked songs match your search</strong><span>Try another title or artist.</span></div>
            )}
          </>
        )
      )}
    </main>
  );
}
