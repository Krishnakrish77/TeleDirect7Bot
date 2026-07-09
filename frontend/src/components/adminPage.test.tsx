import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchAdminSeriesList, fetchAdminStatus, mergeAdminSeries, runAdminAction, runAdminMaintenance } from '../api';
import type { AdminResponse, User } from '../types';
import { AdminPage } from './adminPage';

vi.mock('../api', () => ({
  aiSuggestItem: vi.fn(),
  clearAdminItemTmdb: vi.fn(),
  fetchAdminItem: vi.fn(),
  fetchAdminSeriesList: vi.fn(),
  fetchAdminStatus: vi.fn(),
  fetchAiModels: vi.fn(),
  fetchTmdbPreview: vi.fn(),
  mergeAdminSeries: vi.fn(),
  resolveTmdbImdb: vi.fn(),
  runAdminAction: vi.fn(),
  runAdminMaintenance: vi.fn(),
  saveAdminItem: vi.fn(),
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

const noopAdminProps = {
  user: adminUser,
  loading: false,
  error: '',
  locationSearch: '',
  navigate: vi.fn(),
  onSignIn: vi.fn(),
  reload: vi.fn(),
  updateData: vi.fn(),
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
      duplicateReason: '',
      duplicateGroupSize: 0,
      hasThumb: true,
      missingThumb: false,
      missingPoster: false,
      mediaKind: 'video',
      seriesTitle: '',
      seriesKey: '',
      season: null,
      episode: null,
      episodeEnd: null,
      recapStart: null,
      recapEnd: null,
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
    credits: {},
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
  vi.clearAllMocks();
  vi.mocked(fetchAdminStatus).mockResolvedValue(adminData.status);
  vi.mocked(fetchAdminSeriesList).mockResolvedValue([
    { key: 'split-show', title: 'Split Show', count: 2 },
    { key: 'target-show', title: 'Target Show', count: 8 },
  ]);
  vi.mocked(mergeAdminSeries).mockResolvedValue({
    ok: true,
    merged: 2,
    target_title: 'Target Show',
    target_key: 'target-show',
  });
  vi.mocked(runAdminAction).mockResolvedValue({ ok: true, message: 'Done' });
  vi.mocked(runAdminMaintenance).mockResolvedValue({ ok: true, message: 'Queued' });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
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

  it('renders episode zero as a valid episode label', () => {
    renderAdmin({
      data: {
        ...adminData,
        items: [{
          ...adminData.items[0],
          seriesTitle: 'Specials',
          seriesKey: 'specials',
          season: 1,
          episode: 0,
        }],
      },
    });

    expect(screen.getByText('Specials - S01E00')).toBeTruthy();
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

  it('deletes a single row from the row actions', async () => {
    const reload = vi.fn();
    renderAdmin({ reload });

    fireEvent.click(screen.getByRole('button', { name: 'Delete Kalki' }));

    await waitFor(() => {
      expect(runAdminAction).toHaveBeenCalledWith({ action: 'delete', ids: [101] });
    });
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('Delete "Kalki"'));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('shows duplicate candidate reasons on catalogue rows', () => {
    renderAdmin({
      data: {
        ...adminData,
        items: [{
          ...adminData.items[0],
          duplicate: true,
          duplicateReason: 'Same title/year/quality',
          duplicateGroupSize: 2,
        }],
      },
    });

    expect(screen.getByText('Same title/year/quality (2)')).toBeTruthy();
  });

  it('runs maintenance operations', async () => {
    const reload = vi.fn();
    renderAdmin({ reload, locationSearch: '?tab=ops' });

    fireEvent.click(screen.getByRole('button', { name: /Re-index/ }));

    await waitFor(() => {
      expect(runAdminMaintenance).toHaveBeenCalledWith('reindex');
    });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('surfaces the metadata backfill maintenance action', async () => {
    const reload = vi.fn();
    renderAdmin({ reload, locationSearch: '?tab=ops' });

    fireEvent.click(screen.getByRole('button', { name: /Backfill metadata/ }));

    await waitFor(() => {
      expect(runAdminMaintenance).toHaveBeenCalledWith('metadata-cleanup');
    });
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('metadata backfill'));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('surfaces the non-admin catalogue prune action', async () => {
    const reload = vi.fn();
    renderAdmin({ reload, locationSearch: '?tab=ops' });

    fireEvent.click(screen.getByRole('button', { name: /Prune non-admin/ }));

    await waitFor(() => {
      expect(runAdminMaintenance).toHaveBeenCalledWith('prune-non-admin');
    });
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('non-admin uploads'));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('surfaces the safer credits-only backfill action', async () => {
    const reload = vi.fn();
    renderAdmin({ reload, locationSearch: '?tab=ops' });

    fireEvent.click(screen.getAllByRole('button', { name: /Backfill credits/ })[0]);

    await waitFor(() => {
      expect(runAdminMaintenance).toHaveBeenCalledWith('backfill-credits');
    });
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('cast/director'));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('renders the operations job center with running progress', () => {
    renderAdmin({
      locationSearch: '?tab=ops',
      data: {
        ...adminData,
        status: {
          ...adminData.status,
          enrich: { running: true, done: 3, total: 10, enriched: 2, last_title: 'Kalki' },
        },
      },
    });

    expect(screen.getByRole('heading', { name: 'Job center' })).toBeTruthy();
    expect(screen.getByText('TMDB enrichment')).toBeTruthy();
    expect(screen.getByText('Running')).toBeTruthy();
    expect(screen.getByText('Kalki')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Run enrichment' })).toHaveProperty('disabled', true);
    expect(screen.getByRole('heading', { name: 'Cleanup tools' })).toBeTruthy();
  });

  it('merges series from the React admin maintenance panel', async () => {
    const reload = vi.fn();
    renderAdmin({ reload, locationSearch: '?tab=ops' });

    fireEvent.click(screen.getByRole('button', { name: 'Merge series' }));
    await waitFor(() => expect(fetchAdminSeriesList).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText('Source'), { target: { value: 'Split Show' } });
    fireEvent.change(screen.getByLabelText('Target'), { target: { value: 'Target Show' } });
    const submit = screen.getByRole('button', { name: /^Merge$/ }) as HTMLButtonElement;
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);

    await waitFor(() => {
      expect(mergeAdminSeries).toHaveBeenCalledWith('split-show', 'target-show');
    });
    expect(reload).toHaveBeenCalledTimes(1);
    expect((await screen.findByRole('status')).textContent).toContain('Merged 2 episodes into Target Show');
  });

  it('keeps the status polling interval stable while workers keep running', () => {
    const setIntervalSpy = vi.spyOn(window, 'setInterval');
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval');
    const runningData = {
      ...adminData,
      status: {
        ...adminData.status,
        seed: { running: true, done: 1, total: 10 },
      },
    };
    const stillRunningData = {
      ...runningData,
      status: {
        ...runningData.status,
        seed: { running: true, done: 2, total: 10 },
      },
    };
    const stoppedData = {
      ...runningData,
      status: {
        ...runningData.status,
        seed: { running: false, done: 10, total: 10 },
      },
    };

    const view = render(<AdminPage {...noopAdminProps} data={runningData} />);
    expect(setIntervalSpy).toHaveBeenCalledTimes(1);

    view.rerender(<AdminPage {...noopAdminProps} data={stillRunningData} />);
    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
    expect(clearIntervalSpy).not.toHaveBeenCalled();

    view.rerender(<AdminPage {...noopAdminProps} data={stoppedData} />);
    expect(clearIntervalSpy).toHaveBeenCalledTimes(1);
  });
});
