import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { fetchAdminDashboard, runAdminMaintenance } from '../api';
import type { AdminDashboardResponse, User } from '../types';
import { AdminDashboard } from './adminDashboard';

vi.mock('../api', () => ({
  fetchAdminDashboard: vi.fn(),
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

const dashboard: AdminDashboardResponse = {
  total: 12,
  total_size_bytes: 1024,
  total_size_label: '1 KiB',
  kinds: {
    series_episodes: 3,
    movies: 5,
    movie_variant_groups: 1,
    movie_variant_extras: 1,
    standalone: 4,
  },
  audio_count: 2,
  album_count: 1,
  enrichment: {
    enriched: 8,
    attempted_no_match: 1,
    never_attempted: 3,
  },
  codec_health: {
    probed_playable: 4,
    probed_unplayable: 1,
    never_probed: 7,
  },
  top_genres: [['Action', 4]],
  missing_poster: 1,
  missing_thumb: 1,
  duplicate_groups: 1,
  duplicate_extras: 1,
  metadata_quality: {
    video_items: 10,
    tmdb_enriched_video_items: 8,
    missing_credits: 3,
    missing_tmdb_id: 2,
    missing_overview: 2,
    missing_year: 1,
    missing_cast: 3,
    missing_episode_metadata: 4,
    missing_playback_markers: 5,
    health_score: 70,
  },
  credits_backfill: {
    running: false,
    done: 4,
    total: 4,
    updated: 3,
    failed: 1,
    finished_at: 1710000000,
  },
  storage_by_quality: [{ quality: '1080p', bytes: 1024, label: '1 KiB' }],
  storage_by_codec: [{ codec: 'h264', bytes: 1024, label: '1 KiB' }],
  year_distribution: [{ decade: 2020, count: 3 }],
  year_distribution_max: 3,
  quality_counts: { '1080p': 5 },
  top_series: [{ key: 'show', title: 'Show', count: 3 }],
  recent_additions: [],
  largest_items: [],
};

describe('AdminDashboard', () => {
  it('shows metadata quality cleanup links', async () => {
    vi.mocked(fetchAdminDashboard).mockResolvedValue(dashboard);

    render(<AdminDashboard user={adminUser} onSignIn={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole('heading', { name: '70% complete' })).toBeTruthy());
    expect(screen.getByRole('link', { name: /Missing overview2/i }).getAttribute('href')).toBe('/app/admin?filter=no-overview');
    expect(screen.getByRole('link', { name: /Missing markers5/i }).getAttribute('href')).toBe('/app/admin?filter=no-markers');
  });

  it('shows credits coverage with quick filters', async () => {
    vi.mocked(fetchAdminDashboard).mockResolvedValue(dashboard);

    render(<AdminDashboard user={adminUser} onSignIn={vi.fn()} />);

    await waitFor(() => expect(screen.getByText('Credits coverage')).toBeTruthy());
    expect(screen.getByText('8 / 10')).toBeTruthy();
    expect(screen.getByText('3 updated · 4 checked · 1 failed')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Missing credits 3/i }).getAttribute('href')).toBe('/app/admin?filter=no-cast');
    expect(screen.getByRole('link', { name: /No TMDB ID 2/i }).getAttribute('href')).toBe('/app/admin?filter=unenriched');
  });

  it('queues one-click metadata cleanup from the dashboard', async () => {
    vi.mocked(fetchAdminDashboard).mockResolvedValue(dashboard);
    vi.mocked(runAdminMaintenance).mockResolvedValue({ ok: true, message: 'Metadata cleanup queued' });

    render(<AdminDashboard user={adminUser} onSignIn={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Auto cleanup' }));

    await waitFor(() => expect(runAdminMaintenance).toHaveBeenCalledWith('metadata-cleanup'));
    expect(await screen.findByText('Metadata cleanup queued')).toBeTruthy();
  });

  it('queues credits backfill from the coverage card', async () => {
    vi.mocked(fetchAdminDashboard).mockResolvedValue(dashboard);
    vi.mocked(runAdminMaintenance).mockResolvedValue({ ok: true, message: 'Credits backfill queued' });

    render(<AdminDashboard user={adminUser} onSignIn={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Backfill credits' }));

    await waitFor(() => expect(runAdminMaintenance).toHaveBeenCalledWith('backfill-credits'));
    expect(await screen.findByText('Credits backfill queued')).toBeTruthy();
  });
});
