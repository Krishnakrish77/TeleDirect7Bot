import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useAudioPlayer } from './audio';
import type { WatchTrack } from '../types';

function makeTrack(overrides: Partial<WatchTrack> = {}): WatchTrack {
  return {
    key: 'track-key',
    itemId: 'item-track-key',
    type: 'track',
    messageId: 1,
    secureHash: 'hash',
    title: 'Theme',
    year: 2026,
    mediaKind: 'audio',
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
  };
}

function AudioHarness({ track = makeTrack() }: { track?: WatchTrack }) {
  const audio = useAudioPlayer();
  return (
    <div>
      <audio data-testid="primary-audio" ref={audio.audioRef} />
      <audio data-testid="buffer-audio" ref={audio.bufferRef} />
      <button type="button" onClick={() => audio.playTrack(track, [track])}>Start</button>
      <button type="button" onClick={() => audio.toggleMute()}>Mute</button>
      <button type="button" onClick={() => audio.togglePlayback()}>Toggle</button>
      <span data-testid="muted">{String(audio.player.muted)}</span>
      <span data-testid="volume">{audio.player.volume}</span>
    </div>
  );
}

describe('useAudioPlayer', () => {
  it('restores audible output when playback starts from a stale muted preference', async () => {
    localStorage.setItem('td:muted', '1');
    localStorage.setItem('td:volume', '0');
    const play = vi.mocked(HTMLMediaElement.prototype.play);

    render(<AudioHarness />);

    fireEvent.click(screen.getByText('Start'));

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId('muted').textContent).toBe('false'));
    expect(primary.muted).toBe(false);
    expect(primary.volume).toBeGreaterThan(0);
    expect(localStorage.getItem('td:muted')).toBe('0');
    expect(Number(localStorage.getItem('td:volume'))).toBeGreaterThan(0);
  });

  it('preserves an explicit in-session mute across play toggles', async () => {
    const play = vi.mocked(HTMLMediaElement.prototype.play);

    render(<AudioHarness />);

    fireEvent.click(screen.getByText('Start'));
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText('Mute'));
    await waitFor(() => expect(screen.getByTestId('muted').textContent).toBe('true'));

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    Object.defineProperty(primary, 'paused', { configurable: true, value: true });
    fireEvent.click(screen.getByText('Toggle'));

    await waitFor(() => expect(play).toHaveBeenCalledTimes(2));
    expect(primary.muted).toBe(true);
    expect(primary.volume).toBe(0);
  });

  it('restores volume when unmuting a stale zero-volume player', async () => {
    localStorage.setItem('td:muted', '1');
    localStorage.setItem('td:volume', '0');

    render(<AudioHarness />);

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    fireEvent.click(screen.getByText('Mute'));

    await waitFor(() => expect(screen.getByTestId('muted').textContent).toBe('false'));
    expect(screen.getByTestId('volume').textContent).toBe('1');
    expect(primary.muted).toBe(false);
    expect(primary.volume).toBe(1);
  });
});
