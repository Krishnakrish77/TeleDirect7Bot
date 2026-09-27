import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  connectPhotosChannel,
  fetchPendingPhotoChannel,
  fetchPhotoAlbums,
  fetchPhotosStatus,
  fetchPhotosTimeline,
  setAlbumPhotos,
  uploadPhotos,
} from '../api';
import type { Photo, PhotoAlbum } from '../types';
import { PhotosPage } from './photos';

vi.mock('../api', () => ({
  connectPhotosChannel: vi.fn(),
  createPhotoAlbum: vi.fn(),
  deletePhotoAlbum: vi.fn(),
  disconnectPhotosChannel: vi.fn(),
  fetchPendingPhotoChannel: vi.fn(),
  fetchPhotoAlbums: vi.fn(),
  fetchPhotosStatus: vi.fn(),
  fetchPhotosTimeline: vi.fn(),
  photoFileUrl: (id: string) => `/api/photos/file/${id}`,
  photoThumbUrl: (id: string, size: string) => `/api/photos/thumb/${id}/${size}`,
  renamePhotoAlbum: vi.fn(),
  restorePhotos: vi.fn(),
  resyncPhotosLibrary: vi.fn(),
  setAlbumPhotos: vi.fn(),
  setPhotoFavorite: vi.fn(),
  trashPhotos: vi.fn(),
  uploadPhotos: vi.fn(),
}));

function makePhoto(overrides: Partial<Photo> = {}): Photo {
  return {
    id: 'p1',
    messageId: 11,
    kind: 'image',
    fileName: 'IMG_1.jpg',
    mime: 'image/jpeg',
    size: 1024,
    width: 100,
    height: 80,
    duration: null,
    takenAt: '2026-09-21T14:03:11Z',
    camera: null,
    gps: null,
    favorite: false,
    albumIds: [],
    deleted: false,
    thumbsReady: true,
    uploadedAt: null,
    ...overrides,
  };
}

function makeAlbum(overrides: Partial<PhotoAlbum> = {}): PhotoAlbum {
  return {
    id: 'a1',
    name: 'Holidays',
    coverMessageId: null,
    createdAt: null,
    sort: 0,
    ...overrides,
  };
}

const user = { sub: 1 };

beforeEach(() => {
  vi.mocked(fetchPhotosStatus).mockResolvedValue({
    connected: true,
    channelId: -1001234,
    status: 'active',
    beta: true,
    photoCount: 1,
  });
  vi.mocked(fetchPhotoAlbums).mockResolvedValue({ albums: [] });
  vi.mocked(fetchPendingPhotoChannel).mockResolvedValue({ channelId: null });
  vi.mocked(fetchPhotosTimeline).mockResolvedValue({ items: [makePhoto()], nextCursor: null });
  vi.mocked(setAlbumPhotos).mockResolvedValue(undefined);
  vi.mocked(uploadPhotos).mockResolvedValue({ results: [] });
});

/** The manual link/id field is opt-in now that Continue auto-detects. */
async function revealManualField() {
  fireEvent.click(
    await screen.findByRole('button', { name: /Already added the bot earlier/i }),
  );
}

describe('PhotosPage albums', () => {
  it('adds and removes a photo from albums in the lightbox', async () => {
    const member = makeAlbum({ id: 'a1', name: 'Holidays' });
    const other = makeAlbum({ id: 'a2', name: 'Work' });
    vi.mocked(fetchPhotoAlbums).mockResolvedValue({ albums: [member, other] });
    vi.mocked(fetchPhotosTimeline).mockResolvedValue({
      items: [makePhoto({ albumIds: ['a1'] })],
      nextCursor: null,
    });

    render(<PhotosPage user={user} />);

    fireEvent.click(await screen.findByRole('button', { name: 'IMG_1.jpg' }));
    // The photo is already in "Holidays": its chip removes it.
    fireEvent.click(screen.getByTitle('Remove from Holidays'));
    await waitFor(() => expect(setAlbumPhotos).toHaveBeenCalledWith('a1', ['p1'], false));

    // "Work" is not a member yet: the picker adds it.
    fireEvent.change(screen.getByLabelText('Add to album'), { target: { value: 'a2' } });
    await waitFor(() => expect(setAlbumPhotos).toHaveBeenCalledWith('a2', ['p1'], true));
  });
});

describe('Photos onboarding', () => {
  it('deep-links the bot into a channel and accepts a message link', async () => {
    vi.mocked(fetchPhotosStatus).mockResolvedValue({
      connected: false,
      botUsername: 'tdphotosbot',
      addToChannelUrl: 'https://t.me/tdphotosbot?startchannel=true&admin=post_messages',
    });
    vi.mocked(connectPhotosChannel).mockResolvedValue({ ok: true });

    render(<PhotosPage user={user} />);

    const link = await screen.findByRole('link', { name: /Add @tdphotosbot to a channel/i });
    expect(link.getAttribute('href')).toBe(
      'https://t.me/tdphotosbot?startchannel=true&admin=post_messages',
    );

    await revealManualField();
    fireEvent.change(screen.getByLabelText('Channel link or id'), {
      target: { value: 'https://t.me/c/1234567890/12' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Connect channel' }));
    await waitFor(() =>
      expect(connectPhotosChannel).toHaveBeenCalledWith('https://t.me/c/1234567890/12'),
    );
  });

  it('links the channel from the bot handshake when Continue finds it', async () => {
    vi.mocked(fetchPhotosStatus).mockResolvedValue({
      connected: false,
      botUsername: 'tdphotosbot',
      addToChannelUrl: 'https://t.me/tdphotosbot?startchannel=true&admin=post_messages',
    });
    vi.mocked(fetchPendingPhotoChannel).mockResolvedValue({
      channelId: -1001234567890,
      title: 'Vault',
    });
    vi.mocked(connectPhotosChannel).mockResolvedValue({ ok: true });

    render(<PhotosPage user={user} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Continue' }));

    await waitFor(() =>
      expect(connectPhotosChannel).toHaveBeenCalledWith('-1001234567890'),
    );
  });

  it('offers the manual field without waiting on the handshake', async () => {
    vi.mocked(fetchPhotosStatus).mockResolvedValue({ connected: false, botUsername: 'tdphotosbot' });

    render(<PhotosPage user={user} />);
    fireEvent.click(
      await screen.findByRole('button', { name: /Already added the bot earlier/i }),
    );

    expect(await screen.findByLabelText('Channel link or id')).toBeTruthy();
  });

  it('reports a connect failure without leaving the wizard stuck', async () => {
    vi.mocked(fetchPhotosStatus).mockResolvedValue({ connected: false, botUsername: null, addToChannelUrl: null });
    vi.mocked(connectPhotosChannel).mockResolvedValue({
      ok: false,
      error: 'That channel is public — use a private channel (no @username)',
    });

    render(<PhotosPage user={user} />);
    await revealManualField();
    fireEvent.change(await screen.findByLabelText('Channel link or id'), {
      target: { value: 'https://t.me/mychannel/42' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Connect channel' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('That channel is public');
    // Still on the wizard with the field intact, not a blank page.
    expect((screen.getByLabelText('Channel link or id') as HTMLInputElement).value).toBe(
      'https://t.me/mychannel/42',
    );
  });

  it('explains an unavailable feature instead of offering a doomed connect flow', async () => {
    vi.mocked(fetchPhotosStatus).mockRejectedValue(new Error('MongoDB is required for photos'));

    render(<PhotosPage user={user} />);

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('TeleDirect Photos isn’t available right now');
    expect(alert.textContent).toContain('MongoDB is required for photos');
    expect(screen.queryByLabelText('Channel link or id')).toBeNull();

    vi.mocked(fetchPhotosStatus).mockResolvedValue({ connected: false });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await revealManualField();
    expect(await screen.findByLabelText('Channel link or id')).toBeTruthy();
  });
});

describe('PhotosPage uploads', () => {
  it('surfaces per-file failures the server reports on a 200 response', async () => {
    vi.mocked(uploadPhotos).mockResolvedValue({
      results: [{ fileName: 'huge.jpg', error: 'File exceeds the 200 MB per-file limit' }],
    });

    const { container } = render(<PhotosPage user={user} />);
    await screen.findByRole('button', { name: 'IMG_1.jpg' });

    const input = container.querySelector('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input as HTMLInputElement, {
      target: { files: [new File(['x'], 'huge.jpg', { type: 'image/jpeg' })] },
    });

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('huge.jpg: File exceeds the 200 MB per-file limit');
  });
});
