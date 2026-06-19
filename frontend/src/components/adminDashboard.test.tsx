import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { fetchAdminDashboard } from '../api';
import type { AdminDashboardResponse, User } from '../types';
import { AdminDashboard } from './adminDashboard';

vi.mock('../api', () => ({
  fetchAdminDashboard: vi.fn(),
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
    missing_overview: 2,
    missing_year: 1,
    missing_cast: 3,
    missing_episode_metadata: 4,
    missing_playback_markers: 5,
    health_score: 70,
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
});
