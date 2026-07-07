import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deleteContinueEntry, fetchAudioTracks, fetchRating, fetchSubtitles, fetchWatch, recordWatchHistory, saveContinueEntry, setRating } from '../api';
import type { AudioPlayerHandle, PlayerState } from '../hooks/audio';
import type { AudioTrackOption, SubtitleTrack, VideoChoice, WatchTrack, WatchVideo } from '../types';
import { STILL_WATCHING_TIMEOUT_MS, WatchPage } from './watch';

vi.mock('../api', () => ({
  fetchAudioTracks: vi.fn(),
  deleteContinueEntry: vi.fn(),
  fetchRating: vi.fn(),
  fetchSubtitles: vi.fn(),
  fetchWatch: vi.fn(),
  recordWatchHistory: vi.fn(),
  saveContinueEntry: vi.fn(),
  setRating: vi.fn(),
}));

const fetchWatchMock = vi.mocked(fetchWatch);
const fetchSubtitlesMock = vi.mocked(fetchSubtitles);
const fetchAudioTracksMock = vi.mocked(fetchAudioTracks);
const fetchRatingMock = vi.mocked(fetchRating);
const saveContinueEntryMock = vi.mocked(saveContinueEntry);
const deleteContinueEntryMock = vi.mocked(deleteContinueEntry);
const recordWatchHistoryMock = vi.mocked(recordWatchHistory);
const setRatingMock = vi.mocked(setRating);

function makeAudio(playerOverrides?: Partial<PlayerState>): AudioPlayerHandle {
  return {
    audioRef: { current: null },
    bufferRef: { current: null },
    player: { ...emptyPlayer, ...playerOverrides },
    playTrack: vi.fn(),
    playRelative: vi.fn(),
    playQueueIndex: vi.fn(),
    addToQueue: vi.fn(),
    removeFromQueue: vi.fn(),
    clearQueue: vi.fn(),
    moveQueueItem: vi.fn(),
    shuffleQueue: vi.fn(),
    togglePlayback: vi.fn(),
    seek: vi.fn(),
    setSpeed: vi.fn(),
    cycleRepeatMode: vi.fn(),
    setVolume: vi.fn(),
    toggleMute: vi.fn(),
    confirmNext: vi.fn(),
    cancelNext: vi.fn(),
    dismissPlayer: vi.fn(),
  };
}

const emptyPlayer: PlayerState = {
  track: null,
  queue: [],
  queueIndex: -1,
  playing: false,
  currentTime: 0,
  duration: 0,
  error: '',
  speed: 1,
  repeatMode: 'off',
  volume: 1,
  muted: false,
  nextTrack: null,
  nextCountdown: 5,
  queueToast: '',
};

function makeVideo(overrides: Partial<WatchVideo> = {}): WatchVideo {
  return {
    key: 'video-key',
    itemId: 'item-video-key',
    messageId: 42,
    secureHash: 'hash',
    type: 'video',
    title: 'Pilot',
    subtitle: 'S01E01',
    year: 2026,
    mediaKind: 'series',
    posterUrl: '/thumb/video-key.jpg',
    thumbUrl: '/thumb/video-key.jpg',
    backdropUrl: '/thumb/video-key-backdrop.jpg',
    duration: 120,
    durationLabel: '2:00',
    fileSize: 1000,
    fileSizeLabel: '1 KB',
    quality: '1080p',
    genres: [],
    tags: [],
    overview: 'Episode overview',
    artist: '',
    albumTitle: '',
    href: '/watch/video-key',
    streamHref: '/stream/video-key',
    watchKey: 'video-key',
    episodeLabel: 'S01E01',
    classicHref: '/watch/video-key',
    appHref: '/app/watch/video-key',
    directSrc: '/stream/video-key',
    hlsSrc: '/hls/video-key/master.m3u8',
    subtitleBase: '/sub/video-key',
    audioTrackBase: '/hls/video-key',
    absoluteStreamHref: 'https://example.test/stream/video-key',
    downloadHref: '/download/video-key',
    vlcHref: 'vlc://stream/video-key',
    vlcTrackingToken: '',
    knownUnplayable: false,
    videoCodec: 'h264',
    pixFmt: 'yuv420p',
    qualityVariants: [],
    nextEpisode: {
      key: 'video-key-2',
      url: '/watch/video-key-2',
      title: 'Next episode',
      season: 1,
      episode: 2,
      playHref: '/app/watch/video-key-2',
      classicHref: '/watch/video-key-2',
      posterUrl: '/thumb/video-key-2.jpg',
    },
    introStart: 0,
    introEnd: 0,
    recapStart: 0,
    recapEnd: 0,
    chapters: [],
    resumeKey: 'video-key',
    metadata: {
      title: 'Pilot',
      year: 2026,
      overview: 'Episode overview',
      posterUrl: '/thumb/video-key.jpg',
      thumbUrl: '/thumb/video-key.jpg',
      backdropUrl: '/thumb/video-key-backdrop.jpg',
      genres: [],
      director: '',
      directors: [],
      cast: [],
      imdbId: '',
      imdbHref: '',
      trailerKey: '',
    },
    ...overrides,
  } as WatchVideo;
}

function makeVideoChoice(overrides: Partial<VideoChoice> = {}): VideoChoice {
  const base = makeVideo({
    key: 'video-key-720',
    itemId: 'item-video-key-720',
    title: 'Pilot 720p',
    quality: '720p',
    appHref: '/app/watch/video-key-720',
    classicHref: '/watch/video-key-720',
  });
  return {
    ...base,
    type: 'movie',
    label: '720p',
    playHref: '/app/watch/video-key-720',
    detailsHref: '/app/movie/pilot',
    aspect: 'poster',
    ...overrides,
  } as unknown as VideoChoice;
}

function makeTrack(overrides: Partial<WatchTrack> = {}): WatchTrack {
  return {
    key: 'track-key',
    itemId: 'item-track-key',
    type: 'track',
    messageId: 7,
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
  };
}

function renderWatchPage(video = makeVideo()) {
  fetchWatchMock.mockResolvedValue({
    mediaKind: 'video',
    item: video,
  });

  return render(
    <WatchPage
      watchKey={video.key}
      audio={makeAudio()}
      onOpenQueue={vi.fn()}
    />,
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
  localStorage.clear();
  fetchSubtitlesMock.mockResolvedValue([]);
  fetchAudioTracksMock.mockResolvedValue([]);
  fetchRatingMock.mockResolvedValue({ rating: null, counts: { up: 0, down: 0 } });
  saveContinueEntryMock.mockResolvedValue(undefined);
  deleteContinueEntryMock.mockResolvedValue(undefined);
  recordWatchHistoryMock.mockResolvedValue(undefined);
  setRatingMock.mockResolvedValue({ rating: null, counts: { up: 0, down: 0 } });
});

afterEach(() => {
  Reflect.deleteProperty(navigator, 'mediaSession');
  Reflect.deleteProperty(window, 'MediaMetadata');
  Reflect.deleteProperty(window, 'Hls');
});

describe('WatchPage video player', () => {
  it('persists the autoplay-next preference from the player menu', async () => {
    localStorage.setItem('td:videoAutoplay', '0');
    renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    fireEvent.click(screen.getByLabelText('More video options'));

    const autoplayToggle = screen.getByRole('menuitemcheckbox', { name: /Autoplay next/i });
    expect(autoplayToggle.getAttribute('aria-checked')).toBe('false');
    expect(autoplayToggle.textContent).toContain('Off');

    fireEvent.click(autoplayToggle);

    await waitFor(() => expect(localStorage.getItem('td:videoAutoplay')).toBe('1'));
    expect(screen.getByRole('menuitemcheckbox', { name: /Autoplay next/i }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('menuitemcheckbox', { name: /Autoplay next/i }).textContent).toContain('On');
  });

  it('keeps video event handlers after switching between HLS audio tracks', async () => {
    const audioTracks: AudioTrackOption[] = [
      { index: 1, language: 'eng', label: 'English', codec: 'aac' },
      { index: 2, language: 'tam', label: 'Tamil', codec: 'aac' },
    ];
    fetchAudioTracksMock.mockResolvedValue(audioTracks);
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    fireEvent.click(screen.getByLabelText('More video options'));
    const audioSelect = await screen.findByLabelText('Audio track');

    fireEvent.change(audioSelect, { target: { value: '1' } });
    await waitFor(() => {
      expect(view.container.querySelector('video')?.getAttribute('src')).toBe('/hls/video-key/master.m3u8?a=1');
    });

    fireEvent.change(audioSelect, { target: { value: '2' } });
    await waitFor(() => {
      expect(view.container.querySelector('video')?.getAttribute('src')).toBe('/hls/video-key/master.m3u8?a=2');
    });

    fireEvent.ended(view.container.querySelector('video') as HTMLVideoElement);

    const panel = await screen.findByRole('dialog', { name: 'Next episode' });
    expect(within(panel).getByText('Playing next in 8s')).toBeTruthy();
    expect(within(panel).getAllByText('S01E02')).toHaveLength(2);
    expect(within(panel).getByRole('timer', { name: '8 seconds until next episode' })).toBeTruthy();
    expect(within(panel).getByRole('link', { name: 'Play now' }).getAttribute('href')).toBe('/app/watch/video-key-2');
  });

  it('lets users pause autoplay and replay from the next episode panel', async () => {
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    const video = view.container.querySelector('video') as HTMLVideoElement;
    video.currentTime = 120;

    fireEvent.ended(video);
    const panel = await screen.findByRole('dialog', { name: 'Next episode' });

    fireEvent.click(within(panel).getByRole('checkbox', { name: 'Autoplay' }));

    await waitFor(() => expect(localStorage.getItem('td:videoAutoplay')).toBe('0'));
    expect(within(panel).getByText('Up next')).toBeTruthy();
    expect(within(panel).getByRole('timer', { name: 'Autoplay paused' })).toBeTruthy();

    fireEvent.click(within(panel).getByRole('button', { name: 'Replay' }));

    expect(video.currentTime).toBe(0);
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: 'Next episode' })).toBeNull();
  });

  it('hides HLS-only controls when the API omits an HLS source', async () => {
    renderWatchPage(makeVideo({ hlsSrc: '', audioTrackBase: '' }));

    await screen.findByRole('heading', { name: 'Pilot' });
    expect(fetchAudioTracksMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText('More video options'));

    expect(screen.queryByLabelText('Audio track')).toBeNull();
    expect(screen.queryByText('Source')).toBeNull();
  });

  it('does not duplicate the default audio track option', async () => {
    fetchAudioTracksMock.mockResolvedValue([
      { index: 0, language: 'en', label: 'English', codec: 'aac' },
      { index: 1, language: 'ta', label: 'Tamil', codec: 'aac' },
    ]);
    renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    fireEvent.click(screen.getByLabelText('More video options'));
    const audioSelect = await screen.findByLabelText('Audio track');
    const options = within(audioSelect).getAllByRole('option') as HTMLOptionElement[];

    expect(options.map((option) => option.value)).toEqual(['0', '1']);
    expect(options.map((option) => option.textContent)).toEqual(['English', 'Tamil']);
  });

  it('falls back to direct playback without showing the error overlay when HLS fails fatally', async () => {
    class MockHls {
      static Events = { ERROR: 'error', MANIFEST_PARSED: 'manifest' };
      static isSupported = () => true;
      private handlers = new Map<string, (...args: unknown[]) => void>();

      on(event: string, handler: (...args: unknown[]) => void) {
        this.handlers.set(event, handler);
      }

      off() {}

      loadSource() {
        this.handlers.get(MockHls.Events.ERROR)?.(MockHls.Events.ERROR, { fatal: true });
      }

      attachMedia() {}

      destroy() {}
    }
    Object.defineProperty(window, 'Hls', {
      configurable: true,
      value: MockHls,
    });
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    fireEvent.click(screen.getByLabelText('More video options'));
    fireEvent.click(screen.getByRole('menuitem', { name: /Source/i }));

    await waitFor(() => expect(view.container.querySelector('video')?.getAttribute('src')).toBe('/stream/video-key'));
    expect(screen.queryByText('This video needs another player')).toBeNull();
  });

  it('shows a fallback overlay when playback advances without decoded video frames', async () => {
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    vi.useFakeTimers();
    const video = view.container.querySelector('video') as HTMLVideoElement;
    Object.defineProperty(video, 'paused', {
      configurable: true,
      get: () => false,
    });
    Object.defineProperty(video, 'videoWidth', {
      configurable: true,
      get: () => 0,
    });
    video.currentTime = 2;

    fireEvent.playing(video);
    act(() => {
      vi.advanceTimersByTime(4000);
    });

    expect(screen.getByText('This video needs another player')).toBeTruthy();
    expect(screen.getByText(/no video frames decoded/i)).toBeTruthy();
  });

  it('falls back to native WebKit fullscreen when container fullscreen is rejected', async () => {
    const requestFullscreen = vi.fn().mockRejectedValue(new Error('blocked'));
    Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', {
      configurable: true,
      value: requestFullscreen,
    });
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    const video = view.container.querySelector('video') as HTMLVideoElement & {
      webkitEnterFullscreen?: () => void;
    };
    const webkitEnterFullscreen = vi.fn();
    Object.defineProperty(video, 'webkitEnterFullscreen', {
      configurable: true,
      value: webkitEnterFullscreen,
    });

    fireEvent.click(screen.getByLabelText('Fullscreen'));

    await waitFor(() => expect(requestFullscreen).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(webkitEnterFullscreen).toHaveBeenCalledTimes(1));
  });

  it('supports visible skip controls and mute in the video controls', async () => {
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    const video = view.container.querySelector('video') as HTMLVideoElement;

    fireEvent.click(screen.getByLabelText('Forward 10 seconds'));
    expect(video.currentTime).toBe(10);

    fireEvent.click(screen.getByLabelText('Rewind 10 seconds'));
    expect(video.currentTime).toBe(0);

    fireEvent.click(screen.getByLabelText('Mute'));
    await waitFor(() => expect(video.muted).toBe(true));
    expect(screen.getByLabelText('Unmute')).toBeTruthy();
  });

  it('shows skip recap during the recap window and jumps to recap end', async () => {
    const view = renderWatchPage(makeVideo({ recapStart: 30, recapEnd: 45 }));

    await screen.findByRole('heading', { name: 'Pilot' });
    const video = view.container.querySelector('video') as HTMLVideoElement;
    video.currentTime = 32;
    fireEvent.timeUpdate(video);

    fireEvent.click(await screen.findByRole('button', { name: /Skip recap/i }));

    expect(video.currentTime).toBe(45);
  });

  it('renders chapter markers and seeks from chapter buttons', async () => {
    const view = renderWatchPage(makeVideo({
      chapters: [
        { start: 0, title: 'Opening' },
        { start: 75, title: 'First turn' },
      ],
    }));

    await screen.findByRole('heading', { name: 'Pilot' });
    const video = view.container.querySelector('video') as HTMLVideoElement;

    expect(view.container.querySelectorAll('.video-chapter-markers span')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /First turn/i }));

    expect(video.currentTime).toBe(75);
  });

  it('toggles playback when clicking the video surface', async () => {
    const play = vi.mocked(HTMLMediaElement.prototype.play);
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    const shell = view.container.querySelector('.video-shell') as HTMLElement;
    vi.useFakeTimers();

    fireEvent.click(shell);
    expect(play).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(280);
    });

    expect(play).toHaveBeenCalledTimes(1);
  });

  it('seeks on video surface double-click without triggering the single-click toggle', async () => {
    const play = vi.mocked(HTMLMediaElement.prototype.play);
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    const shell = view.container.querySelector('.video-shell') as HTMLElement;
    const video = view.container.querySelector('video') as HTMLVideoElement;
    Object.defineProperty(shell, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 0, top: 0, width: 200, height: 112, right: 200, bottom: 112, x: 0, y: 0, toJSON: () => ({}) }),
    });
    video.currentTime = 5;
    vi.useFakeTimers();

    fireEvent.click(shell, { detail: 1, clientX: 150 });
    fireEvent.dblClick(shell, { clientX: 150 });

    expect(video.currentTime).toBe(15);

    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(play).not.toHaveBeenCalled();
  });

  it('wires Media Session controls to the video player', async () => {
    const { handlers, mediaSession } = installMediaSession();
    const play = vi.mocked(HTMLMediaElement.prototype.play);
    const pause = vi.mocked(HTMLMediaElement.prototype.pause);
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    const video = view.container.querySelector('video') as HTMLVideoElement;
    await waitFor(() => expect(handlers.get('play')).toBeTruthy());
    expect(mediaSession.metadata).toMatchObject({ title: 'Pilot', artist: 'series', album: 'S01E01' });
    expect(mediaSession.playbackState).toBe('paused');

    handlers.get('play')?.({ action: 'play' });
    expect(play).toHaveBeenCalledTimes(1);
    expect(mediaSession.playbackState).toBe('playing');

    handlers.get('seekto')?.({ action: 'seekto', seekTime: 42 });
    expect(video.currentTime).toBe(42);
    expect(mediaSession.setPositionState).toHaveBeenLastCalledWith(expect.objectContaining({ position: 42 }));

    handlers.get('seekbackward')?.({ action: 'seekbackward', seekOffset: 12 });
    expect(video.currentTime).toBe(30);
    handlers.get('seekforward')?.({ action: 'seekforward', seekOffset: 5 });
    expect(video.currentTime).toBe(35);

    handlers.get('pause')?.({ action: 'pause' });
    expect(pause).toHaveBeenCalledTimes(1);
    expect(mediaSession.playbackState).toBe('paused');
  });

  it('handles keyboard media keys in the video player', async () => {
    const play = vi.mocked(HTMLMediaElement.prototype.play);
    const pause = vi.mocked(HTMLMediaElement.prototype.pause);
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    const video = view.container.querySelector('video') as HTMLVideoElement;
    video.currentTime = 20;

    fireEvent.keyDown(window, { key: 'MediaPlay' });
    expect(play).toHaveBeenCalledTimes(1);

    const optionsButton = screen.getByLabelText('More video options');
    optionsButton.focus();
    fireEvent.keyDown(optionsButton, { key: 'MediaPause' });
    expect(pause).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: 'MediaTrackPrevious' });
    expect(video.currentTime).toBe(0);
  });

  it('hides video controls during playback and reveals them on pointer movement', async () => {
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    vi.useFakeTimers();
    const shell = view.container.querySelector('.video-shell') as HTMLElement;
    const video = view.container.querySelector('video') as HTMLVideoElement;

    fireEvent.play(video);
    expect(shell.className).toContain('controls-visible');
    await act(async () => {});
    act(() => {
      vi.advanceTimersByTime(2300);
    });
    expect(shell.className).toContain('controls-hidden');

    fireEvent.pointerMove(shell);

    expect(shell.className).toContain('controls-visible');
  });

  it('pauses unattended playback and prompts to keep watching', async () => {
    const play = vi.mocked(HTMLMediaElement.prototype.play);
    const pause = vi.mocked(HTMLMediaElement.prototype.pause);
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    vi.useFakeTimers();
    const video = view.container.querySelector('video') as HTMLVideoElement;

    fireEvent.play(video);
    await act(async () => {});
    act(() => {
      vi.advanceTimersByTime(STILL_WATCHING_TIMEOUT_MS);
    });

    expect(pause).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('dialog', { name: /Still watching/i })).toBeTruthy();
    expect(screen.getByText('We paused the stream to save bandwidth.')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /Keep watching/i }));

    expect(play).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog', { name: /Still watching/i })).toBeNull();
  });

  it('toggles captions from the visible fullscreen-safe controls', async () => {
    const subtitles: SubtitleTrack[] = [
      { id: 'eng', url: '/sub/video-key/en.vtt', language: 'en', label: 'English', codec: 'vtt', kind: 'subtitles' },
    ];
    fetchSubtitlesMock.mockResolvedValue(subtitles);
    renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    const captionsButton = await screen.findByLabelText('Turn captions on') as HTMLButtonElement;
    await waitFor(() => expect(captionsButton.disabled).toBe(false));

    fireEvent.click(captionsButton);

    expect(screen.getByLabelText('Turn captions off')).toBeTruthy();
    expect(screen.getAllByText('English').length).toBeGreaterThan(0);
  });

  it('keeps volume and quality variants available inside the video menu', async () => {
    const view = renderWatchPage(makeVideo({ qualityVariants: [makeVideoChoice()] }));

    await screen.findByRole('heading', { name: 'Pilot' });
    const video = view.container.querySelector('video') as HTMLVideoElement;
    fireEvent.click(screen.getByLabelText('More video options'));

    fireEvent.change(screen.getByLabelText('Video volume'), { target: { value: '0.4' } });
    await waitFor(() => expect(video.volume).toBe(0.4));

    expect(screen.getByRole('menuitem', { name: /720pOpen/i }).getAttribute('href')).toBe('/app/watch/video-key-720');
  });

  it('does not repeat the quality as a subtitle in the video titlebar', async () => {
    const view = renderWatchPage(makeVideo({ quality: '720p', subtitle: '720p' }));

    await screen.findByRole('heading', { name: 'Pilot' });

    const titlebar = view.container.querySelector('.video-titlebar') as HTMLElement;
    expect(titlebar.firstElementChild?.querySelector('h1')?.textContent).toBe('Pilot');
    expect(titlebar.lastElementChild?.textContent).toContain('Classic player');
    expect(view.container.querySelector('.video-titlebar p:not(.eyebrow)')).toBeNull();
    expect(view.container.querySelector('.video-topbar-copy span')).toBeNull();
  });

  it('shows movie and series context below the video player', async () => {
    renderWatchPage(makeVideo({
      genres: ['Drama'],
      metadata: {
        ...makeVideo().metadata,
        title: 'The Pilot',
        year: 2026,
        overview: 'A broader show overview',
        genres: ['Sci-Fi'],
        directors: [{ name: 'Jane Director', href: '/app/person/jane-director' }],
        cast: [
          { name: 'Lead Actor', href: '/app/person/lead-actor' },
          { name: 'Supporting Actor', href: '/app/person/supporting-actor' },
        ],
        imdbId: 'tt1234567',
        imdbHref: 'https://www.imdb.com/title/tt1234567/',
      },
    }));

    await screen.findByRole('heading', { name: 'Pilot' });

    const info = within(screen.getByLabelText('Movie and series information'));
    expect(screen.getByRole('heading', { name: 'The Pilot' })).toBeTruthy();
    expect(info.getByText('Episode overview')).toBeTruthy();
    expect(info.getByText('S01E01')).toBeTruthy();
    expect(info.getByText('Drama')).toBeTruthy();
    expect(info.getByRole('link', { name: 'Jane Director' }).getAttribute('href')).toBe('/app/person/jane-director');
    expect(info.getByRole('link', { name: 'Lead Actor' }).getAttribute('href')).toBe('/app/person/lead-actor');
    expect(info.getByRole('link', { name: 'IMDb' }).getAttribute('href')).toBe('https://www.imdb.com/title/tt1234567/');
  });

  it('uses a neutral overview heading instead of repeating the generic about label', async () => {
    renderWatchPage(makeVideo({
      title: 'Chaotic Family',
      subtitle: '480p',
      episodeLabel: '',
      mediaKind: 'movie',
      metadata: {
        ...makeVideo().metadata,
        title: 'Chaotic Family',
        overview: 'A dark comedy centered around a chaotic family.',
      },
    }));

    await screen.findByRole('heading', { name: 'Chaotic Family' });

    const info = within(screen.getByLabelText('Movie and series information'));
    expect(info.getByText('About this title')).toBeTruthy();
    expect(info.getByRole('heading', { name: 'Overview' })).toBeTruthy();
    expect(info.queryByRole('heading', { name: 'About this title' })).toBeNull();
  });

  it('shows uploaded subtitle status only inside the options menu', async () => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:subtitle'),
    });
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    fireEvent.click(screen.getByLabelText('More video options'));
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello'], 'custom.vtt', { type: 'text/vtt' });

    fireEvent.change(input, { target: { files: [file] } });

    const status = await screen.findByRole('status');
    expect(status.textContent).toBe('Loaded "custom.vtt" as subtitles.');
    expect(status.className).toBe('video-menu-status');
    expect(view.container.querySelector('.subtitle-status')).toBeNull();
  });

  it('supports keyboard shortcuts for video seeking and mute', async () => {
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    const video = view.container.querySelector('video') as HTMLVideoElement;
    await act(async () => {});

    fireEvent.keyDown(window, { key: 'ArrowRight' });
    expect(video.currentTime).toBe(10);

    fireEvent.keyDown(window, { key: 'ArrowLeft' });
    expect(video.currentTime).toBe(0);

    fireEvent.keyDown(window, { key: 'm' });
    await waitFor(() => expect(video.muted).toBe(true));
  });

  it('applies playback speed changes from the video controls', async () => {
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    const video = view.container.querySelector('video') as HTMLVideoElement;

    fireEvent.click(screen.getByLabelText('More video options'));
    fireEvent.change(screen.getByLabelText('Playback speed'), { target: { value: '1.5' } });

    await waitFor(() => expect(video.playbackRate).toBe(1.5));
  });
});

describe('WatchPage audio player', () => {
  it('keeps queue available from the audio watch page even for one track', async () => {
    const track = makeTrack();
    const onOpenQueue = vi.fn();
    fetchWatchMock.mockResolvedValue({
      mediaKind: 'music',
      item: track,
      albumTracks: [track],
    });

    render(
      <WatchPage
        watchKey={track.key}
        audio={makeAudio({ track, queue: [track], queueIndex: 0 })}
        onOpenQueue={onOpenQueue}
      />,
    );

    await screen.findByRole('heading', { name: 'Theme', level: 1 });
    const queueButton = screen.getByLabelText('Open queue') as HTMLButtonElement;
    expect(queueButton.disabled).toBe(false);

    fireEvent.click(queueButton);
    expect(onOpenQueue).toHaveBeenCalledTimes(1);
  });
});
