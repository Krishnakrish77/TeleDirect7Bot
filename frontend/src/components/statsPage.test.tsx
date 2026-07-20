import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { StatsResponse, User } from '../types';
import { StatsPage } from './statsPage';

const user: User = {
  sub: 1,
  name: 'Viewer',
  username: 'viewer',
  photo: '',
  is_admin: false,
  exp: 9999999999,
};

const stats: StatsResponse = {
  total_seconds: 36000,
  video_seconds: 28800,
  audio_seconds: 7200,
  total_hours: 10,
  total_mins: 0,
  video_hours: 8,
  video_mins: 0,
  audio_hours: 2,
  audio_mins: 0,
  total_plays: 14,
  total_titles: 6,
  in_progress: 0,
  has_activity: true,
  active_days: 5,
  equiv_movies: 5,
  equiv_flights: 3,
  top_title: {
    title: 'Ultimate Spiderman',
    poster: '/thumb/spiderman.jpg',
    url: '/series/ultimate-spiderman',
    media_kind: 'video',
    year: 2012,
    is_series: true,
    count: 4,
  },
  top_title_label: 'Most played',
  most_replayed: [
    {
      title: 'Navarasa',
      poster: '/thumb/navarasa.jpg',
      url: '/album/navarasa',
      media_kind: 'audio',
      year: 2021,
      is_series: false,
      count: 3,
    },
  ],
  top_genres: [['Action', 4]],
  top_genre: 'Action',
  top_director: ['Sam Raimi', 2],
  top_artists: [['Karthik', 3]],
  best_month: ['May 2026', 6],
  finished: 8,
  started: 10,
  n_video: 8,
  n_audio: 2,
  dow_bars: [
    { label: 'Mon', count: 2, pct: 100 },
    { label: 'Tue', count: 1, pct: 50 },
  ],
  best_day: 'Mon',
  tod_label: 'Evening',
  tod_emoji: '',
  timed_plays: 10,
  completion: 80,
  personality: 'Thrill Seeker',
  heatmap: [
    { date: '2026-05-25', count: 0, dow: 0 },
    { date: '2026-05-26', count: 2, dow: 1 },
  ],
  current_streak: 2,
  longest_streak: 5,
  recent_history: [],
  decades: [{ label: '2010s', count: 8 }, { label: '2020s', count: 6 }],
  rewatch_pct: 33,
  rewatch_label: 'A bit of both',
  rewatched_titles: 2,
  genres_explored: 7,
  diversity_label: 'Balanced',
  longest_binge: 4,
  binge_sessions: 2,
};

describe('StatsPage', () => {
  it('prompts guests to sign in', () => {
    const onSignIn = vi.fn();
    render(
      <StatsPage
        user={null}
        data={null}
        loading={false}
        error=""
        onSignIn={onSignIn}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(onSignIn).toHaveBeenCalledTimes(1);
  });

  it('renders totals, top title, replay data, and activity charts', () => {
    render(
      <StatsPage
        user={user}
        data={stats}
        loading={false}
        error=""
        onSignIn={vi.fn()}
      />,
    );

    expect(screen.getByRole('heading', { name: '10h' })).toBeTruthy();
    expect(screen.getByText('14 completed plays across 6 titles')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Ultimate Spiderman/ }).getAttribute('href')).toBe('/app/series/ultimate-spiderman');
    expect(screen.getByText('Navarasa')).toBeTruthy();
    expect(screen.getByText('Action')).toBeTruthy();
    expect(screen.getByLabelText('Activity heatmap').children.length).toBe(2);
  });

  it('shows an empty state instead of zero-valued charts', () => {
    render(
      <StatsPage
        user={user}
        data={{ ...stats, has_activity: false, total_seconds: 0, total_plays: 0, in_progress: 0 }}
        loading={false}
        error=""
        onSignIn={vi.fn()}
      />,
    );

    expect(screen.getByText('No activity yet')).toBeTruthy();
    expect(screen.queryByText('Weekly activity')).toBeNull();
  });
});
