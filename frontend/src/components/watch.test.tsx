import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchAudioTracks, fetchSubtitles, fetchWatch } from '../api';
import type { PlayerState } from '../hooks/audio';
import type { AudioTrackOption, WatchVideo } from '../types';
import { WatchPage } from './watch';

vi.mock('../api', () => ({
  fetchAudioTracks: vi.fn(),
  fetchSubtitles: vi.fn(),
  fetchWatch: vi.fn(),
}));

const fetchWatchMock = vi.mocked(fetchWatch);
const fetchSubtitlesMock = vi.mocked(fetchSubtitles);
const fetchAudioTracksMock = vi.mocked(fetchAudioTracks);

const emptyPlayer: PlayerState = {
  track: null,
  queue: [],
  queueIndex: -1,
  playing: false,
  currentTime: 0,
  duration: 0,
  error: '',
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
      togglePlayback={vi.fn()}
      seek={vi.fn()}
      onOpenQueue={vi.fn()}
    />,
  );
}

beforeEach(() => {
  fetchSubtitlesMock.mockResolvedValue([]);
  fetchAudioTracksMock.mockResolvedValue([]);
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
});
