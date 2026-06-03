import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
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

function AudioHarness({ track = makeTrack(), queue }: { track?: WatchTrack; queue?: WatchTrack[] }) {
  const audio = useAudioPlayer();
  return (
    <div>
      <audio data-testid="primary-audio" ref={audio.audioRef} />
      <audio data-testid="buffer-audio" ref={audio.bufferRef} />
      <button type="button" onClick={() => audio.playTrack(track, queue || [track])}>Start</button>
      <button type="button" onClick={() => audio.toggleMute()}>Mute</button>
      <button type="button" onClick={() => audio.togglePlayback()}>Toggle</button>
      <button type="button" onClick={() => audio.dismissPlayer()}>Dismiss</button>
      <span data-testid="track">{audio.player.track?.title || 'none'}</span>
      <span data-testid="muted">{String(audio.player.muted)}</span>
      <span data-testid="volume">{audio.player.volume}</span>
    </div>
  );
}

function installMediaSession() {
  const handlers = new Map<string, MediaSessionActionHandler>();
  const mediaSession = {
    metadata: null as unknown,
    playbackState: 'none',
    setActionHandler: vi.fn((action: MediaSessionAction, handler: MediaSessionActionHandler | null) => {
      if (handler) handlers.set(action, handler);
      else handlers.delete(action);
    }),
    setPositionState: vi.fn(),
  };
  class MockMediaMetadata {
    title: string;
    artist: string;
    album: string;
    artwork: MediaImage[];

    constructor(init: MediaMetadataInit) {
      this.title = init.title || '';
      this.artist = init.artist || '';
      this.album = init.album || '';
      this.artwork = init.artwork || [];
    }
  }
  Object.defineProperty(navigator, 'mediaSession', {
    configurable: true,
    value: mediaSession,
  });
  Object.defineProperty(window, 'MediaMetadata', {
    configurable: true,
    value: MockMediaMetadata,
  });
  return { handlers, mediaSession };
}

afterEach(() => {
  Reflect.deleteProperty(navigator, 'mediaSession');
  Reflect.deleteProperty(window, 'MediaMetadata');
});

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

  it('dismisses the player by stopping audio and clearing persisted playback', async () => {
    const play = vi.mocked(HTMLMediaElement.prototype.play);
    const pause = vi.mocked(HTMLMediaElement.prototype.pause);
    const load = vi.mocked(HTMLMediaElement.prototype.load);

    render(<AudioHarness />);

    fireEvent.click(screen.getByText('Start'));

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    const buffer = screen.getByTestId('buffer-audio') as HTMLAudioElement;
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Theme'));
    await waitFor(() => expect(localStorage.getItem('td:reactPlayer')).toBeTruthy());
    pause.mockClear();
    load.mockClear();

    fireEvent.click(screen.getByText('Dismiss'));

    await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('none'));
    expect(primary.getAttribute('src')).toBeNull();
    expect(buffer.getAttribute('src')).toBeNull();
    expect(pause).toHaveBeenCalledTimes(2);
    expect(load).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(localStorage.getItem('td:reactPlayer')).toBeNull());
    expect(localStorage.getItem('td:nowplaying')).toBeNull();
  });

  it('wires Media Session notification actions to the active audio element', async () => {
    const { handlers, mediaSession } = installMediaSession();
    const play = vi.mocked(HTMLMediaElement.prototype.play);
    const pause = vi.mocked(HTMLMediaElement.prototype.pause);
    render(<AudioHarness />);

    fireEvent.click(screen.getByText('Start'));

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    await waitFor(() => expect(handlers.get('play')).toBeTruthy());
    expect(mediaSession.metadata).toMatchObject({ title: 'Theme', artist: 'Composer', album: 'Album' });
    expect(mediaSession.playbackState).toBe('playing');

    pause.mockClear();
    handlers.get('pause')?.({ action: 'pause' });
    expect(pause).toHaveBeenCalledTimes(1);
    expect(mediaSession.playbackState).toBe('paused');

    play.mockClear();
    handlers.get('play')?.({ action: 'play' });
    expect(play).toHaveBeenCalledTimes(1);
    expect(mediaSession.playbackState).toBe('playing');

    handlers.get('seekto')?.({ action: 'seekto', seekTime: 42 });
    expect(primary.currentTime).toBe(42);
    expect(mediaSession.setPositionState).toHaveBeenLastCalledWith(expect.objectContaining({ position: 42 }));

    handlers.get('seekbackward')?.({ action: 'seekbackward', seekOffset: 12 });
    expect(primary.currentTime).toBe(30);
    handlers.get('seekforward')?.({ action: 'seekforward', seekOffset: 5 });
    expect(primary.currentTime).toBe(35);
  });

  it('uses Media Session next and previous actions for queue navigation', async () => {
    const { handlers } = installMediaSession();
    const first = makeTrack();
    const second = makeTrack({
      key: 'second-key',
      itemId: 'item-second-key',
      messageId: 2,
      title: 'Second Theme',
      streamHref: '/stream/second-key',
      watchKey: 'second-key',
      appHref: '/app/watch/second-key',
      classicHref: '/watch/second-key',
    });

    render(<AudioHarness track={first} queue={[first, second]} />);

    fireEvent.click(screen.getByText('Start'));
    await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Theme'));

    handlers.get('nexttrack')?.({ action: 'nexttrack' });
    await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Second Theme'));

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    primary.currentTime = 5;
    handlers.get('previoustrack')?.({ action: 'previoustrack' });
    expect(primary.currentTime).toBe(0);

    handlers.get('previoustrack')?.({ action: 'previoustrack' });
    await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Theme'));
  });
});
