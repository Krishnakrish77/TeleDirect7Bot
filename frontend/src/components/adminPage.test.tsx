import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { runAdminAction, runAdminMaintenance } from '../api';
import type { AdminResponse, User } from '../types';
import { AdminPage } from './adminPage';

vi.mock('../api', () => ({
  fetchAdminStatus: vi.fn(),
  runAdminAction: vi.fn(),
  runAdminMaintenance: vi.fn(),
}));

const adminUser: User = {
  sub: 1,
  name: 'Admin',
  username: 'admin',
  photo: '',
  is_admin: true,
  exp: 9999999999,
};

const viewer: User = {
  ...adminUser,
  is_admin: false,
};

const adminData: AdminResponse = {
  items: [
    {
      messageId: 101,
      secureHash: 'hash',
      watchKey: 'hash101',
      title: 'Kalki',
      year: 2024,
      quality: '1080p',
      tags: ['action'],
      fileName: 'kalki.mkv',
      fileSize: 1024,
      fileSizeLabel: '1 KB',
      duration: 100,
      description: '',
      hidden: false,
      duplicate: false,
      hasThumb: true,
      missingThumb: false,
      missingPoster: false,
      mediaKind: 'video',
      seriesTitle: '',
      seriesKey: '',
      season: null,
      episode: null,
      episodeEnd: null,
      tmdbId: 123,
      tmdbKind: 'movie',
      imdbId: '',
      artist: '',
      albumTitle: '',
      trackNumber: null,
      adminLocked: [],
      posterUrl: '/thumb/hash101.jpg',
      watchHref: '/app/watch/hash101',
      classicHref: '/watch/hash101',
    },
  ],
  catalogueSize: 10,
  filteredCount: 1,
  page: 1,
  totalPages: 1,
  pageSize: 100,
  filterName: 'all',
  searchQ: '',
  sortCol: 'date',
  sortDir: 'desc',
  stats: {
    total: 10,
    total_size_bytes: 1024,
    kinds: {
      series_episodes: 2,
      movies: 5,
      movie_variant_groups: 1,
      movie_variant_extras: 1,
      standalone: 3,
    },
    quality_buckets: [['1080p', 5]],
    enrichment: {
      enriched: 7,
      attempted_no_match: 1,
      never_attempted: 2,
    },
    codec_health: {
      probed_playable: 3,
      probed_unplayable: 1,
      never_probed: 6,
    },
    top_genres: [['Action', 3]],
    missing_poster: 1,
    missing_thumb: 1,
    duplicate_groups: 1,
    duplicate_extras: 1,
    audio_count: 2,
    album_count: 1,
  },
  knownSeries: ['Ultimate Spiderman'],
  filters: [
    { value: 'all', label: 'All' },
    { value: 'movies', label: 'Movies' },
  ],
  sortOptions: [
    { value: 'date', label: 'Newest' },
    { value: 'title', label: 'Title' },
  ],
  capabilities: { gemini: false },
  status: {
    seed: {},
    enrich: {},
    reindex: {},
    probe: {},
    episode_fill: {},
    migrate: {},
    catalogue_size: 10,
  },
};

function renderAdmin(props: Partial<Parameters<typeof AdminPage>[0]> = {}) {
  return render(
    <AdminPage
      user={adminUser}
      data={adminData}
      loading={false}
      error=""
      locationSearch=""
      navigate={vi.fn()}
      onSignIn={vi.fn()}
      reload={vi.fn()}
      updateData={vi.fn()}
      {...props}
    />,
  );
}

beforeEach(() => {
  vi.mocked(runAdminAction).mockResolvedValue({ ok: true, message: 'Done' });
  vi.mocked(runAdminMaintenance).mockResolvedValue({ ok: true, message: 'Queued' });
});

describe('AdminPage', () => {
  it('prompts guests to sign in and blocks non-admin users', () => {
    const onSignIn = vi.fn();
    renderAdmin({ user: null, data: null, onSignIn });

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(onSignIn).toHaveBeenCalledTimes(1);

    renderAdmin({ user: viewer, data: null });
    expect(screen.getByText('Admin access required')).toBeTruthy();
  });

  it('renders catalogue rows and navigates when filters change', () => {
    const navigate = vi.fn();
    renderAdmin({ navigate });

    expect(screen.getByRole('heading', { name: 'Admin console' })).toBeTruthy();
    expect(screen.getByText('Kalki')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Filter'), { target: { value: 'movies' } });
    expect(navigate).toHaveBeenCalledWith('/app/admin?filter=movies');
  });

  it('runs selected-row bulk actions and reloads data', async () => {
    const reload = vi.fn();
    renderAdmin({ reload });

    fireEvent.click(screen.getByLabelText('Select Kalki'));
    fireEvent.click(screen.getAllByRole('button', { name: 'Hide' })[0]);

    await waitFor(() => {
      expect(runAdminAction).toHaveBeenCalledWith({ action: 'hide', ids: [101] });
    });
    expect(reload).toHaveBeenCalledTimes(1);
    expect((await screen.findByRole('status')).textContent).toContain('Done');
  });

  it('runs maintenance operations', async () => {
    const reload = vi.fn();
    renderAdmin({ reload });

    fireEvent.click(screen.getByRole('button', { name: /Re-index/ }));

    await waitFor(() => {
      expect(runAdminMaintenance).toHaveBeenCalledWith('reindex');
    });
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
