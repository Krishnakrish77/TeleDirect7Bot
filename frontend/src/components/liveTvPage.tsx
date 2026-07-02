import { useEffect, useMemo, useRef, useState } from 'react';
import { attachHls } from '../media/hls';
import { BroadcastIcon, HeartIcon, PlayIcon, SearchIcon, XIcon } from '../icons';
import { ErrorPanel, LoadingRows } from './common';
import type { IptvChannel, LiveTvResponse } from '../types';

const HLS_RE = /\.m3u8(?:[?#]|$)|[?&](?:type|format)=m3u8/i;
const FAVORITES_KEY = 'td:live-tv:favorites';
const RECENTS_KEY = 'td:live-tv:recent';
const MAX_RECENTS = 8;
const ALL_CHANNELS = 'All';
const FAVORITE_CHANNELS = '__favorites';
const RECENT_CHANNELS = '__recent';

function readStoredIds(key: string): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(parsed) ? parsed.filter((value) => typeof value === 'string') : [];
  } catch (_) {
    return [];
  }
}

function writeStoredIds(key: string, ids: string[]) {
  try {
    localStorage.setItem(key, JSON.stringify(ids));
  } catch (_) {
    // Local convenience state only; playback should never depend on storage.
  }
}

function sameIds(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

function channelCategory(channel: IptvChannel): string {
  return channel.category?.trim() || 'Uncategorized';
}

function categoryCounts(channels: IptvChannel[]): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const channel of channels) {
    const category = channelCategory(channel);
    counts.set(category, (counts.get(category) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

function hasStreamHeaders(channel: IptvChannel | null): boolean {
  return Object.values(channel?.streamHeaders || {}).some((value) => Boolean(String(value || '').trim()));
}

function liveTvStreamUrl(channel: IptvChannel): string {
  return `/api/live-tv/stream/${encodeURIComponent(channel.id)}`;
}

export function LiveTvPage({
  data,
  loading,
  error,
}: {
  data: LiveTvResponse | null;
  loading: boolean;
  error: string;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const hlsRef = useRef<{ destroy: () => void } | null>(null);
  const channels = data?.channels ?? [];
  const [selectedId, setSelectedId] = useState('');
  const [activeCategory, setActiveCategory] = useState(ALL_CHANNELS);
  const [query, setQuery] = useState('');
  const [playbackError, setPlaybackError] = useState('');
  const [favoriteIds, setFavoriteIds] = useState<Set<string>>(() => new Set(readStoredIds(FAVORITES_KEY)));
  const [recentIds, setRecentIds] = useState<string[]>(() => readStoredIds(RECENTS_KEY));

  useEffect(() => {
    if (!channels.length) {
      setSelectedId('');
      return;
    }
    setSelectedId((current) => channels.some((channel) => channel.id === current) ? current : channels[0].id);
  }, [channels]);

  const channelById = useMemo(() => new Map(channels.map((channel) => [channel.id, channel])), [channels]);
  const categories = useMemo(() => categoryCounts(channels), [channels]);
  const favoriteChannels = useMemo(() => channels.filter((channel) => favoriteIds.has(channel.id)), [channels, favoriteIds]);
  const recentChannels = useMemo(
    () => recentIds.flatMap((id) => {
      const channel = channelById.get(id);
      return channel ? [channel] : [];
    }),
    [channelById, recentIds],
  );
  const filteredChannels = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const scopedChannels = activeCategory === FAVORITE_CHANNELS
      ? favoriteChannels
      : activeCategory === RECENT_CHANNELS
        ? recentChannels
        : channels;
    const categoryFilterActive = ![ALL_CHANNELS, FAVORITE_CHANNELS, RECENT_CHANNELS].includes(activeCategory);
    return scopedChannels.filter((channel) => {
      if (categoryFilterActive && channelCategory(channel) !== activeCategory) return false;
      if (!needle) return true;
      return `${channel.name} ${channel.category}`.toLowerCase().includes(needle);
    });
  }, [activeCategory, channels, favoriteChannels, query, recentChannels]);
  // Don't fall back to channels[0] when a filter is active and produces no results —
  // that would silently stream a hidden channel while the list shows "no results".
  const selected = filteredChannels.find((channel) => channel.id === selectedId) || filteredChannels[0] || null;
  const selectedFavorite = Boolean(selected && favoriteIds.has(selected.id));

  useEffect(() => {
    const validIds = new Set(channels.map((channel) => channel.id));
    setFavoriteIds((current) => {
      const nextIds = [...current].filter((id) => validIds.has(id));
      if (nextIds.length === current.size) return current;
      writeStoredIds(FAVORITES_KEY, nextIds);
      return new Set(nextIds);
    });
    setRecentIds((current) => {
      const nextIds = current.filter((id) => validIds.has(id));
      if (sameIds(current, nextIds)) return current;
      writeStoredIds(RECENTS_KEY, nextIds);
      return nextIds;
    });
  }, [channels]);

  useEffect(() => {
    if (!selected?.id) return;
    setRecentIds((current) => {
      const nextIds = [selected.id, ...current.filter((id) => id !== selected.id)].slice(0, MAX_RECENTS);
      if (sameIds(current, nextIds)) return current;
      writeStoredIds(RECENTS_KEY, nextIds);
      return nextIds;
    });
  }, [selected?.id]);

  const toggleSelectedFavorite = () => {
    if (!selected) return;
    setFavoriteIds((current) => {
      const next = new Set(current);
      if (next.has(selected.id)) next.delete(selected.id);
      else next.add(selected.id);
      writeStoredIds(FAVORITES_KEY, [...next]);
      return next;
    });
  };

  useEffect(() => {
    const video = videoRef.current;
    hlsRef.current?.destroy();
    hlsRef.current = null;
    setPlaybackError('');
    if (!video || !selected?.streamUrl) return undefined;

    let cancelled = false;
    video.pause();
    video.removeAttribute('src');
    video.load();

    const sourceUrl = selected.streamUrl;
    const streamUrl = hasStreamHeaders(selected) ? liveTvStreamUrl(selected) : sourceUrl;
    const play = () => {
      if (cancelled) return;
      void video.play().catch(() => undefined);
    };

    if (HLS_RE.test(sourceUrl)) {
      attachHls(video, streamUrl, '', () => {
        if (!cancelled) setPlaybackError('Unable to play this channel');
      }).then((instance) => {
        if (cancelled) {
          instance?.destroy();
          return;
        }
        hlsRef.current = instance;
        play();
      });
    } else {
      video.src = streamUrl;
      video.load();
      play();
    }

    return () => {
      cancelled = true;
      hlsRef.current?.destroy();
      hlsRef.current = null;
    };
  }, [selected?.id, selected?.streamHeaders, selected?.streamUrl]);

  return (
    <main className="live-tv-main">
      <section className="live-tv-hero">
        <div>
          <p className="eyebrow">Live TV</p>
          <h1>{selected?.name || 'Live TV'}</h1>
          <p>{selected ? channelCategory(selected) : `${channels.length.toLocaleString()} channels`}</p>
        </div>
        <div className="live-tv-hero-count">
          <strong>{channels.length.toLocaleString()}</strong>
          <span>channels</span>
        </div>
      </section>

      {loading && !data && <LoadingRows variant="detail" />}
      {error && <ErrorPanel message={error} />}

      {!loading && !error && !channels.length && (
        <div className="empty-state">
          <BroadcastIcon />
          <strong>No IPTV channels are available</strong>
        </div>
      )}

      {channels.length > 0 && (
        <section className="live-tv-layout" aria-label="Live TV player">
          <div className="live-player-panel">
            <div className="live-video-frame">
              <video
                ref={videoRef}
                controls
                autoPlay
                playsInline
                poster={selected?.logoUrl || undefined}
                onError={() => setPlaybackError('Unable to play this channel')}
              />
              {!selected && (
                <div className="live-video-placeholder">
                  <BroadcastIcon />
                </div>
              )}
            </div>
            <div className="live-now-row">
              <div className="live-now-copy">
                {selected?.logoUrl ? <img src={selected.logoUrl} alt="" /> : <span><BroadcastIcon /></span>}
                <div>
                  <strong>{selected?.name || 'No channel selected'}</strong>
                  <small>{selected ? channelCategory(selected) : 'Live TV'}</small>
                </div>
              </div>
              <div className="live-now-actions">
                <button
                  type="button"
                  className={selectedFavorite ? 'icon-button live-favorite-button active' : 'icon-button live-favorite-button'}
                  disabled={!selected}
                  onClick={toggleSelectedFavorite}
                  aria-label={selectedFavorite && selected ? `Remove ${selected.name} from favorites` : selected ? `Add ${selected.name} to favorites` : 'Favorite channel'}
                  title={selectedFavorite ? 'Remove favorite' : 'Add favorite'}
                >
                  <HeartIcon filled={selectedFavorite} />
                </button>
                {playbackError && <p role="status">{playbackError}</p>}
              </div>
            </div>
          </div>

          <aside className="live-channel-rail" aria-label="Channels">
            <div className="live-channel-tools">
              <label className="live-search">
                <SearchIcon />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.currentTarget.value)}
                  placeholder="Search channels"
                />
                {query && (
                  <button type="button" className="icon-button" aria-label="Clear channel search" onClick={() => setQuery('')}>
                    <XIcon />
                  </button>
                )}
              </label>
              <div className="live-category-tabs" role="tablist" aria-label="Channel categories">
                <button type="button" className={activeCategory === ALL_CHANNELS ? 'active' : ''} onClick={() => setActiveCategory(ALL_CHANNELS)}>
                  All
                  <span>{channels.length}</span>
                </button>
                <button type="button" className={activeCategory === FAVORITE_CHANNELS ? 'active' : ''} onClick={() => setActiveCategory(FAVORITE_CHANNELS)}>
                  Favorites
                  <span>{favoriteChannels.length}</span>
                </button>
                <button type="button" className={activeCategory === RECENT_CHANNELS ? 'active' : ''} onClick={() => setActiveCategory(RECENT_CHANNELS)}>
                  Recent
                  <span>{recentChannels.length}</span>
                </button>
                {categories.map(([category, count]) => (
                  <button
                    key={category}
                    type="button"
                    className={activeCategory === category ? 'active' : ''}
                    onClick={() => setActiveCategory(category)}
                  >
                    {category}
                    <span>{count}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="live-channel-list">
              {filteredChannels.map((channel) => (
                <button
                  key={channel.id}
                  type="button"
                  className={selected?.id === channel.id ? 'live-channel-row active' : 'live-channel-row'}
                  onClick={() => setSelectedId(channel.id)}
                >
                  {channel.logoUrl ? <img src={channel.logoUrl} alt="" /> : <span><BroadcastIcon /></span>}
                  <strong>{channel.name}</strong>
                  <small>{channelCategory(channel)}</small>
                  <em className="live-channel-icons">
                    {favoriteIds.has(channel.id) && <HeartIcon filled />}
                    <PlayIcon />
                  </em>
                </button>
              ))}
              {!filteredChannels.length && (
                <div className="live-channel-empty">No channels match this view</div>
              )}
            </div>
          </aside>
        </section>
      )}
    </main>
  );
}
