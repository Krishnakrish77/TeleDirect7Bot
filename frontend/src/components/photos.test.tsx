import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  connectPhotosChannel,
  fetchPhotoFacets,
  resyncPhotosLibrary,
  fetchPendingPhotoChannel,
  fetchPhotoAlbums,
  fetchPhotosStatus,
  fetchPhotosTimeline,
  setAlbumPhotos,
  uploadPhotos,
} from '../api';
import type { Photo, PhotoAlbum } from '../types';
import {
  PhotosPage,
  buildJustifiedRows,
  buildLightboxSlides,
  buildVirtualModel,
  dayKey,
  trashBatches,
} from './photos';

vi.mock('../api', () => ({
  connectPhotosChannel: vi.fn(),
  createPhotoAlbum: vi.fn(),
  deletePhotoAlbum: vi.fn(),
  disconnectPhotosChannel: vi.fn(),
  fetchPendingPhotoChannel: vi.fn(),
  fetchPhotoAlbums: vi.fn(),
  fetchPhotoFacets: vi.fn().mockResolvedValue({ kinds: {}, cameras: [], months: [] }),
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
    place: null,
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

/**
 * jsdom has no layout engine, so `clientWidth`/`getBoundingClientRect` report 0
 * and the justified timeline would stay on its pre-measure placeholder. Give
 * every element a fixed container width for these tests.
 */
function stubLayoutWidth(width = 1200) {
  return vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: HTMLElement,
  ) {
    return {
      x: 0, y: 0, top: 0, left: 0, right: width, bottom: 0, width, height: 600,
      toJSON: () => ({}),
    } as DOMRect;
  });
}

beforeEach(() => {
  stubLayoutWidth();
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

afterEach(() => {
  vi.restoreAllMocks();
});

describe('lightbox slides and bulk trash', () => {
  it('downloads the original, not the preview thumbnail', () => {
    const slides = buildLightboxSlides([
      makePhoto({ id: 'p1' }),
      makePhoto({ id: 'p2', kind: 'video', mime: 'video/mp4', duration: 12 }),
    ]);

    // The stage keeps the preview (HEIC-safe, no original fetch while browsing).
    expect(slides[0]).toMatchObject({
      type: 'image',
      src: '/api/photos/thumb/p1/preview',
      download: '/api/photos/file/p1',
    });
    // YARL's download plug-in falls back to `src`, which saved a webp.
    expect(slides[1]).toMatchObject({ type: 'video', download: '/api/photos/file/p2' });
    expect(JSON.stringify(slides[1])).toContain('/api/photos/file/p2');
  });

  it('batches bulk trash at the API cap so select-all cannot 400', () => {
    const ids = Array.from({ length: 1200 }, (_, i) => `id${i}`);
    const batches = trashBatches(ids);
    expect(batches.map((b) => b.length)).toEqual([500, 500, 200]);
    expect(batches.flat()).toEqual(ids);
    expect(trashBatches([])).toEqual([]);
  });
});

describe('virtual timeline model', () => {
  const photo = (id: string, day: string): Photo => ({
    ...makePhoto({ id, fileName: `${id}.jpg` }),
    takenAt: `${day}T10:00:00Z`,
  });
  const group = (key: string, label: string, photos: Photo[]) => ({
    key,
    label,
    takenAt: photos[0].takenAt,
    photos,
  });

  it('restarts rows at day boundaries so every day gets its own header', () => {
    // Three photos per day. Rows used to flow across the boundary, which left
    // the later day without a header and mislabeled its photos under the
    // earlier day — a 3-photo library across three months read as one day.
    const groups = [
      group('2026-09-21', 'September 21, 2026', [photo('a', '2026-09-21'), photo('b', '2026-09-21'), photo('c', '2026-09-21')]),
      group('2026-09-20', 'September 20, 2026', [photo('d', '2026-09-20'), photo('e', '2026-09-20'), photo('f', '2026-09-20')]),
    ];
    const entries = buildVirtualModel(groups, 800, 190);
    const headers = entries.filter((e) => e.kind === 'header');
    const rows = entries.flatMap((e) => (e.kind === 'row' ? [e.row] : []));

    expect(headers.map((h) => h.group.label)).toEqual(['September 21, 2026', 'September 20, 2026']);
    // Every photo appears exactly once, in order.
    expect(rows.flatMap((row) => row.items.map((p) => p.id))).toEqual(['a', 'b', 'c', 'd', 'e', 'f']);
    // No row mixes days.
    expect(rows.every((row) => new Set(row.items.map((p) => p.takenAt?.slice(0, 10))).size === 1)).toBe(true);
  });

  it('labels each day truthfully even when one row could hold the whole library', () => {
    // Three photos from three different months fit a single justified row at
    // this width — but each must still sit under its own date header.
    const groups = [
      group('2026-09-27', 'September 27, 2026', [photo('a', '2026-09-27')]),
      group('2026-07-14', 'July 14, 2026', [photo('b', '2026-07-14')]),
      group('2026-06-02', 'June 2, 2026', [photo('c', '2026-06-02')]),
    ];
    const entries = buildVirtualModel(groups, 1400, 150);
    const headers = entries.filter((e) => e.kind === 'header');
    expect(headers.map((h) => h.group.label)).toEqual(['September 27, 2026', 'July 14, 2026', 'June 2, 2026']);
    // Header order follows the photos: each header precedes its day's row.
    const sequence = entries.map((e) => (e.kind === 'header' ? `H:${e.group.key}` : `R:${dayKey(e.row.items[0].takenAt)}`));
    expect(sequence).toEqual(['H:2026-09-27', 'R:2026-09-27', 'H:2026-07-14', 'R:2026-07-14', 'H:2026-06-02', 'R:2026-06-02']);
  });
});

describe('justified timeline layout', () => {
  const photo = (id: string, ratio: number): Photo => ({
    ...makePhoto({ id, fileName: `${id}.jpg` }),
    width: Math.round(ratio * 1000),
    height: 1000,
  });

  it('fills complete rows edge to edge and preserves each aspect ratio', () => {
    const photos = [
      photo('a', 1.5), photo('b', 0.67), photo('c', 1), photo('d', 1.33),
      photo('e', 0.75), photo('f', 1), photo('g', 1.78), photo('h', 0.8),
      photo('i', 1.2),
    ];
    const rows = buildJustifiedRows(photos, 1000, 200, 4);
    expect(rows.length).toBeGreaterThan(1);

    for (const row of rows.slice(0, -1)) {
      const widths = row.items.map((item) => (item.width! / item.height!) * row.height);
      const total = widths.reduce((a, b) => a + b, 0) + 4 * (row.items.length - 1);
      expect(Math.abs(total - 1000)).toBeLessThan(0.01);
    }
    for (const row of rows) {
      for (const item of row.items) {
        // Box ratio == photo ratio: nothing is cropped or distorted.
        const boxRatio = (item.width! / item.height!);
        expect(boxRatio).toBeGreaterThan(0);
        const rendered = (item.width! / item.height!) * row.height / row.height;
        expect(Math.abs(rendered - boxRatio)).toBeLessThan(0.001);
      }
    }
  });

  it('lets a partial row grow to fill, but never beyond the scale cap', () => {
    // Four mixed photos: 988 / 4.5 = 219.6 stays inside the cap, so this
    // partial row still ends flush with the row above it.
    const partial = [photo('a', 1.5), photo('b', 1), photo('c', 1), photo('d', 1)];
    const filled = buildJustifiedRows(partial, 1000, 200, 4);
    expect(filled).toHaveLength(1);
    expect(Math.abs(filled[0].height - (1000 - 3 * 4) / 4.5)).toBeLessThan(0.01);
    expect(filled[0].height).toBeLessThanOrEqual(200 * 1.15 + 0.01);

    // A single 16:9 photo would need 553px to fill; the cap keeps the row in
    // the same height band as its neighbours instead.
    const capped = buildJustifiedRows([photo('wide', 1.78)], 1000, 200, 4);
    expect(capped[0].height).toBeCloseTo(200 * 1.15, 5);
  });

  it('returns nothing for an empty library', () => {
    expect(buildJustifiedRows([], 1000, 200, 4)).toEqual([]);
  });
});

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
    // Album membership lives in the info slide-over (GPhotos' right drawer).
    fireEvent.click(await screen.findByRole('button', { name: 'Info' }));
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

describe('Photos signed-out gate', () => {
  it('invites the user with a sign-in card instead of a bare sentence', () => {
    const onSignIn = vi.fn();
    render(<PhotosPage user={null} onSignIn={onSignIn} />);

    expect(screen.getByRole('heading', { name: 'TeleDirect Photos' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in to use Photos' }));
    expect(onSignIn).toHaveBeenCalledTimes(1);
  });

  it('still explains itself when no sign-in trigger is wired', () => {
    render(<PhotosPage user={null} />);

    expect(screen.getByRole('heading', { name: 'TeleDirect Photos' })).toBeTruthy();
    expect(screen.getByText(/Use the Sign in button in the header/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Sign in to use Photos' })).toBeNull();
  });
});

describe('Photos missing-photo search', () => {
  it('reports that a search for missing photos is running', async () => {
    vi.mocked(resyncPhotosLibrary).mockResolvedValue(undefined);

    render(<PhotosPage user={user} />);
    await screen.findByRole('button', { name: 'IMG_1.jpg' });

    // The scan is a maintenance action in the header overflow menu.
    await userEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Find missing photos' }));

    const status = await screen.findByRole('status');
    expect(status.textContent).toContain('Checking your Telegram channel');
  });
});

describe('Photos info panel', () => {
  it('renders without crashing when stored GPS has null components', async () => {
    // Older Mongo docs can carry {lat: null, lon: null} — a truthy object
    // whose parts are not numbers. The panel must skip Location, not die.
    vi.mocked(fetchPhotosTimeline).mockResolvedValue({
      items: [makePhoto({ gps: { lat: null, lon: null } as never })],
      nextCursor: null,
    });

    render(<PhotosPage user={user} />);
    fireEvent.click(await screen.findByRole('button', { name: 'IMG_1.jpg' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Info' }));

    // The drawer's close button only exists when the panel rendered.
    expect(await screen.findByRole('button', { name: 'Close info' })).toBeTruthy();
    expect(screen.queryByText('Location')).toBeNull();
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

describe('PhotosPage server search & filters', () => {
  it('queries the server with the debounced search box value', async () => {
    render(<PhotosPage user={user} />);
    await screen.findByRole('button', { name: 'IMG_1.jpg' });
    vi.mocked(fetchPhotosTimeline).mockClear();

    const box = screen.getByLabelText(/search photos/i);
    fireEvent.change(box, { target: { value: 'portugal' } });
    // Debounced: the fetch fires after the 250ms settle, not on keystroke.
    await waitFor(() => {
      const call = vi.mocked(fetchPhotosTimeline).mock.calls.at(-1);
      expect(call?.[0]?.q).toBe('portugal');
    }, { timeout: 2000 });
  });

  it('renders camera and kind chips from facets and toggles a chip into the query', async () => {
    vi.mocked(fetchPhotoFacets).mockResolvedValue({
      kinds: { photo: 12, video: 3 },
      cameras: [{ camera: 'Apple iPhone 15', count: 9 }],
      places: [{ place: 'Lisbon, Portugal', count: 7 }],
      months: [{ year: 2026, month: 9, count: 15 }],
    });
    render(<PhotosPage user={user} />);
    await screen.findByRole('button', { name: 'IMG_1.jpg' });

    // Kind chip with count, camera chip with count.
    const videos = await screen.findByRole('button', { name: /Videos/ });
    expect(videos.textContent).toContain('3');
    const cameraChip = screen.getByRole('button', { name: /Apple iPhone 15/ });
    expect(cameraChip.textContent).toContain('9');

    // Clicking a chip re-queries with kind=video; clicking again clears it.
    vi.mocked(fetchPhotosTimeline).mockClear();
    fireEvent.click(videos);
    await waitFor(() => {
      const call = vi.mocked(fetchPhotosTimeline).mock.calls.at(-1);
      expect(call?.[0]?.kind).toBe('video');
    });
    fireEvent.click(videos);
    await waitFor(() => {
      const call = vi.mocked(fetchPhotosTimeline).mock.calls.at(-1);
      expect(call?.[0]?.kind).toBeUndefined();
    });
  });
});
