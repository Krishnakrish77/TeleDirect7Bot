import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { createPlaylist, removeTrackFromPlaylist, renamePlaylist, reorderPlaylistTracks } from '../api';
import type { PlayerState } from '../hooks/audio';
import type { PlaylistDetailResponse, PlaylistsResponse, User, WatchTrack } from '../types';
import { PlaylistDetailPage, PlaylistsPage } from './playlistsPage';

vi.mock('../api', () => ({
  createPlaylist: vi.fn(),
  deletePlaylist: vi.fn(),
  removeTrackFromPlaylist: vi.fn(),
  renamePlaylist: vi.fn(),
  reorderPlaylistTracks: vi.fn(),
}));

const user: User = {
  sub: 1,
  name: 'Viewer',
  username: 'viewer',
  photo: '',
  is_admin: false,
  exp: 9999999999,
};

function makeTrack(overrides: Partial<WatchTrack> = {}): WatchTrack {
  return {
    key: 'hash1',
    itemId: '1',
    type: 'track',
    messageId: 1,
    secureHash: 'hash',
    title: 'Theme',
    year: 2026,
    mediaKind: 'audio',
    posterUrl: '/thumb/theme.jpg',
    thumbUrl: '/thumb/theme.jpg',
    backdropUrl: '',
    duration: 120,
    durationLabel: '2m',
    fileSize: 1000,
    fileSizeLabel: '1 KB',
    quality: '',
    genres: [],
    tags: [],
    overview: '',
    artist: 'Composer',
    albumTitle: 'Album',
    href: '/watch/hash1',
    streamHref: '/hash1',
    watchKey: 'hash1',
    trackNumber: 1,
    format: 'MP3',
    qualityLabel: 'MP3',
    appHref: '/app/watch/hash1',
    classicHref: '/watch/hash1',
    albumHref: '/app/album/album',
    ...overrides,
  };
}

function makePlayer(): PlayerState {
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
  };
}

const library: PlaylistsResponse = {
  available: true,
  maxPlaylists: 50,
  maxTracks: 500,
  playlists: [
    {
      playlistId: '1234567890abcdef1234567890abcdef',
      name: 'Roadtrip',
      trackCount: 2,
      coverUrls: ['/thumb/theme.jpg'],
      createdAt: '',
      updatedAt: '',
    },
  ],
};

function detail(overrides: Partial<PlaylistDetailResponse> = {}): PlaylistDetailResponse {
  const first = makeTrack();
  const second = makeTrack({ key: 'hash2', messageId: 2, itemId: '2', title: 'Second Theme', trackNumber: 2 });
  return {
    playlistId: '1234567890abcdef1234567890abcdef',
    name: 'Roadtrip',
    trackCount: 2,
    coverUrls: ['/thumb/theme.jpg', '/thumb/second.jpg'],
    createdAt: '',
    updatedAt: '',
    available: true,
    maxPlaylists: 50,
    maxTracks: 500,
    tracks: [first, second],
    ...overrides,
  };
}

describe('PlaylistsPage', () => {
  it('prompts guests to sign in', () => {
    const onSignIn = vi.fn();
    render(
      <PlaylistsPage
        user={null}
        data={null}
        loading={false}
        error=""
        navigate={vi.fn()}
        onSignIn={onSignIn}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(onSignIn).toHaveBeenCalledTimes(1);
  });

  it('renders playlist cards and navigates after create', async () => {
    vi.mocked(createPlaylist).mockResolvedValue(detail({ playlistId: 'abcdefabcdefabcdefabcdefabcdefab', name: 'Focus' }));
    const navigate = vi.fn();
    render(
      <PlaylistsPage
        user={user}
        data={library}
        loading={false}
        error=""
        navigate={navigate}
        onSignIn={vi.fn()}
      />,
    );

    expect(screen.getByRole('link', { name: /Roadtrip/i }).getAttribute('href')).toBe('/app/playlist/1234567890abcdef1234567890abcdef');

    fireEvent.change(screen.getByPlaceholderText('New playlist'), { target: { value: 'Focus' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/app/playlist/abcdefabcdefabcdefabcdefabcdefab'));
  });
});

describe('PlaylistDetailPage', () => {
  it('plays, filters, saves tracks elsewhere, removes tracks, and reorders', async () => {
    const current = detail();
    const updatedAfterRemove = detail({ tracks: [current.tracks[1]], trackCount: 1 });
    const updatedAfterReorder = detail({ tracks: [current.tracks[1], current.tracks[0]] });
    vi.mocked(removeTrackFromPlaylist).mockResolvedValue(updatedAfterRemove);
    vi.mocked(reorderPlaylistTracks).mockResolvedValue(updatedAfterReorder);
    vi.mocked(renamePlaylist).mockResolvedValue(detail({ name: 'Night Drive' }));
    const playTrack = vi.fn();
    const addToQueue = vi.fn();
    const onAddToPlaylist = vi.fn();
    const setData = vi.fn();

    render(
      <PlaylistDetailPage
        user={user}
        data={current}
        loading={false}
        error=""
        setData={setData}
        navigate={vi.fn()}
        onSignIn={vi.fn()}
        player={makePlayer()}
        playTrack={playTrack}
        togglePlayback={vi.fn()}
        addToQueue={addToQueue}
        shuffleQueue={vi.fn()}
        onAddToPlaylist={onAddToPlaylist}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(playTrack).toHaveBeenCalledWith(current.tracks[0], current.tracks);

    fireEvent.click(screen.getByRole('button', { name: 'Add Theme to playlist' }));
    expect(onAddToPlaylist).toHaveBeenCalledWith(current.tracks[0]);

    fireEvent.click(screen.getByRole('button', { name: 'Play Theme next' }));
    expect(addToQueue).toHaveBeenCalledWith(current.tracks[0], true);

    fireEvent.change(screen.getByPlaceholderText('Search this playlist'), { target: { value: 'second' } });
    expect(screen.queryByText('Theme')).toBeNull();
    expect(screen.getByText('Second Theme')).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText('Search this playlist'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Move Second Theme up' }));
    await waitFor(() => expect(reorderPlaylistTracks).toHaveBeenCalledWith(current.playlistId, [2, 1]));

    fireEvent.click(screen.getByRole('button', { name: 'Remove Theme from playlist' }));
    await waitFor(() => expect(removeTrackFromPlaylist).toHaveBeenCalledWith(current.playlistId, 1));

    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    fireEvent.change(screen.getByDisplayValue('Roadtrip'), { target: { value: 'Night Drive' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(renamePlaylist).toHaveBeenCalledWith(current.playlistId, 'Night Drive'));
  });
});
