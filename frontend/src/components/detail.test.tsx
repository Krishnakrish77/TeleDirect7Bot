import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PlayerState } from '../hooks/audio';
import type { AlbumDetailResponse, MovieDetailResponse, SeriesDetailResponse, VideoChoice, WatchTrack } from '../types';
import { DetailPage } from './detail';

function makeTrack(overrides: Partial<WatchTrack> = {}): WatchTrack {
  return {
    key: 'theme',
    itemId: 'item-theme',
    type: 'track',
    messageId: 1,
    secureHash: 'hash',
    title: 'Theme',
    year: 2026,
    mediaKind: 'music',
    posterUrl: '/thumb/theme.jpg',
    thumbUrl: '/thumb/theme.jpg',
    backdropUrl: '/thumb/theme-backdrop.jpg',
    duration: 120,
    durationLabel: '2:00',
    fileSize: 1000,
    fileSizeLabel: '1 KB',
    quality: 'mp3',
    genres: [],
    tags: [],
    overview: '',
    artist: 'Composer',
    albumTitle: 'Album',
    href: '/watch/theme',
    streamHref: '/stream/theme',
    watchKey: 'theme',
    trackNumber: 1,
    format: 'MP3',
    qualityLabel: 'MP3',
    appHref: '/app/watch/theme',
    classicHref: '/watch/theme',
    albumHref: '/app/album/album',
    ...overrides,
  };
}

function makeAlbum(): AlbumDetailResponse {
  const first = makeTrack();
  const second = makeTrack({ key: 'second', itemId: 'item-second', title: 'Second Theme', trackNumber: 2 });
  return {
    kind: 'album',
    key: 'album',
    savedId: 'album:album',
    title: 'Album',
    artist: 'Composer',
    artistHref: '/app/artist/composer',
    year: 2026,
    overview: 'A compact album overview.',
    posterUrl: '/thumb/album.jpg',
    backdropUrl: '/thumb/album-backdrop.jpg',
    trackCount: 2,
    playHref: '/app/watch/theme',
    tracks: [first, second],
    related: [],
  };
}

function makePlayer(overrides: Partial<PlayerState> = {}): PlayerState {
  return {
    track: null,
    queue: [],
    queueIndex: 0,
    playing: false,
    currentTime: 0,
    duration: 0,
    error: '',
    speed: 1,
    repeatMode: 'off',
    volume: 1,
    muted: false,
    nextTrack: null,
    nextCountdown: 0,
    queueToast: '',
    ...overrides,
  } as PlayerState;
}

function makeVideoChoice(overrides: Partial<VideoChoice> = {}): VideoChoice {
  return {
    type: 'movie',
    itemId: 'movie:kalki',
    key: 'kalki-1080p',
    label: '1080p',
    messageId: 10,
    secureHash: 'hash',
    title: 'Kalki',
    subtitle: '2024 - 1080p',
    year: 2024,
    mediaKind: 'movie',
    posterUrl: '/thumb/kalki.jpg',
    thumbUrl: '/thumb/kalki.jpg',
    backdropUrl: '/thumb/kalki-backdrop.jpg',
    duration: 7200,
    durationLabel: '2:00:00',
    fileSize: 1000,
    fileSizeLabel: '1 KB',
    quality: '1080p',
    genres: ['Action'],
    tags: [],
    overview: '',
    artist: '',
    albumTitle: '',
    trailerKey: '',
    href: '/watch/kalki',
    playHref: '/app/watch/kalki',
    appHref: '/app/watch/kalki',
    classicHref: '/watch/kalki',
    streamHref: '/stream/kalki',
    downloadHref: '/stream/kalki?download=1',
    watchKey: 'kalki',
    eyebrow: 'Movie',
    badge: '1080p',
    aspect: 'poster',
    ...overrides,
  };
}

function makeMovie(): MovieDetailResponse {
  return {
    kind: 'movie',
    key: 'very-long-title',
    savedId: 'movie:very-long-title',
    title: 'A Very Long Movie Title That Should Stay Readable On Mobile',
    year: 2026,
    overview: 'A long overview that should remain secondary to the primary actions.',
    posterUrl: '/thumb/movie.jpg',
    backdropUrl: '/thumb/movie-backdrop.jpg',
    genres: ['Action', 'Drama'],
    director: 'Director',
    directors: [{ name: 'Director', href: '/app/person/director' }],
    cast: [{ name: 'Actor', href: '/app/person/actor' }],
    imdbHref: '',
    externalRating: { provider: 'TMDB', value: 8.1, label: '8.1', count: 2500 },
    trailerKey: '',
    playHref: '/app/watch/very-long-title',
    classicHref: '/watch/very-long-title',
    variants: [makeVideoChoice()],
    related: [],
  };
}

function makeSeries(): SeriesDetailResponse {
  const started = makeVideoChoice({
    type: 'item',
    itemId: '101',
    key: 'hash101',
    watchKey: 'hash101',
    title: 'Training Day',
    episodeLabel: 'S01E01',
    episodeOverview: 'Peter starts training.',
    episodeStillUrl: '/thumb/episode-1.jpg',
    playHref: '/app/watch/hash101',
    classicHref: '/watch/hash101',
    downloadHref: '/hash101?download=1',
  });
  const watched = makeVideoChoice({
    type: 'item',
    itemId: '102',
    key: 'hash102',
    watchKey: 'hash102',
    title: 'Team Up',
    episodeLabel: 'S01E02',
    episodeOverview: 'The team comes together.',
    episodeStillUrl: '/thumb/episode-2.jpg',
    playHref: '/app/watch/hash102',
    classicHref: '/watch/hash102',
    downloadHref: '/hash102?download=1',
  });
  return {
    kind: 'series',
    key: 'ultimate-spiderman',
    savedId: 'series:ultimate-spiderman',
    title: 'Ultimate Spiderman',
    year: 2026,
    overview: 'A series overview.',
    posterUrl: '/thumb/series.jpg',
    backdropUrl: '/thumb/series-backdrop.jpg',
    genres: ['Action'],
    director: '',
    directors: [],
    cast: [],
    imdbHref: '',
    trailerKey: '',
    playHref: '/app/watch/hash101',
    classicHref: '/watch/hash101',
    seasonOptions: [{ value: '1', label: 'Season 1' }],
    showSelector: false,
    selectedSeason: '1',
    episodeCount: 2,
    totalEpisodeCount: 2,
    seasonCount: 1,
    seasonBlocks: [
      {
        season: 1,
        entries: [
          { rep: started, variants: [started], duplicateCount: 0, progressPct: 42, watched: false },
          { rep: watched, variants: [watched], duplicateCount: 0, progressPct: 0, watched: true },
        ],
      },
    ],
    related: [],
  };
}

beforeEach(() => {
  localStorage.clear();
});

describe('Album detail', () => {
  it('renders a dense album summary with tracks and playback actions', () => {
    const album = makeAlbum();
    const playTrack = vi.fn();
    const shuffleQueue = vi.fn();
    const togglePlayback = vi.fn();
    const addToQueue = vi.fn();
    const onAddToPlaylist = vi.fn();

    render(
      <DetailPage
        route={{ kind: 'detail', detailKind: 'album', key: 'album' }}
        data={album}
        loading={false}
        error=""
        saved={new Set()}
        onToggleSaved={vi.fn()}
        navigate={vi.fn()}
        playTrack={playTrack}
        togglePlayback={togglePlayback}
        addToQueue={addToQueue}
        shuffleQueue={shuffleQueue}
        player={makePlayer()}
        onAddToPlaylist={onAddToPlaylist}
      />,
    );

    expect(screen.getByLabelText('Album summary')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Album' })).toBeTruthy();
    expect(screen.getAllByText('2 tracks').length).toBeGreaterThan(0);
    expect(screen.getByText('2026')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Play all' }));
    expect(playTrack).toHaveBeenCalledWith(album.tracks[0], album.tracks);

    fireEvent.click(screen.getByRole('button', { name: 'Shuffle' }));
    expect(shuffleQueue).toHaveBeenCalledWith(album.tracks);

    fireEvent.click(screen.getByRole('button', { name: 'Play Theme' }));
    expect(togglePlayback).toHaveBeenCalledWith(album.tracks[0], album.tracks);

    fireEvent.click(screen.getByRole('button', { name: 'Play Theme next' }));
    expect(addToQueue).toHaveBeenCalledWith(album.tracks[0], true);

    fireEvent.click(screen.getByRole('button', { name: 'Add Theme to playlist' }));
    expect(onAddToPlaylist).toHaveBeenCalledWith(album.tracks[0]);
  });
});

describe('Series detail', () => {
  it('shows in-progress and completed episode states', () => {
    render(
      <DetailPage
        route={{ kind: 'detail', detailKind: 'series', key: 'ultimate-spiderman' }}
        data={makeSeries()}
        loading={false}
        error=""
        saved={new Set()}
        onToggleSaved={vi.fn()}
        navigate={vi.fn()}
        playTrack={vi.fn()}
        togglePlayback={vi.fn()}
        addToQueue={vi.fn()}
        shuffleQueue={vi.fn()}
        player={makePlayer()}
      />,
    );

    expect(screen.getByRole('heading', { level: 1, name: 'Ultimate Spiderman' })).toBeTruthy();
    const info = within(screen.getByLabelText('Movie and series information'));
    expect(info.getByText('About this series')).toBeTruthy();
    expect(info.getByRole('heading', { name: 'Ultimate Spiderman' })).toBeTruthy();
    expect(info.getByText('A series overview.')).toBeTruthy();
    expect(info.getByText('2 episodes')).toBeTruthy();
    expect(screen.getByLabelText('42% watched')).toBeTruthy();
    expect(screen.getByLabelText('Watched')).toBeTruthy();
    expect(screen.getByText('Training Day')).toBeTruthy();
    expect(screen.getByText('Team Up')).toBeTruthy();
  });

  it('starts visible episode downloads in one user-triggered batch', () => {
    vi.useFakeTimers();
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    try {
      render(
        <DetailPage
          route={{ kind: 'detail', detailKind: 'series', key: 'ultimate-spiderman' }}
          data={makeSeries()}
          loading={false}
          error=""
          saved={new Set()}
          onToggleSaved={vi.fn()}
          navigate={vi.fn()}
          playTrack={vi.fn()}
          togglePlayback={vi.fn()}
          addToQueue={vi.fn()}
          shuffleQueue={vi.fn()}
          player={makePlayer()}
        />,
      );

      expect(screen.getByRole('link', { name: 'Download S01E01 Training Day' }).getAttribute('href')).toBe('/hash101?download=1');
      fireEvent.click(screen.getByRole('button', { name: 'Download all shown episodes' }));
      expect(clickSpy).toHaveBeenCalledTimes(2);
      expect(screen.getByRole('button', { name: 'Download all shown episodes' }).textContent).toBe('Starting 2/2');

      act(() => {
        vi.advanceTimersByTime(1600);
      });
      expect(screen.getByRole('button', { name: 'Download all shown episodes' }).textContent).toBe('Download all');
    } finally {
      clickSpy.mockRestore();
      vi.useRealTimers();
    }
  });

  it('marks the shown episodes watched from the detail page', () => {
    const series = makeSeries();
    const onMarkWatched = vi.fn();
    localStorage.setItem('td:cw', JSON.stringify({
      hash101: { pos: 120, dur: 240 },
      hash102: { pos: 20, dur: 240 },
    }));

    render(
      <DetailPage
        route={{ kind: 'detail', detailKind: 'series', key: 'ultimate-spiderman' }}
        data={series}
        loading={false}
        error=""
        saved={new Set()}
        onToggleSaved={vi.fn()}
        navigate={vi.fn()}
        playTrack={vi.fn()}
        togglePlayback={vi.fn()}
        addToQueue={vi.fn()}
        shuffleQueue={vi.fn()}
        player={makePlayer()}
        onMarkWatched={onMarkWatched}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: `Mark ${series.title} as watched` }));

    expect(onMarkWatched).toHaveBeenCalledWith(['hash101', 'hash102'], series.title);
    expect(JSON.parse(localStorage.getItem('td:cw') || '{}')).toEqual({});
    expect(screen.getByRole('button', { name: `${series.title} watched` }).textContent).toContain('Shown watched');
    expect(screen.queryByLabelText('42% watched')).toBeNull();
    expect(screen.getAllByLabelText('Watched')).toHaveLength(2);
  });
});

describe('Movie detail', () => {
  it('keeps long titles and primary actions available in the detail hero', () => {
    const onToggleSaved = vi.fn();
    const movie = makeMovie();

    render(
      <DetailPage
        route={{ kind: 'detail', detailKind: 'movie', key: 'very-long-title' }}
        data={movie}
        loading={false}
        error=""
        saved={new Set()}
        onToggleSaved={onToggleSaved}
        navigate={vi.fn()}
        playTrack={vi.fn()}
        togglePlayback={vi.fn()}
        addToQueue={vi.fn()}
        shuffleQueue={vi.fn()}
        player={makePlayer()}
      />,
    );

    expect(screen.getByRole('heading', { name: 'A Very Long Movie Title That Should Stay Readable On Mobile (2026)' })).toBeTruthy();
    expect(screen.getByText('TMDB 8.1')).toBeTruthy();
    const info = within(screen.getByLabelText('Movie and series information'));
    expect(info.getByText('About this title')).toBeTruthy();
    expect(info.getByRole('heading', { name: movie.title })).toBeTruthy();
    expect(info.getByText('A long overview that should remain secondary to the primary actions.')).toBeTruthy();
    expect(info.getByText('Director', { selector: 'dt' })).toBeTruthy();
    expect(info.getByRole('link', { name: 'Actor' }).getAttribute('href')).toBe('/app/person/actor');
    expect(screen.getByRole('link', { name: 'Play' }).getAttribute('href')).toBe('/app/watch/very-long-title');
    expect(screen.getByRole('link', { name: 'Classic player' }).getAttribute('href')).toBe('/watch/very-long-title');
    const versionLink = screen.getByRole('link', { name: 'Play Kalki 1080p' });
    expect(versionLink.className).toBe('playback-option');
    expect(versionLink.getAttribute('href')).toBe('/app/watch/kalki');
    expect(screen.getByText('Kalki')).toBeTruthy();
    expect(screen.getByText('2:00:00 - 1 KB')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(onToggleSaved).toHaveBeenCalledWith('movie:very-long-title');
  });

  it('marks a movie watched from the detail page', () => {
    const movie = makeMovie();
    const onMarkWatched = vi.fn();
    localStorage.setItem('td:cw', JSON.stringify({ kalki: { pos: 120, dur: 240 } }));

    render(
      <DetailPage
        route={{ kind: 'detail', detailKind: 'movie', key: 'very-long-title' }}
        data={movie}
        loading={false}
        error=""
        saved={new Set()}
        onToggleSaved={vi.fn()}
        navigate={vi.fn()}
        playTrack={vi.fn()}
        togglePlayback={vi.fn()}
        addToQueue={vi.fn()}
        shuffleQueue={vi.fn()}
        player={makePlayer()}
        onMarkWatched={onMarkWatched}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: `Mark ${movie.title} as watched` }));

    expect(onMarkWatched).toHaveBeenCalledWith(['kalki'], movie.title);
    expect(JSON.parse(localStorage.getItem('td:cw') || '{}')).toEqual({});
    expect(screen.getByRole('button', { name: `${movie.title} watched` }).textContent).toContain('Watched');
  });
});
