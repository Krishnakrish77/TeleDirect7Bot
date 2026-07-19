import { type ReactNode, useEffect, useId, useState } from 'react';
import { ChevronRightIcon, DownloadIcon, ListIcon, MoreVerticalIcon, MusicIcon, PauseIcon, PlayIcon, RepeatIcon, SkipBackIcon, SkipForwardIcon, VolumeIcon, XIcon } from '../icons';
import { formatClock, type PlayerState } from '../hooks/audio';
import type { WatchTrack } from '../types';
import { LyricsPanel } from './lyrics';
import { Button } from './ui/button';
import { Dialog, DialogClose, DialogContent, DialogTitle } from './ui/dialog';

export function useCompactAudioLayout(): boolean {
  const getValue = () => typeof window !== 'undefined' && Boolean(window.matchMedia?.('(max-width: 680px)').matches);
  const [compact, setCompact] = useState(getValue);

  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const media = window.matchMedia('(max-width: 680px)');
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener?.('change', update);
    return () => media.removeEventListener?.('change', update);
  }, []);

  return compact;
}

export function AudioSettingsControls({
  player,
  cycleRepeatMode,
  setVolume,
  toggleMute,
  className = '',
}: {
  player: PlayerState;
  cycleRepeatMode: () => void;
  setVolume: (volume: number) => void;
  toggleMute: () => void;
  className?: string;
}) {
  return (
    <div className={`player-settings ${className}`.trim()} aria-label="Playback settings">
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className={player.repeatMode === 'off' ? 'icon-button repeat-button' : 'icon-button repeat-button active'}
        onClick={cycleRepeatMode}
        aria-label={`Repeat ${player.repeatMode}`}
        title={`Repeat ${player.repeatMode}`}
      >
        <RepeatIcon />
        {player.repeatMode === 'one' && <span aria-hidden="true">1</span>}
      </Button>
      <label className="volume-control audio-volume-control">
        <Button type="button" variant="ghost" size="icon-sm" className="icon-button" onClick={toggleMute} aria-label={player.muted ? 'Unmute audio' : 'Mute audio'}>
          <VolumeIcon />
        </Button>
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
  );
}

export function AudioSettingsDisclosure({
  open,
  onToggle,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const settingsId = useId();
  return (
    <div className="audio-settings-disclosure">
      <Button type="button" variant="secondary" size="sm" onClick={onToggle} aria-expanded={open} aria-controls={settingsId}>
        <MoreVerticalIcon />
        <span>Playback settings</span>
      </Button>
      {open && <div id={settingsId}>{children}</div>}
    </div>
  );
}

export function AudioPlaybackIssue({
  message,
  track,
  onRetry,
  compact = false,
  live = 'alert',
}: {
  message: string;
  track: WatchTrack;
  onRetry?: () => void;
  compact?: boolean;
  live?: 'alert' | 'status';
}) {
  if (!message) return null;
  const downloadHref = track.downloadHref || track.streamHref;
  return (
    <div className={compact ? 'player-error playback-issue compact' : 'player-error playback-issue'} role={live}>
      <p>{message}</p>
      <div className="playback-issue-actions">
        {onRetry && (
          <Button type="button" variant="outline" size="sm" className="playback-issue-action" onClick={onRetry}>
            <PlayIcon />
            <span>Retry</span>
          </Button>
        )}
        {!compact && downloadHref && (
          <a className="playback-issue-action" href={downloadHref} download>
            <DownloadIcon />
            <span>Download</span>
          </a>
        )}
      </div>
    </div>
  );
}

export function MiniPlayer({
  player,
  playRelative,
  togglePlayback,
  seek,
  onExpand,
  onOpenQueue,
  onDismiss,
}: {
  player: PlayerState;
  playRelative: (delta: number) => void;
  playQueueIndex: (index: number) => void;
  togglePlayback: (track?: WatchTrack) => void;
  seek: (seconds: number) => void;
  onExpand: () => void;
  onOpenQueue: () => void;
  onDismiss: () => void;
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
        <span className="audio-art-wrap">
          <MusicIcon />
          <img src={track.posterUrl || track.thumbUrl} alt="" decoding="async" onError={(e) => { e.currentTarget.hidden = true; }} />
        </span>
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
        <button type="button" className="icon-button" onClick={onOpenQueue} aria-label="Open queue">
          <ListIcon />
        </button>
      </div>
      <button type="button" className="icon-button mini-dismiss" onClick={onDismiss} aria-label="Close mini player">
        <XIcon />
      </button>
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
      <AudioPlaybackIssue
        compact
        message={player.error}
        track={track}
        onRetry={() => togglePlayback()}
      />
    </aside>
  );
}

export function NowPlayingSheet({
  open,
  player,
  playRelative,
  togglePlayback,
  seek,
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
  cycleRepeatMode: () => void;
  setVolume: (volume: number) => void;
  toggleMute: () => void;
  confirmNext: () => void;
  cancelNext: () => void;
  onClose: () => void;
  onOpenQueue: () => void;
}) {
  const compact = useCompactAudioLayout();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const track = player.track;
  if (!open || !track) return null;
  const duration = player.duration || track.duration || 0;
  const rangeMax = Math.max(1, Math.round(duration));
  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}>
      <DialogContent className="now-sheet" aria-describedby={undefined}>
        <DialogTitle className="sr-only">Now playing</DialogTitle>
        <DialogClose asChild><Button type="button" variant="ghost" size="icon-sm" className="icon-button modal-close" aria-label="Close"><XIcon /></Button></DialogClose>
        <div className="audio-art-wrap now-art-wrap">
          <MusicIcon />
          <img src={track.posterUrl || track.thumbUrl} alt="" decoding="async" onError={(e) => { e.currentTarget.hidden = true; }} />
        </div>
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
          <Button type="button" variant="ghost" size="icon-sm" className="icon-button player-nav" onClick={() => playRelative(-1)} disabled={player.queueIndex <= 0} aria-label="Previous track">
            <SkipBackIcon />
          </Button>
          <Button type="button" variant="ghost" size="icon" className="player-play" onClick={() => togglePlayback()} aria-label={player.playing ? 'Pause' : 'Play'}>
            {player.playing ? <PauseIcon /> : <PlayIcon />}
          </Button>
          <Button type="button" variant="ghost" size="icon-sm" className="icon-button player-nav" onClick={() => playRelative(1)} disabled={player.queueIndex + 1 >= player.queue.length} aria-label="Next track">
            <SkipForwardIcon />
          </Button>
          <Button type="button" variant="ghost" size="icon-sm" className="icon-button player-nav" onClick={onOpenQueue} aria-label="Open queue">
            <ListIcon />
          </Button>
        </div>
        {compact ? (
          <AudioSettingsDisclosure open={settingsOpen} onToggle={() => setSettingsOpen((value) => !value)}>
            <AudioSettingsControls
              player={player}
              cycleRepeatMode={cycleRepeatMode}
              setVolume={setVolume}
              toggleMute={toggleMute}
            />
          </AudioSettingsDisclosure>
        ) : (
          <AudioSettingsControls
            player={player}
            cycleRepeatMode={cycleRepeatMode}
            setVolume={setVolume}
            toggleMute={toggleMute}
          />
        )}
        {player.nextTrack && (
          <div className="next-track-card">
            <p className="eyebrow">Up next - {player.nextCountdown}s</p>
            <strong>{player.nextTrack.title}</strong>
            <span>{[player.nextTrack.artist, player.nextTrack.albumTitle].filter(Boolean).join(' - ')}</span>
            <div>
              <Button type="button" onClick={confirmNext}>
                <PlayIcon />
                <span>Play now</span>
              </Button>
              <Button type="button" variant="secondary" onClick={cancelNext}>Cancel</Button>
            </div>
          </div>
        )}
        {player.queueToast && <p className="queue-toast" role="status">{player.queueToast}</p>}
        <AudioPlaybackIssue
          live="status"
          message={player.error}
          track={track}
          onRetry={() => togglePlayback()}
        />
        <LyricsPanel
          className="now-lyrics"
          track={track}
          currentTime={player.currentTime}
          seek={seek}
        />
      </DialogContent>
    </Dialog>
  );
}
