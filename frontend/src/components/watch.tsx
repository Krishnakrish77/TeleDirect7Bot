import { TouchEvent, useCallback, useEffect, useRef, useState } from 'react';
import { fetchAudioTracks, fetchSubtitles, fetchWatch } from '../api';
import { CaptionsIcon, ChevronRightIcon, DownloadIcon, FilmIcon, ListIcon, MaximizeIcon, MoreVerticalIcon, PauseIcon, PictureInPictureIcon, PlayIcon, ShareIcon, SkipBackIcon, SkipForwardIcon, VolumeIcon } from '../icons';
import { formatClock, type PlayerState } from '../hooks/audio';
import type { AudioTrackOption, SubtitleTrack, WatchResponse, WatchTrack, WatchVideo } from '../types';
import { ErrorPanel, LoadingRows } from './common';
import { LyricsPanel } from './lyrics';

function isWatchTrack(item: WatchResponse['item']): item is WatchTrack {
  return item.type === 'track' && 'appHref' in item;
}

function isWatchVideo(item: WatchResponse['item']): item is WatchVideo {
  return item.type === 'video' && 'directSrc' in item;
}

export function WatchPage({
  watchKey,
  player,
  playTrack,
  playRelative,
  playQueueIndex,
  addToQueue,
  togglePlayback,
  seek,
  onOpenQueue,
}: {
  watchKey: string;
  player: PlayerState;
  playTrack: (track: WatchTrack, queue?: WatchTrack[]) => void;
  playRelative: (delta: number) => void;
  playQueueIndex: (index: number) => void;
  addToQueue: (track: WatchTrack, playNext?: boolean) => void;
  togglePlayback: (track?: WatchTrack, queue?: WatchTrack[]) => void;
  seek: (seconds: number) => void;
  onOpenQueue: () => void;
}) {
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

  return (
    <main className="watch-main audio-watch-main">
      <section className="audio-watch">
        <div className="audio-art">
          <img src={track.posterUrl || track.thumbUrl} alt="" decoding="async" />
        </div>
        <div className="audio-details">
          <p className="eyebrow">{[track.qualityLabel || track.format || 'Music', queue.length > 1 ? `${queue.length} tracks` : ''].filter(Boolean).join(' - ')}</p>
          <h1 dir="auto">{track.title}</h1>
          <p className="audio-subtitle">
            {[track.artist, track.albumTitle].filter(Boolean).join(' - ')}
          </p>
          {track.overview && <p className="audio-overview">{track.overview}</p>}

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
              disabled={queue.length < 2}
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
          <LyricsPanel
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
          </div>
          <div className="track-list">
            {queue.map((item, index) => {
              const active = player.track?.key === item.key;
              return (
                <a key={item.key} className={active ? 'track-row active' : 'track-row'} href={item.appHref}>
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
                    aria-label={active && player.playing ? 'Pause' : 'Play'}
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
                    aria-label="Play next"
                  >
                    <ListIcon />
                  </button>
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
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(video.duration || 0);
  const [sourceMode, setSourceMode] = useState<'direct' | 'hls'>('direct');
  const [audioIndex, setAudioIndex] = useState(0);
  const [subtitles, setSubtitles] = useState<SubtitleTrack[]>([]);
  const [activeSub, setActiveSub] = useState('');
  const [audioTracks, setAudioTracks] = useState<AudioTrackOption[]>([]);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [brightness, setBrightness] = useState(1);
  const [error, setError] = useState(video.knownUnplayable ? 'This file is marked as difficult for browser playback.' : '');
  const [showNext, setShowNext] = useState(false);
  const [nextCountdown, setNextCountdown] = useState(5);
  const [toast, setToast] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [autoplayNext, setAutoplayNext] = useState(() => {
    try {
      return localStorage.getItem('td:videoAutoplay') !== '0';
    } catch (_) {
      return true;
    }
  });
  const gestureRef = useRef({ x: 0, y: 0, t: 0, moved: false, lastTap: 0 });

  const sourceSrc = sourceMode === 'hls'
    ? `${video.hlsSrc}${audioIndex ? `?a=${audioIndex}` : ''}`
    : video.directSrc;
  const rangeMax = Math.max(1, Math.round(duration || video.duration || 0));
  const hasIntro = video.introEnd > video.introStart;
  const showSkipIntro = hasIntro && currentTime >= video.introStart && currentTime < video.introEnd;

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(''), 900);
  }, []);

  const changeVolume = useCallback((nextVolume: number) => {
    const next = Math.max(0, Math.min(1, nextVolume));
    setVolume(next);
    setMuted(next <= 0);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchSubtitles(video.subtitleBase, controller.signal).then(setSubtitles).catch(() => setSubtitles([]));
    fetchAudioTracks(video.audioTrackBase, controller.signal).then(setAudioTracks).catch(() => setAudioTracks([]));
    return () => controller.abort();
  }, [video.audioTrackBase, video.subtitleBase]);

  useEffect(() => {
    try {
      localStorage.setItem('td:videoAutoplay', autoplayNext ? '1' : '0');
    } catch (_) {
      // Local preference only.
    }
  }, [autoplayNext]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    el.volume = volume;
    el.muted = muted;
  }, [muted, sourceSrc, volume]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    Array.from(el.textTracks || []).forEach((track, index) => {
      const id = subtitles[index]?.id;
      track.mode = id && id === activeSub ? 'showing' : 'disabled';
    });
  }, [activeSub, sourceSrc, subtitles]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    let restored = false;
    let lastSave = 0;
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
    };
    const onTime = () => {
      setCurrentTime(el.currentTime || 0);
      setDuration(el.duration || video.duration || 0);
      saveResume();
    };
    const onLoaded = () => {
      setDuration(el.duration || video.duration || 0);
      loadResume();
    };
    const onEnded = () => {
      saveResume(true);
      setPlaying(false);
      if (video.nextEpisode) setShowNext(true);
    };
    const onError = () => {
      if (sourceMode === 'direct' && video.hlsSrc) {
        setSourceMode('hls');
        setError('');
      } else {
        setPlaying(false);
        setError('Browser playback failed. The classic player and VLC links are available.');
      }
    };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
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
  }, [sourceMode, sourceSrc, video]);

  useEffect(() => {
    if (!showNext || !video.nextEpisode || !autoplayNext) return;
    setNextCountdown(5);
    const interval = window.setInterval(() => {
      setNextCountdown((current) => {
        if (current <= 1) {
          window.clearInterval(interval);
          window.location.href = video.nextEpisode?.playHref || video.nextEpisode?.classicHref || '#';
          return 0;
        }
        return current - 1;
      });
    }, 1000);
    return () => window.clearInterval(interval);
  }, [autoplayNext, showNext, video.nextEpisode]);

  const toggleVideo = useCallback(() => {
    const el = videoRef.current;
    if (!el || video.knownUnplayable) return;
    if (el.paused) {
      el.play().catch(() => setError('Tap play again or open the classic player.'));
    } else {
      el.pause();
    }
  }, [video.knownUnplayable]);

  const seekVideo = useCallback((seconds: number) => {
    const el = videoRef.current;
    if (!el) return;
    const next = Math.max(0, Math.min(seconds, duration || video.duration || seconds));
    el.currentTime = next;
    setCurrentTime(next);
  }, [duration, video.duration]);

  const seekVideoBy = useCallback((delta: number) => {
    const base = videoRef.current?.currentTime ?? currentTime;
    seekVideo(base + delta);
    showToast(delta > 0 ? '+10s' : '-10s');
  }, [currentTime, seekVideo, showToast]);

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

  const shareVideo = useCallback(async () => {
    const data = { title: video.title, url: window.location.href };
    if (navigator.share) {
      try { await navigator.share(data); } catch (_) { return; }
    } else if (navigator.clipboard) {
      await navigator.clipboard.writeText(window.location.href).catch(() => undefined);
      showToast('Link copied');
    }
  }, [showToast, video.title]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const tagName = target?.tagName;
      if (target?.isContentEditable || tagName === 'INPUT' || tagName === 'SELECT' || tagName === 'TEXTAREA' || tagName === 'BUTTON') {
        return;
      }
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
  }, [seekVideoBy, toggleFullscreen, toggleVideo]);

  const onTouchStart = (event: TouchEvent<HTMLDivElement>) => {
    const touch = event.touches[0];
    const now = Date.now();
    const last = gestureRef.current.lastTap;
    const rect = event.currentTarget.getBoundingClientRect();
    if (now - last < 280) {
      const delta = touch.clientX < rect.left + rect.width / 2 ? -10 : 10;
      seekVideo((videoRef.current?.currentTime || 0) + delta);
      showToast(delta > 0 ? '+10s' : '-10s');
    }
    gestureRef.current = { x: touch.clientX, y: touch.clientY, t: now, moved: false, lastTap: now };
  };

  const onTouchMove = (event: TouchEvent<HTMLDivElement>) => {
    const touch = event.touches[0];
    const start = gestureRef.current;
    const dy = start.y - touch.clientY;
    const rect = event.currentTarget.getBoundingClientRect();
    if (Math.abs(dy) > 36) {
      start.moved = true;
      if (start.x > rect.left + rect.width / 2) {
        const next = Math.max(0, Math.min(1, volume + dy / 500));
        changeVolume(next);
        showToast(`Volume ${Math.round(next * 100)}%`);
      } else {
        const next = Math.max(0.45, Math.min(1, brightness + dy / 650));
        setBrightness(next);
        showToast(`Brightness ${Math.round(next * 100)}%`);
      }
    }
  };

  const onTouchEnd = (event: TouchEvent<HTMLDivElement>) => {
    const touch = event.changedTouches[0];
    const start = gestureRef.current;
    const dx = touch.clientX - start.x;
    if (Math.abs(dx) > 60) {
      const delta = Math.round(dx / 6);
      seekVideo((videoRef.current?.currentTime || 0) + delta);
      showToast(delta > 0 ? `+${delta}s` : `${delta}s`);
    }
  };

  return (
    <main className="video-main">
      <section className="video-titlebar">
        <a className="section-link" href={video.classicHref}>
          <span>Classic player</span>
          <ChevronRightIcon />
        </a>
        <div>
          <p className="eyebrow">{video.quality || 'Video'}</p>
          <h1 dir="auto">{video.title}</h1>
          {video.subtitle && <p>{video.subtitle}</p>}
        </div>
      </section>

      <section
        className="video-shell"
        ref={shellRef}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
      >
        <video
          key={`${sourceMode}:${audioIndex}:${video.key}`}
          ref={videoRef}
          src={video.knownUnplayable ? undefined : sourceSrc}
          poster={video.backdropUrl || video.posterUrl}
          crossOrigin="anonymous"
          playsInline
          preload="metadata"
        >
          {subtitles.map((track, index) => (
            <track
              key={track.id}
              id={String(index)}
              kind="subtitles"
              src={track.url}
              srcLang={track.language || 'und'}
              label={track.label || track.language || `Subtitle ${index + 1}`}
            />
          ))}
        </video>
        <div className="video-brightness-overlay" style={{ opacity: Math.max(0, 1 - brightness) }} />

        {toast && <div className="gesture-toast">{toast}</div>}

        {(error || video.knownUnplayable) && (
          <div className="video-overlay-message">
            <FilmIcon />
            <strong>This video needs another player</strong>
            <span>{error || 'Open it in VLC or the classic player.'}</span>
            <div>
              <a className="primary-action" href={video.classicHref}>Classic player</a>
              <a className="secondary-action" href={video.vlcHref}>VLC</a>
            </div>
          </div>
        )}

        {showSkipIntro && (
          <button type="button" className="skip-intro" onClick={() => seekVideo(video.introEnd)}>
            Skip intro
            <SkipForwardIcon />
          </button>
        )}

        {showNext && video.nextEpisode && (
          <div className="next-episode-card">
            <p className="eyebrow">{autoplayNext ? `Up next - ${nextCountdown}s` : 'Up next'}</p>
            <strong>{video.nextEpisode.title}</strong>
            <div>
              <a className="primary-action" href={video.nextEpisode.playHref}>
                <PlayIcon />
                <span>Play</span>
              </a>
              <button type="button" className="secondary-action" onClick={() => setShowNext(false)}>Cancel</button>
            </div>
          </div>
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
            <input
              type="range"
              min="0"
              max={rangeMax}
              value={Math.min(rangeMax, Math.round(currentTime))}
              onChange={(event) => seekVideo(Number(event.currentTarget.value))}
              aria-label="Playback position"
            />
            <span>{formatClock(duration)}</span>
          </div>
          <button type="button" className="icon-button video-step" onClick={() => seekVideoBy(10)} aria-label="Forward 10 seconds">
            <span aria-hidden="true">+10</span>
          </button>
          <button type="button" className="icon-button" onClick={() => setMuted((isMuted) => !isMuted)} aria-label={muted ? 'Unmute' : 'Mute'}>
            <VolumeIcon />
          </button>
          <button type="button" className="icon-button video-pip-control" onClick={togglePip} aria-label="Picture in picture">
            <PictureInPictureIcon />
          </button>
          <button type="button" className="icon-button" onClick={toggleFullscreen} aria-label="Fullscreen">
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
              <select value={activeSub} onChange={(event) => setActiveSub(event.currentTarget.value)} disabled={!subtitles.length} aria-label="Captions">
                <option value="">Off</option>
                {subtitles.map((track) => (
                  <option key={track.id} value={track.id}>{track.label || track.language || track.id}</option>
                ))}
              </select>
            </label>
            <label className="video-menu-row">
              <span>Audio</span>
              <select
                value={audioIndex}
                onChange={(event) => {
                  setAudioIndex(Number(event.currentTarget.value));
                  setSourceMode('hls');
                }}
                disabled={!audioTracks.length}
                aria-label="Audio track"
              >
                <option value={0}>Default</option>
                {audioTracks.map((track) => (
                  <option key={track.index} value={track.index}>{track.label || track.language || `Track ${track.index + 1}`}</option>
                ))}
              </select>
            </label>
            <button type="button" className="video-menu-row" role="menuitem" onClick={() => setSourceMode(sourceMode === 'direct' ? 'hls' : 'direct')}>
              <span>Source</span>
              <strong>{sourceMode === 'direct' ? 'Direct' : 'HLS'}</strong>
            </button>
            <a className="video-menu-row" role="menuitem" href={video.classicHref}>
              <span>Classic player</span>
              <strong>Open</strong>
            </a>
            <a className="video-menu-row" role="menuitem" href={video.vlcHref}>
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

      <section className="video-actions">
        <label className="volume-control">
          <VolumeIcon />
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={volume}
            onChange={(event) => changeVolume(Number(event.currentTarget.value))}
            aria-label="Volume"
          />
        </label>
        <label>
          <CaptionsIcon />
          <select value={activeSub} onChange={(event) => setActiveSub(event.currentTarget.value)} disabled={!subtitles.length} aria-label="Captions">
            <option value="">Captions off</option>
            {subtitles.map((track) => (
              <option key={track.id} value={track.id}>{track.label || track.language || track.id}</option>
            ))}
          </select>
        </label>
        <label>
          <VolumeIcon />
          <select
            value={audioIndex}
            onChange={(event) => {
              setAudioIndex(Number(event.currentTarget.value));
              setSourceMode('hls');
            }}
            disabled={!audioTracks.length}
            aria-label="Audio track"
          >
            <option value={0}>Default audio</option>
            {audioTracks.map((track) => (
              <option key={track.index} value={track.index}>{track.label || track.language || `Track ${track.index + 1}`}</option>
            ))}
          </select>
        </label>
        <button type="button" className="secondary-action" onClick={() => setSourceMode(sourceMode === 'direct' ? 'hls' : 'direct')}>
          <span>{sourceMode === 'direct' ? 'HLS' : 'Direct'}</span>
        </button>
        <a className="secondary-action" href={video.vlcHref}>
          <PlayIcon />
          <span>VLC</span>
        </a>
        <a className="secondary-action" href={video.downloadHref} download>
          <DownloadIcon />
          <span>Download</span>
        </a>
        <button type="button" className="secondary-action" onClick={shareVideo}>
          <ShareIcon />
          <span>Share</span>
        </button>
      </section>

      {video.qualityVariants.length > 0 && (
        <section className="quality-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Versions</p>
              <h2>Quality variants</h2>
            </div>
          </div>
          <div className="variant-list">
            <a className="variant-row active" href={video.appHref}>
              <span>{video.quality || 'Current'}</span>
              <strong>{video.durationLabel || 'Current version'}</strong>
            </a>
            {video.qualityVariants.map((variant) => (
              <a key={variant.key} className="variant-row" href={variant.playHref}>
                <span>{variant.quality || 'Version'}</span>
                <strong>{variant.label || variant.title}</strong>
              </a>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
