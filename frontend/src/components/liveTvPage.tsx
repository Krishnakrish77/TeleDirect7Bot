import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchLiveTvHealth } from '../api';
import { attachHls } from '../media/hls';
import { BroadcastIcon, HeartIcon, PlayIcon, SearchIcon, XIcon } from '../icons';
import { ErrorPanel, LoadingRows } from './common';
import type { IptvChannel, IptvHealthStatus, LiveTvResponse } from '../types';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Tabs, TabsList, TabsTrigger } from './ui/tabs';

const HLS_RE = /\.m3u8(?:[?#]|$)|[?&](?:type|format)=m3u8/i;
const FAVORITES_KEY = 'td:live-tv:favorites';
const RECENTS_KEY = 'td:live-tv:recent';
const MAX_RECENTS = 8;
const INITIAL_CHANNEL_RENDER_COUNT = 80;
const CHANNEL_RENDER_INCREMENT = 80;
const ALL_CHANNELS = 'All';
const FAVORITE_CHANNELS = '__favorites';
const RECENT_CHANNELS = '__recent';
// Health probing: batch ids in small groups so a 1,000-channel catalogue
// gets coverage without one giant request, and re-check after the server
// TTLs expire so currently-dead channels eventually recover their dot.
const HEALTH_BATCH_SIZE = 100;
const HEALTH_REFRESH_MS = 5 * 60 * 1000;
// How long "Connecting…" may hang before we declare the channel dead.
// hls.js retries non-fatal segment errors forever, so without this the
// spinner can spin indefinitely on a half-dead origin.
const CONNECT_TIMEOUT_MS = 20_000;
const failedLiveLogoKeys = new Set<string>();

function channelLogoKey(channel: IptvChannel): string {
  return `${channel.id}:${channel.logoUrl}`;
}

function hasUsableLogo(channel: IptvChannel | null | undefined, failedLogoKeys: Set<string>): channel is IptvChannel {
  if (!channel?.logoUrl) return false;
  return !failedLogoKeys.has(channelLogoKey(channel));
}

function useChannelHealth(channels: IptvChannel[]): Record<string, IptvHealthStatus> {
  const [statuses, setStatuses] = useState<Record<string, IptvHealthStatus>>({});
  const catalogueKey = useMemo(
    () => channels.map((channel) => `${channel.id}:${channel.updatedAt}`).join(','),
    [channels],
  );
  useEffect(() => {
    if (!channels.length) {
      setStatuses({});
      return undefined;
    }
    let cancelled = false;
    const controller = new AbortController();
    const probe = async () => {
      // Probe in batches; merge as results land so early batches light up
      // the rail while later ones are still in flight.
      for (let offset = 0; offset < channels.length; offset += HEALTH_BATCH_SIZE) {
        if (cancelled) return;
        const batch = channels.slice(offset, offset + HEALTH_BATCH_SIZE).map((channel) => channel.id);
        try {
          const result = await fetchLiveTvHealth(batch, controller.signal);
          if (cancelled) return;
          setStatuses((current) => ({ ...current, ...result.statuses }));
        } catch {
          if (cancelled) return;
          // Health is advisory: a failed poll just leaves dots blank.
          return;
        }
      }
    };
    void probe();
    const interval = window.setInterval(probe, HEALTH_REFRESH_MS);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(interval);
    };
    // catalogueKey covers channels + updatedAt; eslint-disable not needed as
    // channels is only used through the memoised key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalogueKey]);
  return statuses;
}

function HealthDot({ status, className }: { status: IptvHealthStatus | undefined; className: string }) {
  if (!status || status === 'unknown') return null;
  const label = status === 'ok' ? 'Channel is online' : 'Channel is currently offline';
  return <i className={`${className} ${status === 'ok' ? 'online' : 'offline'}`} role="img" aria-label={label} title={label} />;
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
  const [connecting, setConnecting] = useState(false);
  const [visibleChannelCount, setVisibleChannelCount] = useState(INITIAL_CHANNEL_RENDER_COUNT);
  const [favoriteIds, setFavoriteIds] = useState<Set<string>>(() => new Set(readStoredIds(FAVORITES_KEY)));
  const [recentIds, setRecentIds] = useState<string[]>(() => readStoredIds(RECENTS_KEY));
  const [failedLogoKeys, setFailedLogoKeys] = useState<Set<string>>(() => new Set(failedLiveLogoKeys));
  const healthStatuses = useChannelHealth(channels);

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
  // Browsing/searching must not replace or stop an active stream. Keep selection
  // and playback tied to the full catalogue, while filters only change the rail.
  const selected = channelById.get(selectedId) || channels[0] || null;
  const playbackChannel = channelById.get(playbackId) || null;
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

  const retryPlayback = () => {
    if (!playbackChannel) return;
    // Re-running the effect (via streamUrl identity) tears down the old
    // hls instance and reconnects from scratch.
    setPlaybackId('');
    window.requestAnimationFrame(() => setPlaybackId(playbackChannel.id));
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
    setConnecting(true);

    let cancelled = false;
    const sourceUrl = playbackChannel.streamUrl;
    const streamUrl = liveTvStreamUrl(playbackChannel);
    const play = () => {
      if (cancelled) return;
      void video.play().catch(() => undefined);
    };
    // Connecting indicator: cleared on first playing/direct-play; on stall
    // (HLS segments stop arriving) the video element's waiting/stalled state
    // is what the UI reports, not a false "unable to play".
    let connecting = true;
    const markConnected = () => {
      connecting = false;
      setConnecting(false);
      window.clearTimeout(timeoutId);
    };
    // hls.js retries non-fatal segment/manifest errors forever, so a dead
    // origin can leave "Connecting…" up indefinitely. Bound it: if no frame
    // decoded within the window, surface the failure.
    const timeoutId = window.setTimeout(() => {
      if (!cancelled && connecting) setPlaybackError('Unable to play this channel');
    }, CONNECT_TIMEOUT_MS);
    video.addEventListener('playing', markConnected, { once: true });

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
      window.clearTimeout(timeoutId);
      video.removeEventListener('playing', markConnected);
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
          <p>{selected ? channelCategory(selected) : loading && !data ? 'Loading channels…' : `${channels.length.toLocaleString()} channels`}</p>
        </div>
        <div className="live-tv-hero-count">
          <strong>{loading && !data ? '…' : channels.length.toLocaleString()}</strong>
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
                onError={() => {
                  // Switching channels (or the pre-attach phase of hls.js)
                  // can fire transient media errors — the same effect
                  // watch.tsx guards against. Only surface an error when a
                  // real stream is attached and has begun loading.
                  if (playbackChannel) setPlaybackError('Unable to play this channel');
                }}
              />
              {connecting && playbackChannel && !playbackError && (
                <div className="live-video-placeholder live-video-connecting" role="status" aria-live="polite">
                  <span className="live-connecting-spinner" aria-hidden="true" />
                  <span>Connecting to {playbackChannel.name}…</span>
                </div>
              )}
              {playbackError && playbackChannel && (
                <div className="live-video-placeholder live-video-error" role="alert">
                  <BroadcastIcon />
                  <strong>Unable to play {playbackChannel.name}</strong>
                  <span>The stream may be offline or temporarily unavailable.</span>
                  <div className="live-error-actions">
                    <Button type="button" variant="secondary" size="sm" onClick={retryPlayback}>
                      Retry
                    </Button>
                  </div>
                </div>
              )}
              {!playbackChannel && (
                <div className="live-video-placeholder">
                  <BroadcastIcon />
                  {selected && (
                    <Button type="button" className="live-play-button" onClick={playSelected}>
                      <PlayIcon />
                      <span>Play channel</span>
                    </Button>
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
                    {selected && (
                      <span className="live-now-state">
                        {playbackError && playbackChannel?.id === selected.id
                          ? 'Offline'
                          : playbackChannel
                            ? 'Playing'
                            : healthStatuses[selected.id] === 'down'
                              ? 'Offline'
                              : healthStatuses[selected.id] === 'ok'
                                ? 'Online'
                                : 'Selected'}
                      </span>
                    )}
                  </small>
                </div>
              </div>
              <div className="live-now-actions">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className={selectedFavorite ? 'icon-button live-favorite-button active' : 'icon-button live-favorite-button'}
                  disabled={!selected}
                  onClick={toggleSelectedFavorite}
                  aria-label={selectedFavorite && selected ? `Remove ${selected.name} from favorites` : selected ? `Add ${selected.name} to favorites` : 'Favorite channel'}
                  title={selectedFavorite ? 'Remove favorite' : 'Add favorite'}
                >
                  <HeartIcon filled={selectedFavorite} />
                </Button>
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
                  <Button type="button" variant="ghost" size="sm" className="live-clear-filters" onClick={clearChannelFilters}>
                    Clear filters
                  </Button>
                )}
              </div>
              <label className="live-search">
                <SearchIcon />
                <Input
                  name="channel-search"
                  value={query}
                  onChange={(event) => setQuery(event.currentTarget.value)}
                  placeholder="Search channels"
                />
                {query && (
                  <Button type="button" variant="ghost" size="icon-sm" className="icon-button" aria-label="Clear channel search" onClick={() => setQuery('')}>
                    <XIcon />
                  </Button>
                )}
              </label>
              <Tabs value={activeCategory} onValueChange={setActiveCategory}>
                <TabsList className="live-category-tabs" aria-label="Channel categories">
                <TabsTrigger value={ALL_CHANNELS} onClick={() => setActiveCategory(ALL_CHANNELS)}>
                  All
                  <span>{channels.length}</span>
                </TabsTrigger>
                <TabsTrigger value={FAVORITE_CHANNELS} onClick={() => setActiveCategory(FAVORITE_CHANNELS)}>
                  Favorites
                  <span>{favoriteChannels.length}</span>
                </TabsTrigger>
                <TabsTrigger value={RECENT_CHANNELS} onClick={() => setActiveCategory(RECENT_CHANNELS)}>
                  Recent
                  <span>{recentChannels.length}</span>
                </TabsTrigger>
                {categories.map(([category, count]) => (
                  <TabsTrigger
                    key={category}
                    value={category}
                    onClick={() => setActiveCategory(category)}
                  >
                    {category}
                    <span>{count}</span>
                  </TabsTrigger>
                ))}
                </TabsList>
              </Tabs>
            </div>
            <div className="live-channel-list">
              {visibleChannels.map((channel) => {
                const health = healthStatuses[channel.id];
                const rowClass = [
                  'live-channel-row h-auto justify-start p-0',
                  selected?.id === channel.id ? 'active' : '',
                  health === 'down' ? 'offline' : '',
                ].filter(Boolean).join(' ');
                return (
                  <Button
                    key={channel.id}
                    type="button"
                    variant="ghost"
                    className={rowClass}
                    onClick={() => selectAndPlay(channel.id)}
                  >
                    <ChannelLogo channel={channel} failedLogoKeys={failedLogoKeys} onLogoError={markLogoFailed} />
                    <strong>{channel.name}</strong>
                    <small>{channelCategory(channel)}</small>
                    <em className="live-channel-icons">
                      {favoriteIds.has(channel.id) && <HeartIcon filled />}
                      <HealthDot status={health} className="live-health-dot" />
                      <PlayIcon />
                    </em>
                  </Button>
                );
              })}
              {remainingChannelCount > 0 && (
                <Button
                  type="button"
                  variant="outline"
                  className="live-channel-more"
                  onClick={() => setVisibleChannelCount((current) => Math.min(filteredChannels.length, current + CHANNEL_RENDER_INCREMENT))}
                >
                  Show more
                  <span>{remainingChannelCount.toLocaleString()} hidden</span>
                </Button>
              )}
              {!filteredChannels.length && (
                <div className="live-channel-empty">
                  <strong>{emptyMessage}</strong>
                  {filterActive && (
                    <Button type="button" variant="secondary" size="sm" onClick={clearChannelFilters}>
                      Clear filters
                    </Button>
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
