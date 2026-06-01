import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { deleteContinueEntry, fetchAudioTracks, fetchRating, fetchSubtitles, fetchWatch, recordWatchHistory, saveContinueEntry, setRating } from '../api';
import type { PlayerState } from '../hooks/audio';
import type { AudioTrackOption, SubtitleTrack, VideoChoice, WatchTrack, WatchVideo } from '../types';
import { WatchPage } from './watch';

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
      player={emptyPlayer}
      playTrack={vi.fn()}
      playRelative={vi.fn()}
      playQueueIndex={vi.fn()}
      addToQueue={vi.fn()}
      shuffleQueue={vi.fn()}
      togglePlayback={vi.fn()}
      seek={vi.fn()}
      setSpeed={vi.fn()}
      cycleRepeatMode={vi.fn()}
      setVolume={vi.fn()}
      toggleMute={vi.fn()}
      confirmNext={vi.fn()}
      cancelNext={vi.fn()}
      onOpenQueue={vi.fn()}
    />,
  );
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

    expect(await screen.findByText('Next episode')).toBeTruthy();
    expect(screen.getByText('Up next - 5s')).toBeTruthy();
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

    fireEvent.change(screen.getByLabelText('Volume'), { target: { value: '0.5' } });
    await waitFor(() => expect(video.muted).toBe(false));
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

  it('supports keyboard shortcuts for video seeking and mute', async () => {
    const view = renderWatchPage();

    await screen.findByRole('heading', { name: 'Pilot' });
    const video = view.container.querySelector('video') as HTMLVideoElement;

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

    fireEvent.change(screen.getAllByLabelText('Playback speed')[0], { target: { value: '1.5' } });

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
        player={{ ...emptyPlayer, track, queue: [track], queueIndex: 0 }}
        playTrack={vi.fn()}
        playRelative={vi.fn()}
        playQueueIndex={vi.fn()}
        addToQueue={vi.fn()}
        shuffleQueue={vi.fn()}
        togglePlayback={vi.fn()}
        seek={vi.fn()}
        setSpeed={vi.fn()}
        cycleRepeatMode={vi.fn()}
        setVolume={vi.fn()}
        toggleMute={vi.fn()}
        confirmNext={vi.fn()}
        cancelNext={vi.fn()}
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
