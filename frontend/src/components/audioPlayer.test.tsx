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
    ...overrides,
  };
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
    ...overrides,
  };
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
        onClose={vi.fn()}
        onOpenQueue={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText('Rewind 10 seconds'));
    expect(seek).toHaveBeenCalledWith(25);

    fireEvent.click(screen.getByLabelText('Forward 10 seconds'));
    expect(seek).toHaveBeenCalledWith(45);
  });
});
