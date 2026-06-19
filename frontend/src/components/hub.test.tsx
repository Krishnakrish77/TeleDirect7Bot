import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { HubCard, HubParams, HubResponse } from '../types';
import { GridView } from './hub';

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
