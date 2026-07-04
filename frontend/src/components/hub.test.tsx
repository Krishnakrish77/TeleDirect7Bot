import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAllContinue, deleteContinueEntry, fetchContinueItems, fetchContinueMap } from '../api';
import type { HubCard, HubParams, HubResponse } from '../types';
import { budgetHomeShelves, ContinueWatching, GridView, HOME_SHELF_LIMIT, RecommendationTeaser, shelfPresentation, sortHomeShelves } from './hub';

vi.mock('../api', () => ({
  clearAllContinue: vi.fn(),
  deleteContinueEntry: vi.fn(),
  dismissRecommendation: vi.fn(),
  fetchContinueItems: vi.fn(),
  fetchContinueMap: vi.fn(),
}));

const params: HubParams = {
  q: '',
  tag: '',
  quality: '',
  genre: '',
  year: null,
  sort: 'newest',
  view: 'movies',
  offset: 0,
  limit: 24,
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

function card(overrides: Partial<HubCard> = {}): HubCard {
  return {
    type: 'movie',
    itemId: 'movie:1',
    messageId: 1,
    secureHash: 'hash1',
    title: 'Kalki',
    subtitle: '2024 - 1080p',
    year: 2024,
    mediaKind: 'video',
    posterUrl: '/thumb/hash1.jpg',
    thumbUrl: '/thumb/hash1.jpg',
    backdropUrl: '/thumb/hash1.jpg',
    duration: 8400,
    durationLabel: '2h 20m',
    fileSize: 1000,
    fileSizeLabel: '1 GB',
    quality: '1080p',
    genres: ['Action'],
    tags: [],
    overview: '',
    artist: '',
    albumTitle: '',
    trailerKey: '',
    href: '/app/movie/kalki',
    playHref: '/app/watch/hash1',
    detailsHref: '/app/movie/kalki',
    streamHref: '/hash1',
    watchKey: 'hash1',
    eyebrow: 'Movie',
    badge: '1080p',
    aspect: 'poster',
    variantCount: 1,
    ...overrides,
  };
}

function response(overrides: Partial<HubResponse> = {}): HubResponse {
  return {
    mode: 'grid',
    params,
    filters: {
      years: [],
      qualities: [],
      genres: [],
      tags: [],
      sortOptions: [{ value: 'newest', label: 'Newest' }],
      views: [{ value: 'movies', label: 'Movies' }],
    },
    catalogueSize: 1,
    heroes: [],
    shelves: [],
    items: [card()],
    total: 1,
    nextOffset: 24,
    nextHref: null,
    emptyText: 'No matching titles',
    ...overrides,
  };
}

describe('GridView', () => {
  it('uses a singular result label for one item', () => {
    render(
      <GridView
        data={response()}
        params={params}
        saved={new Set()}
        update={vi.fn()}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.getByText('1 result')).toBeTruthy();
  });

  it('shows a refresh status without stale load-more pagination during filter updates', () => {
    render(
      <GridView
        data={response({ total: 6 })}
        params={{ ...params, q: 'kalki' }}
        saved={new Set()}
        update={vi.fn()}
        onToggleSaved={vi.fn()}
        loading
      />,
    );

    expect(screen.getAllByText('Updating results')).toHaveLength(1);
    expect(screen.getByRole('status').textContent).toBe('Updating results...');
    expect(screen.queryByRole('button', { name: /Loading/i })).toBeNull();
  });

  it('keeps load-more feedback when the next page is loading', () => {
    render(
      <GridView
        data={response({ nextOffset: 48 })}
        params={{ ...params, offset: 24 }}
        saved={new Set()}
        update={vi.fn()}
        onToggleSaved={vi.fn()}
        loading
      />,
    );

    expect((screen.getByRole('button', { name: /Loading/i }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('home shelf helpers', () => {
  it('promotes high-signal shelves before generic rows', () => {
    const shelves = [
      { name: 'Action', href: null, items: [card()] },
      { name: 'Trending', href: null, items: [card({ itemId: 'movie:2' })] },
      { name: 'Recently added movies', href: null, items: [card({ itemId: 'movie:3' })] },
      { name: 'Recommended for you', href: null, items: [card({ itemId: 'movie:4' })] },
      { name: 'Most Played', href: null, items: [card({ itemId: 'movie:5' })] },
      { name: 'Because you like Mystery', href: null, items: [card({ itemId: 'movie:6' })] },
    ];

    expect(sortHomeShelves(shelves).map((shelf) => shelf.name)).toEqual([
      'Recommended for you',
      'Because you like Mystery',
      'Trending',
      'Most Played',
      'Recently added movies',
      'Action',
    ]);
  });

  it('renames key shelves for clearer home presentation', () => {
    expect(shelfPresentation('Recently added')).toEqual({ title: 'New in your library', eyebrow: 'Latest' });
    expect(shelfPresentation('Most Played')).toEqual({ title: 'Most played', eyebrow: 'Replay value' });
    expect(shelfPresentation('Hidden gems')).toEqual({ title: 'Worth a look', eyebrow: 'Discovery' });
  });

  it('caps rendered home shelves to the governance budget', () => {
    const shelves = [
      'Recommended for you',
      'Because you like Mystery',
      'Recently added',
      'New episodes',
      'Trending',
      'Most Played',
      'Music',
      'Series',
      'Recently added movies',
      'Hidden gems',
      'Action',
      'Empty editorial row',
    ].map((name, index) => ({
      name,
      href: null,
      items: name === 'Empty editorial row' ? [] : [card({ itemId: `movie:${index}` })],
    }));

    const budgeted = budgetHomeShelves(shelves);

    expect(budgeted).toHaveLength(HOME_SHELF_LIMIT);
    expect(budgeted.map((shelf) => shelf.name)).toEqual([
      'Recommended for you',
      'Because you like Mystery',
      'Recently added',
      'New episodes',
      'Trending',
      'Most Played',
      'Music',
    ]);
  });
});

describe('RecommendationTeaser', () => {
  it('opens sign in when the teaser action is clicked', () => {
    const onSignIn = vi.fn();

    render(<RecommendationTeaser onSignIn={onSignIn} />);
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(screen.getByText('Personal picks unlock after sign-in')).toBeTruthy();
    expect(onSignIn).toHaveBeenCalledTimes(1);
  });
});

describe('ContinueWatching', () => {
  it('removes continue entries from server sync when dismissed', async () => {
    localStorage.setItem('td:cw', JSON.stringify({
      hash1: { pos: 120, dur: 1200, t: 10, title: 'Kalki' },
    }));
    vi.mocked(fetchContinueMap).mockResolvedValue({
      hash1: { pos: 120, dur: 1200, t: 10, title: 'Kalki' },
    });
    vi.mocked(fetchContinueItems).mockResolvedValue([
      {
        key: 'hash1',
        title: 'Kalki',
        series_title: '',
        episode_label: '',
        year: 2024,
        poster_path: '',
        thumb_url: '/thumb/hash1.jpg',
        watch_url: '/watch/hash1',
        kind: 'movie',
        media_kind: 'video',
        next_episode: null,
      },
    ]);
    vi.mocked(deleteContinueEntry).mockResolvedValue(undefined);

    render(<ContinueWatching serverSyncEnabled />);

    expect(await screen.findAllByText('Kalki')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));

    await waitFor(() => expect(deleteContinueEntry).toHaveBeenCalledWith('hash1'));
    expect(screen.queryAllByText('Kalki')).toHaveLength(0);
  });

  it('uses local continue data without server sync for anonymous users', async () => {
    localStorage.setItem('td:cw', JSON.stringify({
      hash1: { pos: 120, dur: 1200, t: 10, title: 'Kalki' },
    }));
    vi.mocked(fetchContinueItems).mockResolvedValue([
      {
        key: 'hash1',
        title: 'Kalki',
        series_title: '',
        episode_label: '',
        year: 2024,
        poster_path: '',
        thumb_url: '/thumb/hash1.jpg',
        watch_url: '/watch/hash1',
        kind: 'movie',
        media_kind: 'video',
        next_episode: null,
      },
    ]);

    render(<ContinueWatching />);

    expect(await screen.findAllByText('Kalki')).toHaveLength(2);
    expect(fetchContinueMap).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }));

    expect(clearAllContinue).not.toHaveBeenCalled();
    expect(deleteContinueEntry).not.toHaveBeenCalled();
    expect(localStorage.getItem('td:cw')).toBeNull();
    expect(screen.queryAllByText('Kalki')).toHaveLength(0);
  });
});
