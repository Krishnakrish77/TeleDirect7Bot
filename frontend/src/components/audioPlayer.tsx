import { BookmarkIcon, ChevronRightIcon, ListIcon, MusicIcon, PauseIcon, PlayIcon, SkipBackIcon, SkipForwardIcon, XIcon } from '../icons';
import { formatClock, type PlayerState } from '../hooks/audio';
import type { WatchTrack } from '../types';

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
        <img src={track.posterUrl || track.thumbUrl} alt="" />
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
  onClose,
  onOpenQueue,
}: {
  open: boolean;
  player: PlayerState;
  playRelative: (delta: number) => void;
  togglePlayback: (track?: WatchTrack) => void;
  seek: (seconds: number) => void;
  onClose: () => void;
  onOpenQueue: () => void;
}) {
  const track = player.track;
  if (!open || !track) return null;
  const duration = player.duration || track.duration || 0;
  const rangeMax = Math.max(1, Math.round(duration));
  return (
    <div className="sheet-layer" role="dialog" aria-modal="true" aria-label="Now playing">
      <button type="button" className="modal-scrim" onClick={onClose} aria-label="Close" />
      <section className="now-sheet">
        <button type="button" className="icon-button modal-close" onClick={onClose} aria-label="Close">
          <XIcon />
        </button>
        <img className="now-art" src={track.posterUrl || track.thumbUrl} alt="" />
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
          <button type="button" className="player-play" onClick={() => togglePlayback()} aria-label={player.playing ? 'Pause' : 'Play'}>
            {player.playing ? <PauseIcon /> : <PlayIcon />}
          </button>
          <button type="button" className="icon-button player-nav" onClick={() => playRelative(1)} disabled={player.queueIndex + 1 >= player.queue.length} aria-label="Next track">
            <SkipForwardIcon />
          </button>
          <button type="button" className="icon-button player-nav" onClick={onOpenQueue} disabled={player.queue.length < 2} aria-label="Open queue">
            <ListIcon />
          </button>
        </div>
        <a className="section-link classic-link" href={track.classicHref}>
          <span>Classic player</span>
          <ChevronRightIcon />
        </a>
      </section>
    </div>
  );
}

export function QueueDrawer({
  open,
  player,
  playQueueIndex,
  togglePlayback,
  onClose,
}: {
  open: boolean;
  player: PlayerState;
  playQueueIndex: (index: number) => void;
  togglePlayback: (track?: WatchTrack) => void;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div className="sheet-layer" role="dialog" aria-modal="true" aria-label="Queue">
      <button type="button" className="modal-scrim" onClick={onClose} aria-label="Close" />
      <aside className="queue-drawer">
        <div className="drawer-heading">
          <div>
            <p className="eyebrow">Queue</p>
            <h2>Up next</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close">
            <XIcon />
          </button>
        </div>
        {player.queue.length ? (
          <div className="track-list queue-list">
            {player.queue.map((track, index) => {
              const active = index === player.queueIndex;
              return (
                <button
                  type="button"
                  key={`${track.key}:${index}`}
                  className={active ? 'track-row active queue-row' : 'track-row queue-row'}
                  onClick={() => (active ? togglePlayback() : playQueueIndex(index))}
                >
                  <span className="track-number">{index + 1}</span>
                  <img src={track.posterUrl || track.thumbUrl} alt="" />
                  <span className="track-title">
                    <strong>{track.title}</strong>
                    <span>{[track.artist, track.albumTitle].filter(Boolean).join(' - ')}</span>
                  </span>
                  {active && player.playing ? <PauseIcon /> : <PlayIcon />}
                </button>
              );
            })}
          </div>
        ) : (
          <div className="empty-state">
            <MusicIcon />
            <strong>No queued tracks</strong>
          </div>
        )}
      </aside>
    </div>
  );
}

