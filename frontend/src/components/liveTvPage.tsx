import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { attachHls } from '../media/hls';
import { BroadcastIcon, HeartIcon, PlayIcon, SearchIcon, XIcon } from '../icons';
import { ErrorPanel, LoadingRows } from './common';
import type { IptvChannel, LiveTvResponse } from '../types';

const HLS_RE = /\.m3u8(?:[?#]|$)|[?&](?:type|format)=m3u8/i;
const FAVORITES_KEY = 'td:live-tv:favorites';
const RECENTS_KEY = 'td:live-tv:recent';
const MAX_RECENTS = 8;
const INITIAL_CHANNEL_RENDER_COUNT = 80;
const CHANNEL_RENDER_INCREMENT = 80;
const ALL_CHANNELS = 'All';
const FAVORITE_CHANNELS = '__favorites';
const RECENT_CHANNELS = '__recent';
const failedLiveLogoKeys = new Set<string>();

function channelLogoKey(channel: IptvChannel): string {
  return `${channel.id}:${channel.logoUrl}`;
}

function hasUsableLogo(channel: IptvChannel | null | undefined, failedLogoKeys: Set<string>): channel is IptvChannel {
  if (!channel?.logoUrl) return false;
  return !failedLogoKeys.has(channelLogoKey(channel));
}

function ChannelLogo({
  channel,
  failedLogoKeys,
  onLogoError,
}: {
  channel: IptvChannel | null | undefined;
  failedLogoKeys: Set<string>;
  onLogoError: (channel: IptvChannel) => void;
}) {
  if (!hasUsableLogo(channel, failedLogoKeys)) {
    return <span><BroadcastIcon /></span>;
  }
  return (
    <img
      key={channelLogoKey(channel)}
      src={channel.logoUrl}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => onLogoError(channel)}
    />
  );
}

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

function liveTvStreamUrl(channel: IptvChannel): string {
  return `/api/live-tv/stream/${encodeURIComponent(channel.id)}`;
}

function activeCategoryLabel(activeCategory: string): string {
  if (activeCategory === ALL_CHANNELS) return 'All channels';
  if (activeCategory === FAVORITE_CHANNELS) return 'Favorites';
  if (activeCategory === RECENT_CHANNELS) return 'Recent';
  return activeCategory;
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
  const [playbackId, setPlaybackId] = useState('');
  const [visibleChannelCount, setVisibleChannelCount] = useState(INITIAL_CHANNEL_RENDER_COUNT);
  const [favoriteIds, setFavoriteIds] = useState<Set<string>>(() => new Set(readStoredIds(FAVORITES_KEY)));
  const [recentIds, setRecentIds] = useState<string[]>(() => readStoredIds(RECENTS_KEY));
  const [failedLogoKeys, setFailedLogoKeys] = useState<Set<string>>(() => new Set(failedLiveLogoKeys));

  useEffect(() => {
    if (!channels.length) {
      setSelectedId('');
      return;
    }
    setSelectedId((current) => channels.some((channel) => channel.id === current) ? current : channels[0].id);
  }, [channels]);

  useEffect(() => {
    setVisibleChannelCount(INITIAL_CHANNEL_RENDER_COUNT);
  }, [activeCategory, query]);

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
  const playbackChannel = selected?.id === playbackId ? selected : null;
  const selectedFavorite = Boolean(selected && favoriteIds.has(selected.id));
  const visibleChannels = useMemo(
    () => filteredChannels.slice(0, visibleChannelCount),
    [filteredChannels, visibleChannelCount],
  );
  const remainingChannelCount = Math.max(0, filteredChannels.length - visibleChannels.length);
  const activeViewLabel = activeCategoryLabel(activeCategory);
  const filterActive = activeCategory !== ALL_CHANNELS || Boolean(query.trim());
  const emptyMessage = query.trim()
    ? `No matches for "${query.trim()}" in ${activeViewLabel}.`
    : activeCategory === FAVORITE_CHANNELS
      ? 'No favorites yet. Use the heart on a channel to save it here.'
      : activeCategory === RECENT_CHANNELS
        ? 'No recent channels yet. Play a channel and it will appear here.'
        : 'No channels match this view.';
  const clearChannelFilters = () => {
    setQuery('');
    setActiveCategory(ALL_CHANNELS);
  };

  useEffect(() => {
    const validIds = new Set(channels.map((channel) => channel.id));
    setPlaybackId((current) => validIds.has(current) ? current : '');
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
    if (!playbackChannel?.id) return;
    setRecentIds((current) => {
      const nextIds = [playbackChannel.id, ...current.filter((id) => id !== playbackChannel.id)].slice(0, MAX_RECENTS);
      if (sameIds(current, nextIds)) return current;
      writeStoredIds(RECENTS_KEY, nextIds);
      return nextIds;
    });
  }, [playbackChannel?.id]);

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

  const playSelected = () => {
    if (!selected) return;
    setPlaybackId(selected.id);
  };

  const selectAndPlay = (channelId: string) => {
    setSelectedId(channelId);
    setPlaybackId(channelId);
  };

  const markLogoFailed = useCallback((channel: IptvChannel) => {
    const key = channelLogoKey(channel);
    failedLiveLogoKeys.add(key);
    setFailedLogoKeys((current) => {
      if (current.has(key)) return current;
      return new Set([...current, key]);
    });
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    hlsRef.current?.destroy();
    hlsRef.current = null;
    setPlaybackError('');
    if (!video) return undefined;

    video.pause();
    video.removeAttribute('src');
    video.load();
    if (!playbackChannel?.streamUrl) return undefined;

    let cancelled = false;
    const sourceUrl = playbackChannel.streamUrl;
    const streamUrl = liveTvStreamUrl(playbackChannel);
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
  }, [playbackChannel?.id, playbackChannel?.streamUrl]);

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
                controls={Boolean(playbackChannel)}
                playsInline
                preload={playbackChannel ? 'auto' : 'none'}
                poster={hasUsableLogo(playbackChannel, failedLogoKeys) ? playbackChannel.logoUrl : undefined}
                onError={() => setPlaybackError('Unable to play this channel')}
              />
              {!playbackChannel && (
                <div className="live-video-placeholder">
                  <BroadcastIcon />
                  {selected && (
                    <button type="button" className="primary-action live-play-button" onClick={playSelected}>
                      <PlayIcon />
                      <span>Play channel</span>
                    </button>
                  )}
                </div>
              )}
            </div>
            <div className="live-now-row">
              <div className="live-now-copy">
                <ChannelLogo channel={selected} failedLogoKeys={failedLogoKeys} onLogoError={markLogoFailed} />
                <div>
                  <strong>{selected?.name || 'No channel selected'}</strong>
                  <small>
                    {selected ? channelCategory(selected) : 'Live TV'}
                    {selected && <span>{playbackChannel ? 'Playing' : 'Selected'}</span>}
                  </small>
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
              <div className="live-channel-summary">
                <div>
                  <strong>{filteredChannels.length.toLocaleString()}</strong>
                  <span>{filteredChannels.length === 1 ? 'channel' : 'channels'} in {activeViewLabel}</span>
                </div>
                {filterActive && filteredChannels.length > 0 && (
                  <button type="button" className="text-button live-clear-filters" onClick={clearChannelFilters}>
                    Clear filters
                  </button>
                )}
              </div>
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
                <button type="button" role="tab" aria-selected={activeCategory === ALL_CHANNELS} className={activeCategory === ALL_CHANNELS ? 'active' : ''} onClick={() => setActiveCategory(ALL_CHANNELS)}>
                  All
                  <span>{channels.length}</span>
                </button>
                <button type="button" role="tab" aria-selected={activeCategory === FAVORITE_CHANNELS} className={activeCategory === FAVORITE_CHANNELS ? 'active' : ''} onClick={() => setActiveCategory(FAVORITE_CHANNELS)}>
                  Favorites
                  <span>{favoriteChannels.length}</span>
                </button>
                <button type="button" role="tab" aria-selected={activeCategory === RECENT_CHANNELS} className={activeCategory === RECENT_CHANNELS ? 'active' : ''} onClick={() => setActiveCategory(RECENT_CHANNELS)}>
                  Recent
                  <span>{recentChannels.length}</span>
                </button>
                {categories.map(([category, count]) => (
                  <button
                    key={category}
                    type="button"
                    role="tab"
                    aria-selected={activeCategory === category}
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
              {visibleChannels.map((channel) => (
                <button
                  key={channel.id}
                  type="button"
                  className={selected?.id === channel.id ? 'live-channel-row active' : 'live-channel-row'}
                  onClick={() => selectAndPlay(channel.id)}
                >
                  <ChannelLogo channel={channel} failedLogoKeys={failedLogoKeys} onLogoError={markLogoFailed} />
                  <strong>{channel.name}</strong>
                  <small>{channelCategory(channel)}</small>
                  <em className="live-channel-icons">
                    {favoriteIds.has(channel.id) && <HeartIcon filled />}
                    <PlayIcon />
                  </em>
                </button>
              ))}
              {remainingChannelCount > 0 && (
                <button
                  type="button"
                  className="live-channel-more"
                  onClick={() => setVisibleChannelCount((current) => Math.min(filteredChannels.length, current + CHANNEL_RENDER_INCREMENT))}
                >
                  Show more
                  <span>{remainingChannelCount.toLocaleString()} hidden</span>
                </button>
              )}
              {!filteredChannels.length && (
                <div className="live-channel-empty">
                  <strong>{emptyMessage}</strong>
                  {filterActive && (
                    <button type="button" className="secondary-action compact-action" onClick={clearChannelFilters}>
                      Clear filters
                    </button>
                  )}
                </div>
              )}
            </div>
          </aside>
        </section>
      )}
    </main>
  );
}
