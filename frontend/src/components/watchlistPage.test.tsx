import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { User, WatchlistItem, WatchlistPageResponse } from '../types';
import { WatchlistPage, watchlistCard } from './watchlistPage';

const user: User = {
  sub: 1,
  name: 'Viewer',
  username: 'viewer',
  photo: '',
  is_admin: false,
  exp: 9999999999,
};

const movieItem: WatchlistItem = {
  item_id: 'movie:kalki',
  url: '/movie/kalki',
  title: 'Kalki',
  year: 2024,
  poster: '/thumb/kalki.jpg',
  kind: 'movie',
  subtitle: '2 versions',
};

const seriesItem: WatchlistItem = {
  item_id: 'series:dark',
  url: '/series/dark',
  title: 'Dark',
  year: 2020,
  poster: '/thumb/dark.jpg',
  kind: 'series',
  subtitle: '3 seasons',
};

const audioItem: WatchlistItem = {
  item_id: 'audio:theme',
  url: '/watch/theme42',
  title: 'Theme',
  year: 2023,
  poster: '/thumb/theme.jpg',
  kind: 'audio',
  subtitle: 'Anirudh',
};

const response: WatchlistPageResponse = {
  items: [movieItem],
  mongoAvailable: true,
};

describe('WatchlistPage', () => {
  it('prompts guests to sign in', () => {
    const onSignIn = vi.fn();
    render(
      <WatchlistPage
        user={null}
        data={null}
        loading={false}
        error=""
        onToggleSaved={vi.fn()}
        onSignIn={onSignIn}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(onSignIn).toHaveBeenCalledTimes(1);
  });

  it('renders saved items as app links and removes from the page', () => {
    const onToggleSaved = vi.fn();
    render(
      <WatchlistPage
        user={user}
        data={response}
        loading={false}
        error=""
        onToggleSaved={onToggleSaved}
        onSignIn={vi.fn()}
      />,
    );

    expect(screen.getByRole('heading', { name: 'Watchlist' })).toBeTruthy();
    expect(screen.getByRole('link', { name: /Kalki/ }).getAttribute('href')).toBe('/app/movie/kalki');

    fireEvent.click(screen.getByLabelText('Remove from watchlist'));
    expect(onToggleSaved).toHaveBeenCalledWith(expect.objectContaining({ itemId: 'movie:kalki' }));
  });

  it('filters, searches, and sorts saved items', () => {
    render(
      <WatchlistPage
        user={user}
        data={{ ...response, items: [movieItem, seriesItem, audioItem] }}
        loading={false}
        error=""
        onToggleSaved={vi.fn()}
        onSignIn={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Music/i }));
    expect(screen.getByRole('link', { name: /Theme/i })).toBeTruthy();
    expect(screen.queryByRole('link', { name: /Kalki/i })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /All/i }));
    fireEvent.change(screen.getByPlaceholderText('Search saved titles'), { target: { value: 'dark' } });
    expect(screen.getByRole('link', { name: /Dark/i })).toBeTruthy();
    expect(screen.queryByRole('link', { name: /Theme/i })).toBeNull();

    fireEvent.change(screen.getByPlaceholderText('Search saved titles'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Sort'), { target: { value: 'title' } });
    const titles = screen.getAllByRole('link').map((link) => link.textContent || '');
    expect(titles[0]).toContain('Dark');
    expect(titles[1]).toContain('Kalki');
    expect(titles[2]).toContain('Theme');
  });
});

describe('watchlistCard', () => {
  it('routes direct watch items to the React watch player', () => {
    const card = watchlistCard({
      ...movieItem,
      item_id: '42',
      url: '/watch/hash42',
      kind: 'video',
    });

    expect(card.href).toBe('/app/watch/hash42');
    expect(card.type).toBe('item');
  });
});
