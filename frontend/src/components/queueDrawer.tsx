import { memo, useMemo } from 'react';
import { ChevronDownIcon, ChevronUpIcon, ListIcon, MusicIcon, PauseIcon, PlayIcon, SkipForwardIcon, XIcon } from '../icons';
import { formatClock, type PlayerState } from '../hooks/audio';
import type { WatchTrack } from '../types';
import { Button } from './ui/button';
import { Dialog, DialogClose, DialogContent, DialogTitle } from './ui/dialog';

function trackSubtitle(track: WatchTrack) {
  return [track.artist, track.albumTitle].filter(Boolean).join(' - ') || track.format || track.qualityLabel || 'Track';
}

function trackArtwork(track: WatchTrack) {
  return track.posterUrl || track.thumbUrl;
}

function trackDurationLabel(track: WatchTrack) {
  if (track.durationLabel) return track.durationLabel;
  return track.duration ? formatClock(track.duration) : '';
}

function pluralizeTracks(count: number) {
  return `${count} ${count === 1 ? 'track' : 'tracks'}`;
}

type QueueTrackRowProps = {
  track: WatchTrack;
  index: number;
  canMoveUp: boolean;
  canMoveDown: boolean;
  canRemove: boolean;
  showReorder: boolean;
  playQueueIndex: (index: number) => void;
  moveQueueItemToNext: (index: number) => void;
  moveQueueItem: (index: number, direction: -1 | 1) => void;
  removeFromQueue: (index: number) => void;
};

const QueueTrackRow = memo(function QueueTrackRow({
  track,
  index,
  canMoveUp,
  canMoveDown,
  canRemove,
  showReorder,
  playQueueIndex,
  moveQueueItemToNext,
  moveQueueItem,
  removeFromQueue,
}: QueueTrackRowProps) {
  const durationLabel = trackDurationLabel(track);

  return (
    <div className="queue-row">
      <span className="queue-position">{index + 1}</span>
      <img className="queue-row-art" src={trackArtwork(track)} alt="" loading="lazy" decoding="async" />
      <Button
        type="button"
        variant="ghost"
        className="queue-title-button h-auto p-0"
        onClick={() => playQueueIndex(index)}
        aria-label={`Play ${track.title}`}
      >
        <span className="queue-row-copy">
          <strong>{track.title}</strong>
          <span>{trackSubtitle(track)}</span>
        </span>
        <span className="queue-row-play" aria-hidden="true">
          <PlayIcon />
        </span>
      </Button>
      <span className="queue-duration">{durationLabel}</span>
      <div className="queue-row-actions">
        {showReorder && (
          <>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="icon-button queue-play-next"
              onClick={() => moveQueueItemToNext(index)}
              disabled={!canMoveUp}
              aria-label={`Play ${track.title} next`}
              title="Play next"
            >
              <SkipForwardIcon />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="icon-button"
              onClick={() => moveQueueItem(index, -1)}
              disabled={!canMoveUp}
              aria-label={`Move ${track.title} up`}
            >
              <ChevronUpIcon />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="icon-button"
              onClick={() => moveQueueItem(index, 1)}
              disabled={!canMoveDown}
              aria-label={`Move ${track.title} down`}
            >
              <ChevronDownIcon />
            </Button>
          </>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="icon-button"
          onClick={() => removeFromQueue(index)}
          disabled={!canRemove}
          aria-label={`Remove ${track.title}`}
        >
          <XIcon />
        </Button>
      </div>
    </div>
  );
});

export function QueueDrawer({
  open,
  player,
  playQueueIndex,
  togglePlayback,
  moveQueueItemToNext,
  removeFromQueue,
  clearQueue,
  moveQueueItem,
  onClose,
}: {
  open: boolean;
  player: PlayerState;
  playQueueIndex: (index: number) => void;
  togglePlayback: (track?: WatchTrack) => void;
  moveQueueItemToNext: (index: number) => void;
  removeFromQueue: (index: number) => void;
  clearQueue: () => void;
  moveQueueItem: (index: number, direction: -1 | 1) => void;
  onClose: () => void;
}) {
  const queue = useMemo(() => (
    player.queue.length ? player.queue : player.track ? [player.track] : []
  ), [player.queue, player.track]);
  const currentIndex = queue.length ? Math.min(Math.max(player.queueIndex, 0), queue.length - 1) : -1;
  const currentTrack = currentIndex >= 0 ? queue[currentIndex] : null;
  const { played, upNext } = useMemo(() => {
    const sections = { played: [] as { track: WatchTrack; index: number }[], upNext: [] as { track: WatchTrack; index: number }[] };
    queue.forEach((track, index) => {
      if (index < currentIndex) sections.played.push({ track, index });
      if (index > currentIndex) sections.upNext.push({ track, index });
    });
    return sections;
  }, [currentIndex, queue]);
  const totalLabel = queue.length ? pluralizeTracks(queue.length) : 'No tracks';
  const progressLabel = currentTrack
    ? `${formatClock(player.currentTime)} / ${formatClock(player.duration || currentTrack.duration || 0)}`
    : '';

  if (!open) return null;

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}>
      <DialogContent className="queue-drawer" aria-describedby={undefined}>
        <div className="drawer-heading">
          <div>
            <p className="eyebrow">Now playing</p>
            <DialogTitle asChild><h2>Queue</h2></DialogTitle>
            <p className="queue-count">{totalLabel}</p>
          </div>
          <div className="drawer-actions">
            <Button type="button" variant="ghost" size="sm" className="text-button" onClick={clearQueue} disabled={queue.length < 2}>Clear queue</Button>
            <DialogClose asChild><Button type="button" variant="ghost" size="icon-sm" className="icon-button" aria-label="Close"><XIcon /></Button></DialogClose>
          </div>
        </div>
        {currentTrack ? (
          <section className="queue-current-card" aria-label="Current track">
            <img src={trackArtwork(currentTrack)} alt="" decoding="async" />
            <div className="queue-current-copy">
              <p className="eyebrow">Playing now</p>
              <strong>{currentTrack.title}</strong>
              <span>{trackSubtitle(currentTrack)}</span>
              {progressLabel && <small>{progressLabel}</small>}
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="player-play queue-current-play"
              onClick={() => togglePlayback()}
              aria-label={player.playing ? 'Pause current track' : 'Play current track'}
            >
              {player.playing ? <PauseIcon /> : <PlayIcon />}
            </Button>
          </section>
        ) : null}
        {queue.length ? (
          <div className="queue-body">
            <div className="queue-summary-strip" aria-label="Queue summary">
              <span aria-label={`${upNext.length} up next`}><strong>{upNext.length}</strong> up next</span>
              <span aria-label={`${played.length} played`}><strong>{played.length}</strong> played</span>
            </div>
            <section className="queue-section" aria-label="Up next">
              <div className="queue-section-heading">
                <div>
                  <p className="eyebrow">Up next</p>
                  <h3>{upNext.length ? pluralizeTracks(upNext.length) : 'Nothing queued'}</h3>
                </div>
                {upNext.length > 0 && <span>{currentIndex + 2}-{queue.length}</span>}
              </div>
              {upNext.length ? (
                <div className="queue-list">
                  {upNext.map(({ track, index }) => (
                    <QueueTrackRow
                      key={`${track.key}:${index}`}
                      track={track}
                      index={index}
                      canMoveUp={index > currentIndex + 1}
                      canMoveDown={index + 1 < queue.length}
                      canRemove
                      showReorder
                      playQueueIndex={playQueueIndex}
                      moveQueueItemToNext={moveQueueItemToNext}
                      moveQueueItem={moveQueueItem}
                      removeFromQueue={removeFromQueue}
                    />
                  ))}
                </div>
              ) : (
                <div className="queue-empty compact">
                  <ListIcon />
                  <span>Add songs from an album or shelf to build the queue.</span>
                </div>
              )}
            </section>
            {played.length > 0 && (
              <details className="queue-history">
                <summary>
                  <span>Played earlier</span>
                  <strong>{pluralizeTracks(played.length)}</strong>
                </summary>
                <div className="queue-list">
                  {played.map(({ track, index }) => (
                    <QueueTrackRow
                      key={`${track.key}:${index}`}
                      track={track}
                      index={index}
                      canMoveUp={false}
                      canMoveDown={false}
                      canRemove
                      showReorder={false}
                      playQueueIndex={playQueueIndex}
                      moveQueueItemToNext={moveQueueItemToNext}
                      moveQueueItem={moveQueueItem}
                      removeFromQueue={removeFromQueue}
                    />
                  ))}
                </div>
              </details>
            )}
          </div>
        ) : (
          <div className="queue-empty">
            <MusicIcon />
            <strong>No queued tracks</strong>
            <span>Start playback from an album to fill this list.</span>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
