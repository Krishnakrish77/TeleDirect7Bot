import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deleteContinueEntry, recordWatchHistory, reportAudioDuration, saveContinueEntry } from '../api';
import { describeAudioPlaybackFailure, RESTORE_AUDIO_MEDIA_SESSION_EVENT, useAudioPlayer } from './audio';
import { clearLyricsCache } from './lyrics';
import type { WatchTrack } from '../types';

vi.mock('../api', () => ({
  deleteContinueEntry: vi.fn().mockResolvedValue(undefined),
  recordWatchHistory: vi.fn().mockResolvedValue(undefined),
  reportAudioDuration: vi.fn().mockResolvedValue(undefined),
  saveContinueEntry: vi.fn().mockResolvedValue(true),
}));

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
    sourceType: '',
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
      <button type="button" onClick={() => audio.playRelative(1)}>Next</button>
      <button type="button" onClick={() => audio.dismissPlayer()}>Dismiss</button>
      <span data-testid="track">{audio.player.track?.title || 'none'}</span>
      <span data-testid="muted">{String(audio.player.muted)}</span>
      <span data-testid="volume">{audio.player.volume}</span>
      <span data-testid="error">{audio.player.error}</span>
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

beforeEach(() => {
  clearLyricsCache();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: false,
    json: async () => ({}),
  }));
});

afterEach(() => {
  clearLyricsCache();
  Reflect.deleteProperty(navigator, 'mediaSession');
  Reflect.deleteProperty(window, 'MediaMetadata');
  vi.unstubAllGlobals();
});

describe('useAudioPlayer', () => {
  it('removes the legacy persisted playback-speed preference', () => {
    localStorage.setItem('td:speed', '0.75');

    render(<AudioHarness />);

    expect(screen.getByTestId('volume').textContent).toBe('1');
    expect(localStorage.getItem('td:speed')).toBeNull();
  });

  it('finalises a near-complete track without waiting for the ended event', async () => {
    render(<AudioHarness />);

    fireEvent.click(screen.getByText('Start'));

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    Object.defineProperty(primary, 'duration', { configurable: true, value: 200 });
    Object.defineProperty(primary, 'paused', { configurable: true, value: false });

    // 96% — past the completion ratio; a tab closed here used to leave the
    // entry (which never auto-resumes for songs) stuck on the CW shelf.
    act(() => {
      fireEvent.timeUpdate(primary);
      Object.defineProperty(primary, 'currentTime', { configurable: true, value: 192 });
      fireEvent.timeUpdate(primary);
    });

    await waitFor(() => expect(recordWatchHistory).toHaveBeenCalledWith('track-key', 'Theme'));
    await waitFor(() => expect(deleteContinueEntry).toHaveBeenCalledWith('track-key'));
    expect(JSON.parse(localStorage.getItem('td:cw') || '{}')['track-key']).toBeUndefined();
  });

  it('records only one completion per play when the ended event follows the 95% path', async () => {
    render(<AudioHarness />);

    fireEvent.click(screen.getByText('Start'));

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    Object.defineProperty(primary, 'duration', { configurable: true, value: 200 });
    Object.defineProperty(primary, 'paused', { configurable: true, value: false });

    act(() => {
      Object.defineProperty(primary, 'currentTime', { configurable: true, value: 196 });
      fireEvent.timeUpdate(primary);
      fireEvent.ended(primary);
    });

    await waitFor(() => expect(recordWatchHistory).toHaveBeenCalledTimes(1));
    expect(deleteContinueEntry).toHaveBeenCalledTimes(1);
  });

  it('explains browser playback failures with actionable messages', () => {
    const track = makeTrack({ format: 'FLAC', qualityLabel: 'FLAC' });
    const audio = document.createElement('audio');

    Object.defineProperty(audio, 'error', {
      configurable: true,
      value: { code: 4 },
    });

    expect(describeAudioPlaybackFailure({ name: 'NotAllowedError' }, null, track)).toBe('Tap play to start audio.');
    expect(describeAudioPlaybackFailure({ name: 'NotSupportedError' }, null, track)).toContain('FLAC stream');
    expect(describeAudioPlaybackFailure(undefined, audio, track)).toContain('FLAC stream');
  });

  it('surfaces play promise failures in the player state', async () => {
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValueOnce({ name: 'NotSupportedError' });

    render(<AudioHarness />);

    fireEvent.click(screen.getByText('Start'));

    await waitFor(() => expect(screen.getByTestId('error').textContent).toContain('MP3 stream'));
  });

  it('surfaces native media loading errors in the player state', async () => {
    render(<AudioHarness />);

    fireEvent.click(screen.getByText('Start'));

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    Object.defineProperty(primary, 'error', {
      configurable: true,
      value: { code: 2 },
    });
    fireEvent.error(primary);

    await waitFor(() => expect(screen.getByTestId('error').textContent).toContain('Network issue'));
  });

  it('reloads the audio source when retrying after a native media error', async () => {
    const load = vi.mocked(HTMLMediaElement.prototype.load);

    render(<AudioHarness />);

    fireEvent.click(screen.getByText('Start'));

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    Object.defineProperty(primary, 'error', {
      configurable: true,
      value: { code: 2 },
    });
    Object.defineProperty(primary, 'paused', { configurable: true, value: true });
    fireEvent.error(primary);
    await waitFor(() => expect(screen.getByTestId('error').textContent).toContain('Network issue'));

    load.mockClear();
    fireEvent.click(screen.getByText('Toggle'));

    await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  });

  it('reloads an errored audio source when Media Session play retries', async () => {
    const { handlers } = installMediaSession();
    const load = vi.mocked(HTMLMediaElement.prototype.load);

    render(<AudioHarness />);

    fireEvent.click(screen.getByText('Start'));
    await waitFor(() => expect(handlers.get('play')).toBeTruthy());

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    Object.defineProperty(primary, 'error', {
      configurable: true,
      value: { code: 2 },
    });
    Object.defineProperty(primary, 'paused', { configurable: true, value: true });
    fireEvent.error(primary);
    await waitFor(() => expect(screen.getByTestId('error').textContent).toContain('Network issue'));

    load.mockClear();
    handlers.get('play')?.({ action: 'play' });

    await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  });

  it('ignores stale play promise failures after the user starts another track', async () => {
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
    let rejectFirstPlay: ((error: unknown) => void) | null = null;
    vi.mocked(HTMLMediaElement.prototype.play)
      .mockImplementationOnce(() => new Promise<void>((_resolve, reject) => { rejectFirstPlay = reject; }))
      .mockResolvedValue(undefined);

    render(<AudioHarness track={first} queue={[first, second]} />);

    fireEvent.click(screen.getByText('Start'));
    await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Theme'));

    fireEvent.click(screen.getByText('Next'));
    await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Second Theme'));

    await act(async () => {
      rejectFirstPlay?.({ name: 'AbortError' });
      await Promise.resolve();
    });

    expect(screen.getByTestId('track').textContent).toBe('Second Theme');
    expect(screen.getByTestId('error').textContent).toBe('');
  });

  it('times out a stuck playback start with a retryable failure state', async () => {
    vi.useFakeTimers();
    const pause = vi.mocked(HTMLMediaElement.prototype.pause);

    render(<AudioHarness />);

    fireEvent.click(screen.getByText('Start'));

    const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
    Object.defineProperty(primary, 'paused', { configurable: true, value: false });

    act(() => {
      vi.advanceTimersByTime(12000);
    });

    expect(pause).toHaveBeenCalled();
    expect(screen.getByTestId('error').textContent).toContain('Still waiting');
  });

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

  it('hands off to the preloaded element while still audible in a hidden tab', async () => {
    const play = vi.mocked(HTMLMediaElement.prototype.play);
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    try {
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
      await waitFor(() => expect(play).toHaveBeenCalledTimes(1));

      const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
      const buffer = screen.getByTestId('buffer-audio') as HTMLAudioElement;
      Object.defineProperty(primary, 'duration', { configurable: true, value: 200 });
      Object.defineProperty(primary, 'paused', { configurable: true, value: false });

      // Entering the 3s window in a hidden tab starts the PRELOADED second
      // element underneath the still-playing first one (tab never goes
      // silent, so the browser keep-alive holds)...
      act(() => {
        Object.defineProperty(primary, 'currentTime', { configurable: true, value: 199, writable: true });
        fireEvent.timeUpdate(primary);
      });
      await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Second Theme'));
      expect(buffer.getAttribute('src')).toContain('/stream/second-key');
      expect(play).toHaveBeenCalledTimes(2);
      // ...and the outgoing element is NOT paused by the handoff itself.
      expect(primary.getAttribute('src')).toContain('/stream/track-key');
    } finally {
      hidden.mockRestore();
    }
  });

  it('pauses the keep-alive element once the handoff element shows progress', async () => {
    const pause = vi.mocked(HTMLMediaElement.prototype.pause);
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    try {
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

      const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
      const buffer = screen.getByTestId('buffer-audio') as HTMLAudioElement;
      Object.defineProperty(primary, 'duration', { configurable: true, value: 200 });
      Object.defineProperty(primary, 'paused', { configurable: true, value: false });

      act(() => {
        Object.defineProperty(primary, 'currentTime', { configurable: true, value: 199, writable: true });
        fireEvent.timeUpdate(primary);
      });
      await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Second Theme'));
      pause.mockClear();

      // The new element proves audible progress; the keep-alive goes silent
      // only now.
      act(() => {
        Object.defineProperty(buffer, 'currentTime', { configurable: true, value: 0.5, writable: true });
        fireEvent.timeUpdate(buffer);
      });

      await waitFor(() => expect(pause).toHaveBeenCalledWith());
      expect(primary.getAttribute('src')).toBeNull();
    } finally {
      hidden.mockRestore();
    }
  });

  it('discards a hidden handoff whose play() resolves after the queue already advanced', async () => {
    const play = vi.mocked(HTMLMediaElement.prototype.play);
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    try {
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
      const third = makeTrack({
        key: 'third-key',
        itemId: 'item-third-key',
        messageId: 3,
        title: 'Third Theme',
        streamHref: '/stream/third-key',
        watchKey: 'third-key',
        appHref: '/app/watch/third-key',
        classicHref: '/watch/third-key',
      });
      // Keep the handoff play() pending so the stale resolution can be
      // delivered after the queue advanced by another path.
      let resolveSecondPlay: (() => void) | null = null;
      play
        .mockResolvedValueOnce(undefined) // start track 1
        .mockImplementationOnce(() => new Promise<void>((resolve) => { resolveSecondPlay = resolve; })) // handoff, pending
        .mockResolvedValue(undefined); // advance after from ended

      render(<AudioHarness track={first} queue={[first, second, third]} />);

      fireEvent.click(screen.getByText('Start'));
      await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Theme'));

      const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
      const buffer = screen.getByTestId('buffer-audio') as HTMLAudioElement;
      Object.defineProperty(primary, 'duration', { configurable: true, value: 200 });
      Object.defineProperty(primary, 'paused', { configurable: true, value: false });

      // Arm the hidden handoff: play() on the second element stays pending.
      act(() => {
        Object.defineProperty(primary, 'currentTime', { configurable: true, value: 199, writable: true });
        fireEvent.timeUpdate(primary);
      });
      await waitFor(() => expect(buffer.getAttribute('src')).toContain('/stream/second-key'));
      expect(screen.getByTestId('track').textContent).toBe('Theme');

      // The first element reaches its natural end before the promise resolves:
      // onEnded advances to track 2 via the same element (the Android path).
      // Plays so far: 1 = start track 1, 2 = pending handoff, 3 = track 2
      // restarted on the primary element.
      Object.defineProperty(primary, 'paused', { configurable: true, value: true });
      fireEvent.ended(primary);
      await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Second Theme'));
      expect(play).toHaveBeenCalledTimes(3);

      // NOW the stale handoff promise resolves — it must not steal the
      // active slot, restart track 2 in the orphaned element, or touch the
      // live primary element.
      await act(async () => { resolveSecondPlay?.(); });
      expect(play).toHaveBeenCalledTimes(3);
      await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Second Theme'));
      expect(screen.getByTestId('track').textContent).not.toBe('none');
      // The orphaned element was discarded, not adopted.
      expect(buffer.getAttribute('src')).toBeNull();
    } finally {
      hidden.mockRestore();
    }
  });

  it('recovers a queue that finished while the tab was hidden on return', async () => {
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    try {
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

      // The OS suspended the process before `ended` dispatched: track sits at
      // 100% with the queue unadvanced.
      const primary = screen.getByTestId('primary-audio') as HTMLAudioElement;
      Object.defineProperty(primary, 'duration', { configurable: true, value: 200 });
      Object.defineProperty(primary, 'currentTime', { configurable: true, value: 200, writable: true });
      act(() => { fireEvent.timeUpdate(primary); });

      hidden.mockReturnValue(false);
      act(() => {
        document.dispatchEvent(new Event('visibilitychange'));
      });

      await waitFor(() => expect(screen.getByTestId('track').textContent).toBe('Second Theme'));
    } finally {
      hidden.mockRestore();
    }
  });

  it('restores audio Media Session handlers after another player releases them', async () => {
    const { handlers, mediaSession } = installMediaSession();
    render(<AudioHarness />);

    await waitFor(() => expect(handlers.get('play')).toBeTruthy());
    mediaSession.setActionHandler('play', null);
    mediaSession.setActionHandler('pause', null);
    expect(handlers.get('play')).toBeUndefined();

    window.dispatchEvent(new Event(RESTORE_AUDIO_MEDIA_SESSION_EVENT));

    await waitFor(() => expect(handlers.get('play')).toBeTruthy());
    expect(handlers.get('pause')).toBeTruthy();
  });
});
