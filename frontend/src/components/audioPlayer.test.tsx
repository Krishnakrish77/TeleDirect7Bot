import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { PlayerState } from '../hooks/audio';
import type { WatchTrack } from '../types';
import { NowPlayingSheet } from './audioPlayer';

function makeTrack(overrides: Partial<WatchTrack> = {}): WatchTrack {
  return {
    key: 'track-key',
    itemId: 'item-track-key',
    type: 'track',
    messageId: 1,
    secureHash: 'hash',
    title: 'Theme',
    year: 2026,
    mediaKind: 'music',
    posterUrl: '/thumb/track.jpg',
    thumbUrl: '/thumb/track.jpg',
    backdropUrl: '/thumb/track-backdrop.jpg',
    duration: 100,
    durationLabel: '1:40',
    fileSize: 1000,
    fileSizeLabel: '1 KB',
    quality: 'mp3',
    genres: [],
    tags: [],
    overview: '',
    artist: 'Composer',
    albumTitle: 'Album',
    href: '/watch/track-key',
    streamHref: '/stream/track-key',
    watchKey: 'track-key',
    trackNumber: 1,
    format: 'MP3',
    qualityLabel: 'MP3',
    appHref: '/app/watch/track-key',
    classicHref: '/watch/track-key',
    albumHref: '/app/album/album',
    ...overrides,
  } as WatchTrack;
}

function makePlayer(overrides: Partial<PlayerState> = {}): PlayerState {
  const track = makeTrack();
  return {
    track,
    queue: [track],
    queueIndex: 0,
    playing: true,
    currentTime: 35,
    duration: 100,
    error: '',
    speed: 1,
    repeatMode: 'off',
    volume: 1,
    muted: false,
    nextTrack: null,
    nextCountdown: 5,
    queueToast: '',
    ...overrides,
  } as PlayerState;
}

describe('NowPlayingSheet', () => {
  it('exposes quick seek controls for the audio player', () => {
    const seek = vi.fn();

    render(
      <NowPlayingSheet
        open
        player={makePlayer()}
        playRelative={vi.fn()}
        togglePlayback={vi.fn()}
        seek={seek}
        setSpeed={vi.fn()}
        cycleRepeatMode={vi.fn()}
        setVolume={vi.fn()}
        toggleMute={vi.fn()}
        confirmNext={vi.fn()}
        cancelNext={vi.fn()}
        onClose={vi.fn()}
        onOpenQueue={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText('Rewind 10 seconds'));
    expect(seek).toHaveBeenCalledWith(25);

    fireEvent.click(screen.getByLabelText('Forward 10 seconds'));
    expect(seek).toHaveBeenCalledWith(45);
  });

  it('exposes speed, repeat, volume, and pending next-track actions', () => {
    const setSpeed = vi.fn();
    const cycleRepeatMode = vi.fn();
    const setVolume = vi.fn();
    const confirmNext = vi.fn();
    const cancelNext = vi.fn();
    const nextTrack = makeTrack({ key: 'next-track', title: 'Next Theme' });

    render(
      <NowPlayingSheet
        open
        player={makePlayer({ nextTrack, nextCountdown: 3 })}
        playRelative={vi.fn()}
        togglePlayback={vi.fn()}
        seek={vi.fn()}
        setSpeed={setSpeed}
        cycleRepeatMode={cycleRepeatMode}
        setVolume={setVolume}
        toggleMute={vi.fn()}
        confirmNext={confirmNext}
        cancelNext={cancelNext}
        onClose={vi.fn()}
        onOpenQueue={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText('1.5x'));
    expect(setSpeed).toHaveBeenCalledWith(1.5);

    fireEvent.click(screen.getByText('Repeat off'));
    expect(cycleRepeatMode).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText('Audio volume'), { target: { value: '0.4' } });
    expect(setVolume).toHaveBeenCalledWith(0.4);

    fireEvent.click(screen.getByText('Play now'));
    expect(confirmNext).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText('Cancel'));
    expect(cancelNext).toHaveBeenCalledTimes(1);
  });
});
