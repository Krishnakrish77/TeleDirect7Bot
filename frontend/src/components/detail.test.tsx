import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { PlayerState } from '../hooks/audio';
import type { AlbumDetailResponse, MovieDetailResponse, VideoChoice, WatchTrack } from '../types';
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
    href: '/watch/kalki',
    playHref: '/app/watch/kalki',
    appHref: '/app/watch/kalki',
    classicHref: '/watch/kalki',
    streamHref: '/stream/kalki',
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
    trailerKey: '',
    playHref: '/app/watch/very-long-title',
    classicHref: '/watch/very-long-title',
    variants: [makeVideoChoice()],
    related: [],
  };
}

describe('Album detail', () => {
  it('renders a dense album summary with tracks and playback actions', () => {
    const album = makeAlbum();
    const playTrack = vi.fn();
    const shuffleQueue = vi.fn();
    const togglePlayback = vi.fn();
    const addToQueue = vi.fn();

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
    expect(screen.getByRole('link', { name: 'Play' }).getAttribute('href')).toBe('/app/watch/very-long-title');
    expect(screen.getByRole('link', { name: 'Classic player' }).getAttribute('href')).toBe('/watch/very-long-title');

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(onToggleSaved).toHaveBeenCalledWith('movie:very-long-title');
  });
});
