import { type CSSProperties, DragEvent, MouseEvent, TouchEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { deleteContinueEntry, fetchAudioTracks, fetchSubtitles, fetchWatch, recordWatchHistory, saveContinueEntry } from '../api';
import { CaptionsIcon, ChevronRightIcon, DownloadIcon, FilmIcon, HeartIcon, ListIcon, ListPlusIcon, MaximizeIcon, MoreVerticalIcon, PauseIcon, PictureInPictureIcon, PlayIcon, ShareIcon, ShuffleIcon, SkipBackIcon, SkipForwardIcon, VolumeIcon } from '../icons';
import { formatClock, RESTORE_AUDIO_MEDIA_SESSION_EVENT, type AudioPlayerHandle, type PlayerState } from '../hooks/audio';
import type { AudioTrackOption, SubtitleTrack, WatchResponse, WatchTrack, WatchVideo } from '../types';
import { ErrorPanel, LoadingRows } from './common';
import { LyricsFlipCard, LyricsPanel } from './lyrics';
import { RatingControls } from './rating';
import { attachHls, hlsUrl } from '../media/hls';
import { restoreCachedSubtitle, revokeSubtitleTrack, subtitleFileToTrack } from '../media/subtitles';
import { buildVlcHref } from '../media/vlc';
import { markLocallyWatched } from '../utils/localWatched';
import { uniqueMetadataParts } from '../utils/metadata';

function isWatchTrack(item: WatchResponse['item']): item is WatchTrack {
  return item.type === 'track' && 'appHref' in item;
}

function isWatchVideo(item: WatchResponse['item']): item is WatchVideo {
  return item.type === 'video' && 'directSrc' in item;
}

export const STILL_WATCHING_TIMEOUT_MS = 45 * 60 * 1000;
const NEXT_EPISODE_COUNTDOWN_SECONDS = 8;

type NextEpisode = NonNullable<WatchVideo['nextEpisode']>;

function isVideoChromeTarget(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest(
    'button, a, input, select, textarea, label, .video-controls, .video-options-menu, .skip-intro, .next-episode-card, .video-overlay-message, .still-watching-overlay',
  ));
}

function VideoInfoSection({ video }: { video: WatchVideo }) {
  const meta = video.metadata;
  const genres = (video.genres.length ? video.genres : meta.genres).slice(0, 5);
  const overview = (video.overview || meta.overview || '').trim();
  const facts = uniqueMetadataParts([
    video.episodeLabel,
    video.year || meta.year,
    video.durationLabel,
    video.fileSizeLabel,
    video.quality,
  ]);
  const directors = meta.directors.length
    ? meta.directors
    : meta.director
      ? [{ name: meta.director, href: `/app/person/${encodeURIComponent(meta.director.toLowerCase().replace(/\s+/g, '-'))}` }]
      : [];
  const cast = meta.cast.slice(0, 6);
  const infoTitle = (meta.title || video.title).trim();
  const kindLabel = video.episodeLabel || video.subtitle.toLowerCase().includes('s0')
    ? 'About this episode'
    : 'About this title';
  const sectionTitle = infoTitle && infoTitle !== video.title ? infoTitle : 'Overview';

  if (!overview && !facts.length && !genres.length && !directors.length && !cast.length && !meta.imdbHref) {
    return null;
  }

  return (
    <section className="video-info-section" aria-label="Movie and series information">
      <div className="video-info-copy">
        <p className="eyebrow">{kindLabel}</p>
        <h2 dir="auto">{sectionTitle}</h2>
        {overview && <p className="video-info-overview">{overview}</p>}
        {(facts.length > 0 || genres.length > 0) && (
          <div className="video-info-chips" aria-label="Media details">
            {facts.map((fact) => <span key={fact}>{fact}</span>)}
            {genres.map((genre) => <span key={genre}>{genre}</span>)}
          </div>
        )}
      </div>
      {(directors.length > 0 || cast.length > 0 || meta.imdbHref) && (
        <dl className="video-info-credits">
          {directors.length > 0 && (
            <div>
              <dt>{directors.length === 1 ? 'Director' : 'Directors'}</dt>
              <dd>
                {directors.slice(0, 3).map((person) => (
                  <a key={person.href || person.name} href={person.href}>{person.name}</a>
                ))}
              </dd>
            </div>
          )}
          {cast.length > 0 && (
            <div>
              <dt>Cast</dt>
              <dd>
                {cast.map((person) => (
                  <a key={person.href || person.name} href={person.href}>{person.name}</a>
                ))}
              </dd>
            </div>
          )}
          {meta.imdbHref && (
            <div>
              <dt>Links</dt>
              <dd>
                <a href={meta.imdbHref}>IMDb</a>
              </dd>
            </div>
          )}
        </dl>
      )}
    </section>
  );
}

function nextEpisodeLabel(nextEpisode: NextEpisode): string {
  if (typeof nextEpisode.season === 'number' && typeof nextEpisode.episode === 'number') {
    return `S${String(nextEpisode.season).padStart(2, '0')}E${String(nextEpisode.episode).padStart(2, '0')}`;
  }
  if (typeof nextEpisode.episode === 'number') return `Episode ${nextEpisode.episode}`;
  return '';
}

function NextEpisodePanel({
  nextEpisode,
  autoplay,
  countdown,
  playHref,
  onReplay,
  onDismiss,
  onToggleAutoplay,
}: {
  nextEpisode: NextEpisode;
  autoplay: boolean;
  countdown: number;
  playHref: string;
  onReplay: () => void;
  onDismiss: () => void;
  onToggleAutoplay: () => void;
}) {
  const label = nextEpisodeLabel(nextEpisode);
  const safeCountdown = Math.max(0, Math.min(NEXT_EPISODE_COUNTDOWN_SECONDS, countdown));
  const progress = autoplay ? Math.round((safeCountdown / NEXT_EPISODE_COUNTDOWN_SECONDS) * 100) : 0;
  return (
    <div className="next-episode-card" role="dialog" aria-labelledby="next-episode-title">
      <div className="next-episode-art">
        <img src={nextEpisode.posterUrl} alt="" decoding="async" />
        {label && <span>{label}</span>}
      </div>
      <div className="next-episode-copy">
        <p className="eyebrow">{autoplay ? `Playing next in ${safeCountdown}s` : 'Up next'}</p>
        <strong id="next-episode-title" dir="auto">{nextEpisode.title}</strong>
        {label && <span>{label}</span>}
        <div className="next-episode-actions">
          <a className="primary-action" href={playHref}>
            <PlayIcon />
            <span>Play now</span>
          </a>
          <button type="button" className="secondary-action" onClick={onReplay}>
            <SkipBackIcon />
            <span>Replay</span>
          </button>
          <button type="button" className="secondary-action" onClick={onDismiss}>Stay here</button>
        </div>
      </div>
      <div className="next-episode-side">
        <div
          className={autoplay ? 'next-countdown active' : 'next-countdown'}
          role="timer"
          aria-label={autoplay ? `${safeCountdown} seconds until next episode` : 'Autoplay paused'}
          style={{ '--next-progress': `${progress}%` } as CSSProperties}
        >
          {autoplay ? <span>{safeCountdown}</span> : <PlayIcon />}
        </div>
        <label className="next-autoplay-toggle">
          <input type="checkbox" checked={autoplay} onChange={onToggleAutoplay} />
          <span>Autoplay</span>
        </label>
      </div>
    </div>
  );
}

export function WatchPage({
  watchKey,
  audio,
  onOpenQueue,
  onAddToPlaylist,
  savedIds,
  onToggleSaved,
}: {
  watchKey: string;
  audio: AudioPlayerHandle;
  onOpenQueue: () => void;
  onAddToPlaylist?: (track: WatchTrack) => void;
  savedIds?: Set<string>;
  onToggleSaved?: (itemId: string) => void;
}) {
  const { player, playTrack, playRelative, playQueueIndex, addToQueue, shuffleQueue,
    togglePlayback, seek, setSpeed, cycleRepeatMode, setVolume, toggleMute,
    confirmNext, cancelNext } = audio;
  const [data, setData] = useState<WatchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchWatch(watchKey, controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load this item');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [watchKey]);

  if (loading) {
    return (
      <main className="watch-main">
        <LoadingRows />
      </main>
    );
  }

  if (error || !data) {
    return (
      <main className="watch-main">
        <ErrorPanel message={error || 'Unable to load this item'} />
      </main>
    );
  }

  if (isWatchVideo(data.item)) {
    return <VideoWatchPage video={data.item} />;
  }

  if (!isWatchTrack(data.item)) {
    const card = data.item;
    return (
      <main className="watch-main">
        <section className="watch-fallback">
          <img src={card.posterUrl} alt="" decoding="async" />
          <div>
            <p className="eyebrow">{card.mediaKind || 'Media'}</p>
            <h1 dir="auto">{card.title}</h1>
            <p>{card.overview || card.subtitle || 'Open this item in the classic player.'}</p>
            <a className="primary-action" href={data.classicHref || card.href}>
              <PlayIcon />
              <span>Open player</span>
            </a>
          </div>
        </section>
      </main>
    );
  }

  const track = data.item;
  const queue = data.albumTracks?.length ? data.albumTracks : [track];
  const current = player.track?.key === track.key;
  const playing = current && player.playing;
  const duration = current ? player.duration || track.duration || 0 : track.duration || 0;
  const currentTime = current ? player.currentTime : 0;
  const rangeMax = Math.max(1, Math.round(duration));
  const prevAvailable = current
    ? player.queueIndex > 0
    : Boolean(data.prev);
  const nextAvailable = current
    ? player.queueIndex + 1 < player.queue.length
    : Boolean(data.next);
  const seekAudioBy = (delta: number) => {
    if (!current) return;
    const upperBound = duration > 0 ? duration : Number.POSITIVE_INFINITY;
    seek(Math.max(0, Math.min(upperBound, currentTime + delta)));
  };
  const shareAudio = async () => {
    const payload = { title: track.title, url: window.location.href };
    if (navigator.share) {
      try { await navigator.share(payload); } catch (_) { return; }
    } else if (navigator.clipboard) {
      await navigator.clipboard.writeText(window.location.href).catch(() => undefined);
    }
  };

  return (
    <main className="watch-main audio-watch-main">
      <section className="audio-watch">
        <LyricsFlipCard
          track={track}
          currentTime={currentTime}
          seek={(seconds) => {
            if (!current) {
              playTrack(track, queue);
              window.setTimeout(() => seek(seconds), 0);
              return;
            }
            seek(seconds);
          }}
        />
        <div className="audio-details">
          <div className="audio-hero-copy">
            <p className="eyebrow">{[track.format || 'Music', queue.length > 1 ? `${queue.length} tracks` : ''].filter(Boolean).join(' - ')}</p>
            <h1 dir="auto">{track.title}</h1>
            <p className="audio-subtitle">
              {[track.artist, track.albumTitle].filter(Boolean).join(' - ')}
            </p>
            {track.qualityLabel && track.qualityLabel !== track.format && (
              <p className="audio-quality-badge">{track.qualityLabel}</p>
            )}
            {track.overview && <p className="audio-overview">{track.overview}</p>}
          </div>

          <div className="audio-player-surface">
            <div className="watch-controls">
              <button
                type="button"
                className="icon-button player-nav"
                onClick={() => (current ? playRelative(-1) : data.prev && playTrack(data.prev, queue))}
                disabled={!prevAvailable}
                aria-label="Previous track"
              >
                <SkipBackIcon />
              </button>
              <button
                type="button"
                className="icon-button player-nav"
                onClick={() => seekAudioBy(-10)}
                disabled={!current}
                aria-label="Rewind 10 seconds"
              >
                <span aria-hidden="true">-10</span>
              </button>
              <button
                type="button"
                className="player-play"
                onClick={() => togglePlayback(track, queue)}
                aria-label={playing ? 'Pause' : 'Play'}
              >
                {playing ? <PauseIcon /> : <PlayIcon />}
              </button>
              <button
                type="button"
                className="icon-button player-nav"
                onClick={() => seekAudioBy(10)}
                disabled={!current}
                aria-label="Forward 10 seconds"
              >
                <span aria-hidden="true">+10</span>
              </button>
              <button
                type="button"
                className="icon-button player-nav"
                onClick={() => (current ? playRelative(1) : data.next && playTrack(data.next, queue))}
                disabled={!nextAvailable}
                aria-label="Next track"
              >
                <SkipForwardIcon />
              </button>
              <button
                type="button"
                className="icon-button player-nav"
                onClick={onOpenQueue}
                aria-label="Open queue"
              >
                <ListIcon />
              </button>
            </div>

            <div className="watch-progress">
              <span>{formatClock(currentTime)}</span>
              <input
                type="range"
                min="0"
                max={rangeMax}
                value={Math.min(rangeMax, Math.round(currentTime))}
                onChange={(event) => seek(Number(event.currentTarget.value))}
                disabled={!current}
                aria-label="Playback position"
              />
              <span>{formatClock(duration)}</span>
            </div>
            <div className="audio-toolbar">
              <div className="audio-actions">
                {onToggleSaved && (
                  <button
                    type="button"
                    className={savedIds?.has(track.itemId) ? 'secondary-action saved-action' : 'secondary-action'}
                    onClick={() => onToggleSaved(track.itemId)}
                    aria-label={savedIds?.has(track.itemId) ? 'Remove from liked songs' : 'Like this song'}
                  >
                    <HeartIcon filled={savedIds?.has(track.itemId)} />
                    <span>{savedIds?.has(track.itemId) ? 'Liked' : 'Like'}</span>
                  </button>
                )}
                <a className="secondary-action" href={track.downloadHref || track.streamHref} download>
                  <DownloadIcon />
                  <span>Download</span>
                </a>
                <button type="button" className="secondary-action" onClick={shareAudio}>
                  <ShareIcon />
                  <span>Share</span>
                </button>
                {track.albumHref && (
                  <a className="secondary-action" href={track.albumHref}>
                    <ListIcon />
                    <span>Album</span>
                  </a>
                )}
              </div>
              <div className="player-settings audio-watch-settings" aria-label="Audio settings">
                <div className="speed-controls" aria-label="Playback speed">
                  {[0.75, 1, 1.5, 2].map((speed) => (
                    <button
                      key={speed}
                      type="button"
                      className={player.speed === speed ? 'active' : ''}
                      onClick={() => setSpeed(speed)}
                    >
                      {speed === 0.75 ? '3/4x' : `${speed}x`}
                    </button>
                  ))}
                </div>
                <button type="button" className="secondary-action compact-action" onClick={cycleRepeatMode}>
                  <span>Repeat {player.repeatMode}</span>
                </button>
                <label className="volume-control audio-volume-control">
                  <button type="button" className="icon-button" onClick={toggleMute} aria-label={player.muted ? 'Unmute audio' : 'Mute audio'}>
                    <VolumeIcon />
                  </button>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.01"
                    value={player.muted ? 0 : player.volume}
                    onChange={(event) => setVolume(Number(event.currentTarget.value))}
                    aria-label="Audio volume"
                  />
                </label>
              </div>
            </div>
          </div>
          {player.nextTrack && (
            <div className="next-track-card inline-next-track-card">
              <p className="eyebrow">Up next - {player.nextCountdown}s</p>
              <strong>{player.nextTrack.title}</strong>
              <span>{[player.nextTrack.artist, player.nextTrack.albumTitle].filter(Boolean).join(' - ')}</span>
              <div>
                <button type="button" className="primary-action" onClick={confirmNext}>
                  <PlayIcon />
                  <span>Play now</span>
                </button>
                <button type="button" className="secondary-action" onClick={cancelNext}>Cancel</button>
              </div>
            </div>
          )}
          {player.queueToast && <p className="queue-toast" role="status">{player.queueToast}</p>}
          <LyricsPanel
            className="audio-watch-lyrics"
            track={track}
            currentTime={currentTime}
            seek={(seconds) => {
              if (!current) {
                playTrack(track, queue);
                window.setTimeout(() => seek(seconds), 0);
                return;
              }
              seek(seconds);
            }}
          />
          {player.error && current && <p className="player-error">{player.error}</p>}
          <a className="section-link classic-link" href={track.classicHref}>
            <span>Classic player</span>
            <ChevronRightIcon />
          </a>
        </div>
      </section>

      {queue.length > 1 && (
        <section className="track-list-section audio-queue-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Album</p>
              <h2>{track.albumTitle || 'Tracks'}</h2>
            </div>
            <button type="button" className="secondary-action" onClick={() => shuffleQueue(queue)}>
              <ShuffleIcon />
              <span>Shuffle</span>
            </button>
          </div>
          <div className="track-list">
            {queue.map((item, index) => {
              const active = player.track?.key === item.key;
              return (
                <a
                  key={item.key}
                  className={[
                    'track-row',
                    onAddToPlaylist ? 'has-playlist' : '',
                    active ? 'active' : '',
                  ].filter(Boolean).join(' ')}
                  href={item.appHref}
                >
                  <span className="track-number">{item.trackNumber || index + 1}</span>
                  <span className="track-title">
                    <strong>{item.title}</strong>
                    <span>{[item.artist, item.qualityLabel].filter(Boolean).join(' - ')}</span>
                  </span>
                  <span className="track-duration">{item.durationLabel}</span>
                  <button
                    type="button"
                    className="icon-button"
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      togglePlayback(item, queue);
                    }}
                    aria-label={active && player.playing ? `Pause ${item.title}` : `Play ${item.title}`}
                  >
                    {active && player.playing ? <PauseIcon /> : <PlayIcon />}
                  </button>
                  <button
                    type="button"
                    className="icon-button"
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      addToQueue(item, true);
                    }}
                    aria-label={`Play ${item.title} next`}
                  >
                    <ListIcon />
                  </button>
                  <button
                    type="button"
                    className="icon-button"
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      addToQueue(item, false);
                    }}
                    aria-label={`Add ${item.title} to queue`}
                  >
                    <span aria-hidden="true">+</span>
                  </button>
                  {onAddToPlaylist && (
                    <button
                      type="button"
                      className="icon-button"
                      onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        onAddToPlaylist(item);
                      }}
                      aria-label={`Add ${item.title} to playlist`}
                    >
                      <ListPlusIcon />
                    </button>
                  )}
                </a>
              );
            })}
          </div>
        </section>
      )}
    </main>
  );
}

function VideoWatchPage({ video }: { video: WatchVideo }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const hlsRef = useRef<{ destroy: () => void } | null>(null);
  const subInputRef = useRef<HTMLInputElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(video.duration || 0);
  const [sourceMode, setSourceMode] = useState<'direct' | 'hls'>('direct');
  const [audioIndex, setAudioIndex] = useState(0);
  const [subtitles, setSubtitles] = useState<SubtitleTrack[]>([]);
  const [activeSub, setActiveSub] = useState('');
  const [audioTracks, setAudioTracks] = useState<AudioTrackOption[]>([]);
  const [customSubtitles, setCustomSubtitles] = useState<SubtitleTrack[]>([]);
  const [subtitleStatus, setSubtitleStatus] = useState('');
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [brightness, setBrightness] = useState(1);
  const [error, setError] = useState(video.knownUnplayable ? 'This file is marked as difficult for browser playback.' : '');
  const [showNext, setShowNext] = useState(false);
  const [nextCountdown, setNextCountdown] = useState(5);
  const [toast, setToast] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [stillWatchingPrompt, setStillWatchingPrompt] = useState(false);
  const [stillWatchingActivity, setStillWatchingActivity] = useState(0);
  const hlsFailedRef = useRef(false);
  const controlsTimerRef = useRef<number | null>(null);
  const clickTimerRef = useRef<number | null>(null);
  const stillWatchingTimerRef = useRef<number | null>(null);
  const lastStillWatchingActivityRef = useRef<number | null>(null);
  const [autoplayNext, setAutoplayNext] = useState(() => {
    try {
      return localStorage.getItem('td:videoAutoplay') !== '0';
    } catch (_) {
      return true;
    }
  });
  const gestureRef = useRef({
    x: 0,
    y: 0,
    t: 0,
    moved: false,
    lastTap: 0,
    active: false,
    volumeStart: 1,
    brightnessStart: 1,
  });

  const hasHls = Boolean(video.hlsSrc);
  const sourceSrc = sourceMode === 'hls' && hasHls ? hlsUrl(video.hlsSrc, audioIndex) : video.directSrc;
  const allSubtitles = useMemo(() => [...subtitles, ...customSubtitles], [customSubtitles, subtitles]);
  const defaultAudioTrack = useMemo(() => audioTracks.find((track) => track.index === 0), [audioTracks]);
  const selectableAudioTracks = useMemo(() => audioTracks.filter((track) => track.index !== 0), [audioTracks]);
  const vlcHref = useMemo(
    () => buildVlcHref(video.absoluteStreamHref || video.streamHref || video.vlcHref.replace(/^vlc:\/\//, ''), video.vlcTrackingToken),
    [video.absoluteStreamHref, video.streamHref, video.vlcHref, video.vlcTrackingToken],
  );
  const rangeMax = Math.max(1, Math.round(duration || video.duration || 0));
  const hasIntro = video.introEnd > video.introStart;
  const hasRecap = video.recapEnd > video.recapStart;
  const showSkipIntro = hasIntro && currentTime >= video.introStart && currentTime < video.introEnd;
  const showSkipRecap = hasRecap && currentTime >= video.recapStart && currentTime < video.recapEnd;
  const activeSubtitle = allSubtitles.find((track) => track.id === activeSub);
  const chapters = useMemo(() => {
    const total = duration || video.duration || 0;
    return (video.chapters || [])
      .filter((chapter) => Number.isFinite(chapter.start) && chapter.start >= 0 && (!total || chapter.start < total))
      .slice()
      .sort((a, b) => a.start - b.start);
  }, [duration, video.chapters, video.duration]);
  const activeChapter = useMemo(() => {
    let active: (typeof chapters)[number] | null = null;
    for (const chapter of chapters) {
      if (chapter.start <= currentTime + 0.5) active = chapter;
      else break;
    }
    return active;
  }, [chapters, currentTime]);
  const displaySubtitle = video.subtitle && video.subtitle !== video.quality ? video.subtitle : '';
  const shellClass = [
    controlsVisible || menuOpen || stillWatchingPrompt ? 'video-shell controls-visible' : 'video-shell controls-hidden',
    video.knownUnplayable ? 'video-unplayable' : '',
  ].filter(Boolean).join(' ');

  const setVideoMediaSessionAction = useCallback((action: MediaSessionAction, handler: MediaSessionActionHandler | null) => {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.setActionHandler(action, handler);
    } catch (_) {
      // Browsers may expose only a subset of Media Session actions.
    }
  }, []);

  const setVideoMediaSessionPlaybackState = useCallback((isPlaying: boolean) => {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.playbackState = video.knownUnplayable ? 'none' : (isPlaying ? 'playing' : 'paused');
    } catch (_) {
      // Media Session support is uneven; ignore per-browser failures.
    }
  }, [video.knownUnplayable]);

  const setVideoMediaSessionPosition = useCallback((position = videoRef.current?.currentTime ?? 0) => {
    if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState || video.knownUnplayable) return;
    const total = videoRef.current?.duration || video.duration || 0;
    if (!Number.isFinite(total) || total <= 0) return;
    try {
      navigator.mediaSession.setPositionState({
        duration: total,
        playbackRate,
        position: Math.max(0, Math.min(total, position || 0)),
      });
    } catch (_) {
      // Media Session support is uneven; ignore per-browser failures.
    }
  }, [playbackRate, video.duration, video.knownUnplayable]);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(''), 900);
  }, []);

  const clearControlsTimer = useCallback(() => {
    if (controlsTimerRef.current !== null) {
      window.clearTimeout(controlsTimerRef.current);
      controlsTimerRef.current = null;
    }
  }, []);

  const clearStillWatchingTimer = useCallback(() => {
    if (stillWatchingTimerRef.current !== null) {
      window.clearTimeout(stillWatchingTimerRef.current);
      stillWatchingTimerRef.current = null;
    }
  }, []);

  const noteStillWatchingActivity = useCallback(() => {
    const now = Date.now();
    const last = lastStillWatchingActivityRef.current;
    if (last !== null && now - last < 1000) return;
    lastStillWatchingActivityRef.current = now;
    setStillWatchingActivity((activity) => activity + 1);
  }, []);

  const scheduleControlsHide = useCallback(() => {
    clearControlsTimer();
    if (!playing || menuOpen || error || showNext || stillWatchingPrompt) return;
    controlsTimerRef.current = window.setTimeout(() => {
      setControlsVisible(false);
      controlsTimerRef.current = null;
    }, 2200);
  }, [clearControlsTimer, error, menuOpen, playing, showNext, stillWatchingPrompt]);

  const revealVideoControls = useCallback(() => {
    if (playing) noteStillWatchingActivity();
    setControlsVisible(true);
    scheduleControlsHide();
  }, [noteStillWatchingActivity, playing, scheduleControlsHide]);

  const changeVolume = useCallback((nextVolume: number) => {
    noteStillWatchingActivity();
    const next = Math.max(0, Math.min(1, nextVolume));
    setVolume(next);
    setMuted(next <= 0);
  }, [noteStillWatchingActivity]);

  useEffect(() => {
    hlsFailedRef.current = false;
  }, [video.hlsSrc, video.key]);

  useEffect(() => {
    if (!hasHls && sourceMode === 'hls') setSourceMode('direct');
  }, [hasHls, sourceMode]);

  useEffect(() => {
    const controller = new AbortController();
    fetchSubtitles(video.subtitleBase, controller.signal).then(setSubtitles).catch(() => setSubtitles([]));
    if (hasHls) {
      fetchAudioTracks(video.audioTrackBase, controller.signal).then(setAudioTracks).catch(() => setAudioTracks([]));
    } else {
      setAudioTracks([]);
    }
    return () => controller.abort();
  }, [hasHls, video.audioTrackBase, video.subtitleBase]);

  useEffect(() => {
    const restored = restoreCachedSubtitle(video.resumeKey);
    if (!restored) return undefined;
    setCustomSubtitles([restored]);
    setSubtitleStatus(`Restored "${restored.label}" subtitles.`);
    return () => revokeSubtitleTrack(restored);
  }, [video.resumeKey]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return undefined;
    hlsRef.current?.destroy();
    hlsRef.current = null;
    if (video.knownUnplayable) {
      el.removeAttribute('src');
      return undefined;
    }
    const savedTime = el.currentTime || 0;
    let cancelled = false;
    if (sourceMode === 'hls' && hasHls) {
      attachHls(el, sourceSrc, video.directSrc, () => {
        if (!cancelled) {
          hlsFailedRef.current = true;
          setSourceMode('direct');
          setError('');
          showToast('HLS failed. Using direct stream.');
        }
      }).then((instance) => {
        if (cancelled) {
          instance?.destroy();
          return;
        }
        hlsRef.current = instance;
        if (savedTime > 0 && Number.isFinite(savedTime)) {
          try { el.currentTime = savedTime; } catch (_) { /* ignore invalid seek */ }
        }
      });
    } else if (video.directSrc) {
      el.src = video.directSrc;
      if (savedTime > 0 && Number.isFinite(savedTime)) {
        try { el.currentTime = savedTime; } catch (_) { /* ignore invalid seek */ }
      }
    }
    return () => {
      cancelled = true;
      hlsRef.current?.destroy();
      hlsRef.current = null;
    };
  }, [hasHls, showToast, sourceMode, sourceSrc, video.directSrc, video.knownUnplayable]);

  useEffect(() => {
    try {
      localStorage.setItem('td:videoAutoplay', autoplayNext ? '1' : '0');
    } catch (_) {
      // Local preference only.
    }
  }, [autoplayNext]);

  useEffect(() => {
    const onFullscreenChange = () => setFullscreen(document.fullscreenElement === shellRef.current);
    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange);
  }, []);

  useEffect(() => {
    if (!playing || menuOpen || error || showNext || stillWatchingPrompt) {
      clearControlsTimer();
      setControlsVisible(true);
      return undefined;
    }
    scheduleControlsHide();
    return clearControlsTimer;
  }, [clearControlsTimer, error, menuOpen, playing, scheduleControlsHide, showNext, stillWatchingPrompt]);

  useEffect(() => {
    clearStillWatchingTimer();
    if (!playing || error || showNext || stillWatchingPrompt || video.knownUnplayable) {
      return undefined;
    }
    stillWatchingTimerRef.current = window.setTimeout(() => {
      const el = videoRef.current;
      if (!el || el.ended || video.knownUnplayable) return;
      el.pause();
      setPlaying(false);
      setMenuOpen(false);
      setStillWatchingPrompt(true);
      setControlsVisible(true);
      setVideoMediaSessionPlaybackState(false);
      stillWatchingTimerRef.current = null;
    }, STILL_WATCHING_TIMEOUT_MS);
    return clearStillWatchingTimer;
  }, [
    clearStillWatchingTimer,
    error,
    menuOpen,
    playing,
    setVideoMediaSessionPlaybackState,
    showNext,
    stillWatchingActivity,
    stillWatchingPrompt,
    video.knownUnplayable,
  ]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    el.volume = volume;
    el.muted = muted;
  }, [muted, sourceSrc, volume]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    el.playbackRate = playbackRate;
  }, [playbackRate, sourceSrc]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    // Match by TextTrack.id so HLS.js-injected tracks at unpredictable
    // indices don't interfere. Guard activeSub so empty-id HLS tracks
    // are never accidentally shown when no sub is selected.
    const applyMode = () => {
      Array.from(el.textTracks || []).forEach((track) => {
        track.mode = (activeSub && track.id === activeSub) ? 'showing' : 'disabled';
      });
    };
    applyMode();
    // Re-apply when HLS.js or the browser adds/recreates tracks after a
    // source change — without this, tracks that appear after the initial
    // applyMode call stay in their default 'disabled' mode.
    const textTracks = el.textTracks;
    if (
      typeof textTracks?.addEventListener !== 'function'
      || typeof textTracks?.removeEventListener !== 'function'
    ) {
      return;
    }
    textTracks.addEventListener('addtrack', applyMode);
    return () => textTracks.removeEventListener('addtrack', applyMode);
  }, [activeSub, allSubtitles, sourceSrc]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    let restored = false;
    let lastSave = 0;
    let lastServerSave = 0;
    let completed = false;
    const loadResume = () => {
      if (restored || !el.duration) return;
      restored = true;
      try {
        const data = JSON.parse(localStorage.getItem('td:cw') || '{}') || {};
        const entry = data[video.resumeKey];
        if (entry?.pos > 0 && entry.pos < el.duration * 0.95) {
          el.currentTime = entry.pos;
        }
      } catch (_) {
        // Local resume state is best-effort only.
      }
    };
    const saveResume = (force = false) => {
      if (!el.duration || !Number.isFinite(el.duration)) return;
      const now = Date.now();
      if (!force && now - lastSave < 5000) return;
      lastSave = now;
      const pct = el.currentTime / el.duration;
      try {
        const data = JSON.parse(localStorage.getItem('td:cw') || '{}') || {};
        if (pct >= 0.95 || pct < 0.02) {
          delete data[video.resumeKey];
        } else {
          data[video.resumeKey] = {
            pos: el.currentTime,
            dur: el.duration,
            t: now,
            title: video.title,
          };
        }
        localStorage.setItem('td:cw', JSON.stringify(data));
      } catch (_) {
        // Ignore quota/private-mode failures.
      }
      if (pct >= 0.95) {
        if (!completed) {
          completed = true;
          markLocallyWatched(video.resumeKey);
          void deleteContinueEntry(video.resumeKey).catch(() => undefined);
          void recordWatchHistory(video.resumeKey, video.title).catch(() => undefined);
        }
      } else if (pct >= 0.02) {
        const shouldSync = force || now - lastServerSave > 30000;
        if (shouldSync) {
          lastServerSave = now;
          void saveContinueEntry(video.resumeKey, {
            pos: Math.floor(el.currentTime),
            dur: Math.floor(el.duration),
            t: now,
            title: video.title,
          }).catch(() => undefined);
        }
      } else if (force) {
        void deleteContinueEntry(video.resumeKey).catch(() => undefined);
      }
    };
    const onTime = () => {
      setCurrentTime(el.currentTime || 0);
      setDuration(el.duration || video.duration || 0);
      setVideoMediaSessionPosition(el.currentTime || 0);
      saveResume();
    };
    const onLoaded = () => {
      setDuration(el.duration || video.duration || 0);
      setVideoMediaSessionPosition(el.currentTime || 0);
      loadResume();
    };
    const onEnded = () => {
      saveResume(true);
      setPlaying(false);
      setVideoMediaSessionPlaybackState(false);
      if (video.nextEpisode) setShowNext(true);
    };
    const onError = () => {
      if (sourceMode === 'direct' && hasHls && !hlsFailedRef.current) {
        setSourceMode('hls');
        setError('');
      } else {
        setPlaying(false);
        setVideoMediaSessionPlaybackState(false);
        setError('Browser playback failed. The classic player and VLC links are available.');
      }
    };
    const onPlay = () => {
      setPlaying(true);
      setVideoMediaSessionPlaybackState(true);
      setVideoMediaSessionPosition(el.currentTime || 0);
    };
    const onPause = () => {
      setPlaying(false);
      setVideoMediaSessionPlaybackState(false);
      setVideoMediaSessionPosition(el.currentTime || 0);
    };
    el.addEventListener('timeupdate', onTime);
    el.addEventListener('loadedmetadata', onLoaded);
    el.addEventListener('durationchange', onLoaded);
    el.addEventListener('play', onPlay);
    el.addEventListener('pause', onPause);
    el.addEventListener('ended', onEnded);
    el.addEventListener('error', onError);
    const onBeforeUnload = () => saveResume(true);
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => {
      el.removeEventListener('timeupdate', onTime);
      el.removeEventListener('loadedmetadata', onLoaded);
      el.removeEventListener('durationchange', onLoaded);
      el.removeEventListener('play', onPlay);
      el.removeEventListener('pause', onPause);
      el.removeEventListener('ended', onEnded);
      el.removeEventListener('error', onError);
      window.removeEventListener('beforeunload', onBeforeUnload);
      saveResume(true);
    };
  }, [hasHls, setVideoMediaSessionPlaybackState, setVideoMediaSessionPosition, sourceMode, sourceSrc, video]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el || video.knownUnplayable) return undefined;
    let shown = false;
    let everDecoded = false;
    let stallTimer: number | null = null;
    const clearStallTimer = () => {
      if (stallTimer !== null) {
        window.clearInterval(stallTimer);
        stallTimer = null;
      }
    };
    const markDecoded = () => {
      if (el.videoWidth > 0) everDecoded = true;
    };
    const showDecodeError = () => {
      if (shown || everDecoded) return;
      shown = true;
      setPlaying(false);
      setVideoMediaSessionPlaybackState(false);
      setError('Browser playback started but no video frames decoded. The classic player and VLC links are available.');
    };
    const onPlaying = () => {
      clearStallTimer();
      let stallTicks = 0;
      let ticks = 0;
      stallTimer = window.setInterval(() => {
        if (shown || everDecoded) {
          clearStallTimer();
          return;
        }
        if (!el.paused && el.currentTime > 1 && el.videoWidth === 0) {
          stallTicks += 1;
          if (stallTicks >= 4) showDecodeError();
        } else {
          stallTicks = 0;
        }
        ticks += 1;
        if (ticks > 15) clearStallTimer();
      }, 1000);
    };
    ['loadedmetadata', 'loadeddata', 'playing', 'timeupdate'].forEach((eventName) => {
      el.addEventListener(eventName, markDecoded);
    });
    el.addEventListener('playing', onPlaying);
    return () => {
      clearStallTimer();
      ['loadedmetadata', 'loadeddata', 'playing', 'timeupdate'].forEach((eventName) => {
        el.removeEventListener(eventName, markDecoded);
      });
      el.removeEventListener('playing', onPlaying);
    };
  }, [setVideoMediaSessionPlaybackState, sourceSrc, video.knownUnplayable]);

  const playNextEpisode = useCallback(() => {
    const href = video.nextEpisode?.playHref || video.nextEpisode?.classicHref;
    if (href) window.location.href = href;
  }, [video.nextEpisode]);

  useEffect(() => {
    if (!showNext || !video.nextEpisode || !autoplayNext) return;
    setNextCountdown(NEXT_EPISODE_COUNTDOWN_SECONDS);
    const interval = window.setInterval(() => {
      setNextCountdown((current) => {
        if (current <= 1) {
          window.clearInterval(interval);
          playNextEpisode();
          return 0;
        }
        return current - 1;
      });
    }, 1000);
    return () => window.clearInterval(interval);
  }, [autoplayNext, playNextEpisode, showNext, video.nextEpisode]);

  const playVideo = useCallback(() => {
    const el = videoRef.current;
    if (!el || video.knownUnplayable) return;
    setStillWatchingPrompt(false);
    noteStillWatchingActivity();
    setError('');
    setVideoMediaSessionPlaybackState(true);
    const promise = el.play();
    if (promise) {
      promise.catch(() => {
        setPlaying(false);
        setVideoMediaSessionPlaybackState(false);
        setError('Tap play again or open the classic player.');
      });
    }
  }, [noteStillWatchingActivity, setVideoMediaSessionPlaybackState, video.knownUnplayable]);

  const pauseVideo = useCallback(() => {
    const el = videoRef.current;
    if (!el) return;
    clearStillWatchingTimer();
    el.pause();
    setPlaying(false);
    setVideoMediaSessionPlaybackState(false);
  }, [clearStillWatchingTimer, setVideoMediaSessionPlaybackState]);

  const toggleVideo = useCallback(() => {
    const el = videoRef.current;
    if (!el || video.knownUnplayable) return;
    if (el.paused) playVideo();
    else pauseVideo();
  }, [pauseVideo, playVideo, video.knownUnplayable]);

  const seekVideo = useCallback((seconds: number) => {
    const el = videoRef.current;
    if (!el) return;
    noteStillWatchingActivity();
    const next = Math.max(0, Math.min(seconds, duration || video.duration || seconds));
    el.currentTime = next;
    setCurrentTime(next);
    setVideoMediaSessionPosition(next);
  }, [duration, noteStillWatchingActivity, setVideoMediaSessionPosition, video.duration]);

  const seekVideoBy = useCallback((delta: number) => {
    const base = videoRef.current?.currentTime ?? currentTime;
    seekVideo(base + delta);
    showToast(delta > 0 ? '+10s' : '-10s');
  }, [currentTime, seekVideo, showToast]);

  const replayCurrentVideo = useCallback(() => {
    setShowNext(false);
    seekVideo(0);
    playVideo();
  }, [playVideo, seekVideo]);

  const togglePip = useCallback(() => {
    const el = videoRef.current as (HTMLVideoElement & {
      webkitSupportsPresentationMode?: (mode: string) => boolean;
      webkitSetPresentationMode?: (mode: string) => void;
      webkitPresentationMode?: string;
    }) | null;
    if (!el) return;
    if (document.pictureInPictureElement) {
      document.exitPictureInPicture().catch(() => undefined);
    } else if (document.pictureInPictureEnabled && el.requestPictureInPicture) {
      el.requestPictureInPicture().catch(() => undefined);
    } else if (el.webkitSupportsPresentationMode?.('picture-in-picture') && el.webkitSetPresentationMode) {
      el.webkitSetPresentationMode(el.webkitPresentationMode === 'picture-in-picture' ? 'inline' : 'picture-in-picture');
    }
  }, []);

  const toggleFullscreen = useCallback(() => {
    const target = shellRef.current;
    const el = videoRef.current as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null;
    const enterNativeFullscreen = () => {
      try { el?.webkitEnterFullscreen?.(); } catch (_) { /* Best-effort Safari fallback. */ }
    };
    if (!target) return;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
    else if (target.requestFullscreen) target.requestFullscreen().catch(enterNativeFullscreen);
    else enterNativeFullscreen();
  }, []);

  const toggleCaptions = useCallback(() => {
    if (!allSubtitles.length) {
      showToast('Captions unavailable');
      return;
    }
    setActiveSub((current) => current ? '' : allSubtitles[0].id);
  }, [allSubtitles, showToast]);

  const switchToAudioTrack = useCallback((index: number) => {
    if (!hasHls) return;
    hlsFailedRef.current = false;
    setAudioIndex(index);
    setSourceMode('hls');
  }, [hasHls]);

  const toggleSourceMode = useCallback(() => {
    if (!hasHls) return;
    if (sourceMode === 'direct') hlsFailedRef.current = false;
    setSourceMode((current) => current === 'direct' ? 'hls' : 'direct');
  }, [hasHls, sourceMode]);

  const shareVideo = useCallback(async () => {
    const data = { title: video.title, url: window.location.href };
    if (navigator.share) {
      try { await navigator.share(data); } catch (_) { return; }
    } else if (navigator.clipboard) {
      await navigator.clipboard.writeText(window.location.href).catch(() => undefined);
      showToast('Link copied');
    }
  }, [showToast, video.title]);

  const loadSubtitleFile = useCallback(async (file: File | null | undefined) => {
    if (!file) return;
    try {
      const track = await subtitleFileToTrack(file, video.resumeKey);
      setCustomSubtitles((current) => {
        current.forEach(revokeSubtitleTrack);
        return [track];
      });
      setActiveSub(track.id);
      setSubtitleStatus(`Loaded "${file.name}" as subtitles.`);
    } catch (err) {
      setSubtitleStatus(err instanceof Error ? err.message : 'Could not load subtitles.');
    }
  }, [video.resumeKey]);

  const openAirPlay = useCallback(() => {
    const el = videoRef.current as (HTMLVideoElement & { webkitShowPlaybackTargetPicker?: () => void }) | null;
    if (el?.webkitShowPlaybackTargetPicker) {
      el.webkitShowPlaybackTargetPicker();
      return;
    }
    showToast('AirPlay unavailable');
  }, [showToast]);

  const keepWatching = useCallback(() => {
    setStillWatchingPrompt(false);
    setControlsVisible(true);
    noteStillWatchingActivity();
    playVideo();
  }, [noteStillWatchingActivity, playVideo]);

  const stayPaused = useCallback(() => {
    setStillWatchingPrompt(false);
    setControlsVisible(true);
  }, []);

  useEffect(() => {
    if (!('mediaSession' in navigator) || video.knownUnplayable) return undefined;
    try {
      if ('MediaMetadata' in window) {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: video.title || '',
          artist: video.mediaKind || '',
          album: video.subtitle || video.metadata.title || '',
          artwork: (video.posterUrl || video.thumbUrl)
            ? [{ src: video.posterUrl || video.thumbUrl, sizes: '512x512', type: 'image/jpeg' }]
            : [],
        });
      }
    } catch (_) {
      // Metadata is optional; controls should still work without it.
    }

    setVideoMediaSessionAction('play', () => playVideo());
    setVideoMediaSessionAction('pause', () => pauseVideo());
    setVideoMediaSessionAction('seekto', (details) => {
      if (Number.isFinite(details.seekTime)) seekVideo(details.seekTime || 0);
    });
    setVideoMediaSessionAction('seekbackward', (details) => seekVideoBy(-(details.seekOffset || 10)));
    setVideoMediaSessionAction('seekforward', (details) => seekVideoBy(details.seekOffset || 10));
    setVideoMediaSessionAction('previoustrack', () => seekVideo(0));
    setVideoMediaSessionAction('nexttrack', video.nextEpisode ? playNextEpisode : null);
    setVideoMediaSessionAction('stop', () => {
      pauseVideo();
      seekVideo(0);
    });
    setVideoMediaSessionPlaybackState(false);
    setVideoMediaSessionPosition();

    return () => {
      setVideoMediaSessionAction('play', null);
      setVideoMediaSessionAction('pause', null);
      setVideoMediaSessionAction('seekto', null);
      setVideoMediaSessionAction('seekbackward', null);
      setVideoMediaSessionAction('seekforward', null);
      setVideoMediaSessionAction('previoustrack', null);
      setVideoMediaSessionAction('nexttrack', null);
      setVideoMediaSessionAction('stop', null);
      try {
        navigator.mediaSession.playbackState = 'none';
        navigator.mediaSession.metadata = null;
      } catch (_) {
        // Ignore uneven browser Media Session support.
      }
      window.dispatchEvent(new Event(RESTORE_AUDIO_MEDIA_SESSION_EVENT));
    };
  }, [
    pauseVideo,
    playNextEpisode,
    playVideo,
    seekVideo,
    seekVideoBy,
    setVideoMediaSessionAction,
    setVideoMediaSessionPlaybackState,
    setVideoMediaSessionPosition,
    video.knownUnplayable,
    video.mediaKind,
    video.metadata.title,
    video.nextEpisode,
    video.posterUrl,
    video.subtitle,
    video.thumbUrl,
    video.title,
  ]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'MediaPlayPause') {
        event.preventDefault();
        toggleVideo();
        return;
      }
      if (event.key === 'MediaPlay') {
        event.preventDefault();
        playVideo();
        return;
      }
      if (event.key === 'MediaPause' || event.key === 'MediaStop') {
        event.preventDefault();
        pauseVideo();
        return;
      }
      if (event.key === 'MediaTrackNext') {
        event.preventDefault();
        playNextEpisode();
        return;
      }
      if (event.key === 'MediaTrackPrevious') {
        event.preventDefault();
        seekVideo(0);
        return;
      }

      const target = event.target as HTMLElement | null;
      const tagName = target?.tagName;
      if (target?.isContentEditable || tagName === 'INPUT' || tagName === 'SELECT' || tagName === 'TEXTAREA' || tagName === 'BUTTON') {
        return;
      }
      noteStillWatchingActivity();
      if (event.key === ' ' || event.key.toLowerCase() === 'k') {
        event.preventDefault();
        toggleVideo();
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        seekVideoBy(-10);
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        seekVideoBy(10);
      } else if (event.key.toLowerCase() === 'm') {
        event.preventDefault();
        setMuted((isMuted) => !isMuted);
      } else if (event.key.toLowerCase() === 'f') {
        event.preventDefault();
        toggleFullscreen();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [noteStillWatchingActivity, pauseVideo, playNextEpisode, playVideo, seekVideo, seekVideoBy, toggleFullscreen, toggleVideo]);

  useEffect(() => () => {
    if (clickTimerRef.current !== null) window.clearTimeout(clickTimerRef.current);
  }, []);

  const onTouchStart = (event: TouchEvent<HTMLDivElement>) => {
    revealVideoControls();
    if (isVideoChromeTarget(event.target)) {
      gestureRef.current = { ...gestureRef.current, active: false };
      return;
    }
    const touch = event.touches[0];
    const now = Date.now();
    const last = gestureRef.current.lastTap;
    const rect = event.currentTarget.getBoundingClientRect();
    if (now - last < 280) {
      const delta = touch.clientX < rect.left + rect.width / 2 ? -10 : 10;
      seekVideo((videoRef.current?.currentTime || 0) + delta);
      showToast(delta > 0 ? '+10s' : '-10s');
    }
    gestureRef.current = {
      x: touch.clientX,
      y: touch.clientY,
      t: now,
      moved: false,
      lastTap: now,
      active: true,
      volumeStart: volume,
      brightnessStart: brightness,
    };
  };

  const onTouchMove = (event: TouchEvent<HTMLDivElement>) => {
    const start = gestureRef.current;
    if (!start.active) return;
    const touch = event.touches[0];
    const dy = start.y - touch.clientY;
    const rect = event.currentTarget.getBoundingClientRect();
    if (Math.abs(dy) > 36) {
      start.moved = true;
      if (start.x > rect.left + rect.width / 2) {
        const next = Math.max(0, Math.min(1, start.volumeStart + dy / 500));
        changeVolume(next);
        showToast(`Volume ${Math.round(next * 100)}%`);
      } else {
        const next = Math.max(0.45, Math.min(1, start.brightnessStart + dy / 650));
        setBrightness(next);
        showToast(`Brightness ${Math.round(next * 100)}%`);
      }
    }
  };

  const onTouchEnd = (event: TouchEvent<HTMLDivElement>) => {
    const start = gestureRef.current;
    if (!start.active) return;
    start.active = false;
    if (start.moved) return;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - start.x;
    if (Math.abs(dx) > 60) {
      const delta = Math.round(dx / 6);
      seekVideo((videoRef.current?.currentTime || 0) + delta);
      showToast(delta > 0 ? `+${delta}s` : `${delta}s`);
    }
  };

  const onShellClick = (event: MouseEvent<HTMLDivElement>) => {
    revealVideoControls();
    if (event.defaultPrevented || event.detail > 1 || isVideoChromeTarget(event.target)) return;
    if (clickTimerRef.current !== null) window.clearTimeout(clickTimerRef.current);
    clickTimerRef.current = window.setTimeout(() => {
      clickTimerRef.current = null;
      toggleVideo();
    }, 280);
  };

  const onShellDoubleClick = (event: MouseEvent<HTMLDivElement>) => {
    revealVideoControls();
    if (event.defaultPrevented || isVideoChromeTarget(event.target)) return;
    event.preventDefault();
    if (clickTimerRef.current !== null) {
      window.clearTimeout(clickTimerRef.current);
      clickTimerRef.current = null;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    const delta = event.clientX < rect.left + rect.width / 2 ? -10 : 10;
    seekVideoBy(delta);
  };

  const onDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
  };

  const onDropSubtitle = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    void loadSubtitleFile(event.dataTransfer.files?.[0]);
  };

  return (
    <main className="video-main">
      <section className="video-titlebar">
        <div>
          <p className="eyebrow">{video.quality || 'Video'}</p>
          <h1 dir="auto">{video.title}</h1>
          {displaySubtitle && <p>{displaySubtitle}</p>}
          <RatingControls messageId={video.messageId || video.itemId} />
        </div>
        <a className="section-link" href={video.classicHref}>
          <span>Classic player</span>
          <ChevronRightIcon />
        </a>
      </section>

      <section
        className={shellClass}
        ref={shellRef}
        onPointerMove={revealVideoControls}
        onFocusCapture={revealVideoControls}
        onClick={onShellClick}
        onDoubleClick={onShellDoubleClick}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onDragOver={onDragOver}
        onDrop={onDropSubtitle}
      >
        <input
          ref={subInputRef}
          type="file"
          accept=".srt,.vtt,text/vtt"
          hidden
          onChange={(event) => {
            void loadSubtitleFile(event.currentTarget.files?.[0]);
            event.currentTarget.value = '';
          }}
        />
        <video
          ref={videoRef}
          poster={video.backdropUrl || video.posterUrl}
          playsInline
          preload="metadata"
        >
          {allSubtitles.map((track, index) => (
            <track
              key={track.id}
              id={track.id}
              kind="subtitles"
              src={track.url}
              srcLang={track.language || 'und'}
              label={track.label || track.language || `Subtitle ${index + 1}`}
            />
          ))}
        </video>
        <div className="video-brightness-overlay" style={{ opacity: Math.max(0, 1 - brightness) }} />

        <div className="video-topbar">
          <div className="video-topbar-copy">
            <strong dir="auto">{video.title}</strong>
            {displaySubtitle && <span>{displaySubtitle}</span>}
          </div>
          <div className="video-topbar-badges" aria-label="Playback state">
            <span>{sourceMode === 'hls' ? 'HLS' : 'Direct'}</span>
            {video.quality && <span>{video.quality}</span>}
            {activeSubtitle && <span>{activeSubtitle.label || activeSubtitle.language || 'Captions'}</span>}
            {activeChapter && <span>{activeChapter.title}</span>}
          </div>
        </div>

        {toast && <div className="gesture-toast">{toast}</div>}

        {stillWatchingPrompt && (
          <div className="still-watching-overlay" role="dialog" aria-modal="true" aria-labelledby="still-watching-title">
            <div className="still-watching-panel">
              <p className="eyebrow">Playback paused</p>
              <h2 id="still-watching-title">Still watching?</h2>
              <p>We paused the stream to save bandwidth.</p>
              <div className="still-watching-actions">
                <button type="button" className="primary-action" onClick={keepWatching}>
                  <PlayIcon />
                  <span>Keep watching</span>
                </button>
                <button type="button" className="secondary-action" onClick={stayPaused}>
                  <PauseIcon />
                  <span>Stay paused</span>
                </button>
              </div>
            </div>
          </div>
        )}

        {(error || video.knownUnplayable) && (
          <div className="video-overlay-message">
            <FilmIcon />
            <strong>This video needs another player</strong>
            <span>{error || 'Open it in VLC or the classic player.'}</span>
            <div>
              <a className="primary-action" href={video.classicHref}>Classic player</a>
              <a className="secondary-action" href={vlcHref}>VLC</a>
            </div>
          </div>
        )}

        {showSkipIntro && (
          <button type="button" className="skip-intro" onClick={() => seekVideo(video.introEnd)}>
            Skip intro
            <SkipForwardIcon />
          </button>
        )}

        {showSkipRecap && (
          <button type="button" className="skip-intro skip-recap" onClick={() => seekVideo(video.recapEnd)}>
            Skip recap
            <SkipForwardIcon />
          </button>
        )}

        {showNext && video.nextEpisode && (
          <NextEpisodePanel
            nextEpisode={video.nextEpisode}
            autoplay={autoplayNext}
            countdown={nextCountdown}
            playHref={video.nextEpisode.playHref || video.nextEpisode.classicHref}
            onReplay={replayCurrentVideo}
            onDismiss={() => setShowNext(false)}
            onToggleAutoplay={() => setAutoplayNext((enabled) => !enabled)}
          />
        )}

        <div className="video-controls">
          <button type="button" className="player-play video-play" onClick={toggleVideo} aria-label={playing ? 'Pause' : 'Play'}>
            {playing ? <PauseIcon /> : <PlayIcon />}
          </button>
          <button type="button" className="icon-button video-step" onClick={() => seekVideoBy(-10)} aria-label="Rewind 10 seconds">
            <span aria-hidden="true">-10</span>
          </button>
          <div className="video-time">
            <span>{formatClock(currentTime)}</span>
            <div className="video-scrub-wrap">
              <input
                type="range"
                min="0"
                max={rangeMax}
                value={Math.min(rangeMax, Math.round(currentTime))}
                onChange={(event) => seekVideo(Number(event.currentTarget.value))}
                aria-label="Playback position"
              />
              {chapters.length > 0 && (
                <div className="video-chapter-markers" aria-hidden="true">
                  {chapters.map((chapter) => (
                    <span
                      key={`${chapter.start}:${chapter.title}`}
                      style={{ left: `${Math.max(0, Math.min(100, (chapter.start / rangeMax) * 100))}%` }}
                    />
                  ))}
                </div>
              )}
            </div>
            <span>{formatClock(duration)}</span>
          </div>
          <button type="button" className="icon-button video-step" onClick={() => seekVideoBy(10)} aria-label="Forward 10 seconds">
            <span aria-hidden="true">+10</span>
          </button>
          <button type="button" className="icon-button" onClick={() => setMuted((isMuted) => !isMuted)} aria-label={muted ? 'Unmute' : 'Mute'}>
            <VolumeIcon />
          </button>
          <button
            type="button"
            className={activeSub ? 'icon-button active' : 'icon-button'}
            onClick={toggleCaptions}
            disabled={!allSubtitles.length}
            aria-label={activeSub ? 'Turn captions off' : 'Turn captions on'}
          >
            <CaptionsIcon />
          </button>
          <button type="button" className="icon-button video-pip-control" onClick={togglePip} aria-label="Picture in picture">
            <PictureInPictureIcon />
          </button>
          <button type="button" className="icon-button" onClick={toggleFullscreen} aria-label={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}>
            <MaximizeIcon />
          </button>
          <button type="button" className="icon-button" onClick={() => setMenuOpen((open) => !open)} aria-label="More video options" aria-expanded={menuOpen}>
            <MoreVerticalIcon />
          </button>
        </div>

        {menuOpen && (
          <div className="video-options-menu" role="menu" aria-label="Video options">
            <button
              type="button"
              className="video-menu-row"
              role="menuitemcheckbox"
              aria-checked={autoplayNext}
              onClick={() => setAutoplayNext((enabled) => !enabled)}
            >
              <span>Autoplay next</span>
              <strong>{autoplayNext ? 'On' : 'Off'}</strong>
            </button>
            <label className="video-menu-row">
              <span>Captions</span>
              <select value={activeSub} onChange={(event) => setActiveSub(event.currentTarget.value)} disabled={!allSubtitles.length} aria-label="Captions">
                <option value="">Off</option>
                {allSubtitles.map((track) => (
                  <option key={track.id} value={track.id}>{track.label || track.language || track.id}</option>
                ))}
              </select>
            </label>
            <label className="video-menu-row video-menu-range">
              <span>Volume</span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={muted ? 0 : volume}
                onChange={(event) => changeVolume(Number(event.currentTarget.value))}
                aria-label="Video volume"
              />
            </label>
            <button type="button" className="video-menu-row" role="menuitem" onClick={() => subInputRef.current?.click()}>
              <span>Load subtitles</span>
              <strong>SRT/VTT</strong>
            </button>
            {hasHls && (
              <label className="video-menu-row">
                <span>Audio</span>
                <select
                  value={audioIndex}
                  onChange={(event) => switchToAudioTrack(Number(event.currentTarget.value))}
                  disabled={!selectableAudioTracks.length}
                  aria-label="Audio track"
                >
                  <option value={0}>{defaultAudioTrack?.label || defaultAudioTrack?.language || 'Default'}</option>
                  {selectableAudioTracks.map((track) => (
                    <option key={track.index} value={track.index}>{track.label || track.language || `Track ${track.index + 1}`}</option>
                  ))}
                </select>
              </label>
            )}
            {hasHls && (
              <button type="button" className="video-menu-row" role="menuitem" onClick={toggleSourceMode}>
                <span>Source</span>
                <strong>{sourceMode === 'direct' ? 'Direct' : 'HLS'}</strong>
              </button>
            )}
            <label className="video-menu-row">
              <span>Speed</span>
              <select value={playbackRate} onChange={(event) => setPlaybackRate(Number(event.currentTarget.value))} aria-label="Playback speed">
                {[0.75, 1, 1.25, 1.5, 2].map((rate) => (
                  <option key={rate} value={rate}>{rate}x</option>
                ))}
              </select>
            </label>
            {video.qualityVariants.length > 0 && (
              <>
                <div className="video-menu-label" role="presentation">Quality</div>
                <a className="video-menu-row" role="menuitem" href={video.appHref}>
                  <span>{video.quality || 'Current'}</span>
                  <strong>Current</strong>
                </a>
                {video.qualityVariants.map((variant) => (
                  <a key={variant.key} className="video-menu-row" role="menuitem" href={variant.playHref}>
                    <span>{variant.quality || variant.label || variant.title}</span>
                    <strong>Open</strong>
                  </a>
                ))}
              </>
            )}
            <button type="button" className="video-menu-row" role="menuitem" onClick={openAirPlay}>
              <span>AirPlay</span>
              <strong>Open</strong>
            </button>
            {subtitleStatus && <div className="video-menu-status" role="status">{subtitleStatus}</div>}
            <a className="video-menu-row" role="menuitem" href={video.classicHref}>
              <span>Classic player</span>
              <strong>Open</strong>
            </a>
            <a className="video-menu-row" role="menuitem" href={vlcHref}>
              <span>VLC</span>
              <strong>Open</strong>
            </a>
            <a className="video-menu-row" role="menuitem" href={video.downloadHref} download>
              <span>Download</span>
              <strong>File</strong>
            </a>
            <button type="button" className="video-menu-row" role="menuitem" onClick={shareVideo}>
              <span>Share</span>
              <strong>Link</strong>
            </button>
          </div>
        )}
      </section>

      <VideoInfoSection video={video} />

      {chapters.length > 0 && (
        <section className="chapter-section" aria-label="Video chapters">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Scene access</p>
              <h2>Chapters</h2>
            </div>
          </div>
          <div className="chapter-list">
            {chapters.map((chapter, index) => {
              const active = activeChapter?.start === chapter.start;
              return (
                <button
                  key={`${chapter.start}:${chapter.title}`}
                  type="button"
                  className={active ? 'chapter-button active' : 'chapter-button'}
                  onClick={() => seekVideo(chapter.start)}
                >
                  <span>{formatClock(chapter.start)}</span>
                  <strong>{chapter.title || `Chapter ${index + 1}`}</strong>
                </button>
              );
            })}
          </div>
        </section>
      )}

      {video.qualityVariants.length > 0 && (
        <section className="quality-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Versions</p>
              <h2>Quality variants</h2>
            </div>
          </div>
          <div className="playback-options">
            <a className="playback-option active" href={video.appHref} aria-current="true" aria-label={`Current ${video.quality || 'version'}`}>
              <strong>{video.quality || 'Current'}</strong>
              <span>Current version</span>
              <small>{video.durationLabel || 'Now playing'}</small>
              <em>Playing</em>
            </a>
            {video.qualityVariants.map((variant) => (
              <a
                key={variant.key}
                className="playback-option"
                href={variant.playHref}
                aria-label={`Open ${[variant.title, variant.quality].filter(Boolean).join(' ') || 'version'}`}
              >
                <strong>{variant.quality || 'Version'}</strong>
                <span>{variant.title || variant.label || 'Playback version'}</span>
                <small>{[variant.durationLabel, variant.fileSizeLabel].filter(Boolean).join(' - ')}</small>
                <em>Open</em>
              </a>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
