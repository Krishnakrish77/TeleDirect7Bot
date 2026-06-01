import { ChevronRightIcon, ListIcon, PauseIcon, PlayIcon, SkipBackIcon, SkipForwardIcon, VolumeIcon, XIcon } from '../icons';
import { formatClock, type PlayerState } from '../hooks/audio';
import type { WatchTrack } from '../types';
import { LyricsPanel } from './lyrics';

const SPEEDS = [0.75, 1, 1.5, 2];

export function MiniPlayer({
  player,
  playRelative,
  togglePlayback,
  seek,
  onExpand,
  onOpenQueue,
}: {
  player: PlayerState;
  playRelative: (delta: number) => void;
  playQueueIndex: (index: number) => void;
  togglePlayback: (track?: WatchTrack) => void;
  seek: (seconds: number) => void;
  onExpand: () => void;
  onOpenQueue: () => void;
}) {
  const track = player.track;
  if (!track) return null;
  const duration = player.duration || track.duration || 0;
  const rangeMax = Math.max(1, Math.round(duration));
  const hasPrev = player.queueIndex > 0;
  const hasNext = player.queueIndex + 1 < player.queue.length;

  return (
    <aside className="mini-player" aria-label="Audio player">
      <button type="button" className="mini-track mini-track-button" onClick={onExpand}>
        <img src={track.posterUrl || track.thumbUrl} alt="" decoding="async" />
        <span>
          <strong>{track.title}</strong>
          <span>{[track.artist, track.albumTitle].filter(Boolean).join(' - ')}</span>
        </span>
      </button>
      <div className="mini-controls">
        <button type="button" className="icon-button" onClick={() => playRelative(-1)} disabled={!hasPrev} aria-label="Previous track">
          <SkipBackIcon />
        </button>
        <button type="button" className="player-play mini-play" onClick={() => togglePlayback()} aria-label={player.playing ? 'Pause' : 'Play'}>
          {player.playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <button type="button" className="icon-button" onClick={() => playRelative(1)} disabled={!hasNext} aria-label="Next track">
          <SkipForwardIcon />
        </button>
        <button type="button" className="icon-button" onClick={onOpenQueue} disabled={player.queue.length < 2} aria-label="Open queue">
          <ListIcon />
        </button>
      </div>
      <div className="mini-progress">
        <span>{formatClock(player.currentTime)}</span>
        <input
          type="range"
          min="0"
          max={rangeMax}
          value={Math.min(rangeMax, Math.round(player.currentTime))}
          onChange={(event) => seek(Number(event.currentTarget.value))}
          aria-label="Playback position"
        />
        <span>{formatClock(duration)}</span>
      </div>
      {player.queueToast && <p className="queue-toast" role="status">{player.queueToast}</p>}
      {player.error && <p className="player-error mini-error">{player.error}</p>}
    </aside>
  );
}

export function NowPlayingSheet({
  open,
  player,
  playRelative,
  togglePlayback,
  seek,
  setSpeed,
  cycleRepeatMode,
  setVolume,
  toggleMute,
  confirmNext,
  cancelNext,
  onClose,
  onOpenQueue,
}: {
  open: boolean;
  player: PlayerState;
  playRelative: (delta: number) => void;
  togglePlayback: (track?: WatchTrack) => void;
  seek: (seconds: number) => void;
  setSpeed: (speed: number) => void;
  cycleRepeatMode: () => void;
  setVolume: (volume: number) => void;
  toggleMute: () => void;
  confirmNext: () => void;
  cancelNext: () => void;
  onClose: () => void;
  onOpenQueue: () => void;
}) {
  const track = player.track;
  if (!open || !track) return null;
  const duration = player.duration || track.duration || 0;
  const rangeMax = Math.max(1, Math.round(duration));
  const seekBy = (delta: number) => {
    seek(Math.max(0, Math.min(rangeMax, player.currentTime + delta)));
  };
  return (
    <div className="sheet-layer" role="dialog" aria-modal="true" aria-label="Now playing">
      <button type="button" className="modal-scrim" onClick={onClose} aria-label="Close" />
      <section className="now-sheet">
        <button type="button" className="icon-button modal-close" onClick={onClose} aria-label="Close">
          <XIcon />
        </button>
        <img className="now-art" src={track.posterUrl || track.thumbUrl} alt="" decoding="async" />
        <div className="now-copy">
          <p className="eyebrow">{track.qualityLabel || track.format || 'Now playing'}</p>
          <h2>{track.title}</h2>
          <p>{[track.artist, track.albumTitle].filter(Boolean).join(' - ')}</p>
        </div>
        <div className="watch-progress now-progress">
          <span>{formatClock(player.currentTime)}</span>
          <input
            type="range"
            min="0"
            max={rangeMax}
            value={Math.min(rangeMax, Math.round(player.currentTime))}
            onChange={(event) => seek(Number(event.currentTarget.value))}
            aria-label="Playback position"
          />
          <span>{formatClock(duration)}</span>
        </div>
        <div className="watch-controls now-controls">
          <button type="button" className="icon-button player-nav" onClick={() => playRelative(-1)} disabled={player.queueIndex <= 0} aria-label="Previous track">
            <SkipBackIcon />
          </button>
          <button type="button" className="icon-button player-nav" onClick={() => seekBy(-10)} aria-label="Rewind 10 seconds">
            <span aria-hidden="true">-10</span>
          </button>
          <button type="button" className="player-play" onClick={() => togglePlayback()} aria-label={player.playing ? 'Pause' : 'Play'}>
            {player.playing ? <PauseIcon /> : <PlayIcon />}
          </button>
          <button type="button" className="icon-button player-nav" onClick={() => seekBy(10)} aria-label="Forward 10 seconds">
            <span aria-hidden="true">+10</span>
          </button>
          <button type="button" className="icon-button player-nav" onClick={() => playRelative(1)} disabled={player.queueIndex + 1 >= player.queue.length} aria-label="Next track">
            <SkipForwardIcon />
          </button>
          <button type="button" className="icon-button player-nav" onClick={onOpenQueue} disabled={player.queue.length < 2} aria-label="Open queue">
            <ListIcon />
          </button>
        </div>
        <div className="player-settings" aria-label="Playback settings">
          <div className="speed-controls" aria-label="Playback speed">
            {SPEEDS.map((speed) => (
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
        {player.nextTrack && (
          <div className="next-track-card">
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
        <a className="section-link classic-link" href={track.classicHref}>
          <span>Classic player</span>
          <ChevronRightIcon />
        </a>
        <LyricsPanel
          className="now-lyrics"
          track={track}
          currentTime={player.currentTime}
          seek={seek}
        />
      </section>
    </div>
  );
}
