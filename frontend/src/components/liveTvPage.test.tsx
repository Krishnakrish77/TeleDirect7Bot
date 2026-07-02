import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import type { LiveTvResponse } from '../types';
import { LiveTvPage } from './liveTvPage';

const liveTvData: LiveTvResponse = {
  channels: [
    {
      id: 'news',
      name: 'News 24',
      streamUrl: 'https://example.test/news.ts',
      logoUrl: '/logo-news.png',
      category: 'News',
      enabled: true,
      sortOrder: 1,
      createdAt: 1,
      updatedAt: 1,
    },
    {
      id: 'movies',
      name: 'Movie One',
      streamUrl: 'https://example.test/movies.ts',
      logoUrl: '',
      category: 'Movies',
      enabled: true,
      sortOrder: 2,
      createdAt: 1,
      updatedAt: 1,
    },
  ],
};

describe('LiveTvPage', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders channels, filters them, and switches the video source', () => {
    const view = render(<LiveTvPage data={liveTvData} loading={false} error="" />);

    expect(screen.getByRole('heading', { name: 'News 24' })).toBeTruthy();
    expect(view.container.querySelector('video')?.getAttribute('src')).toBe('https://example.test/news.ts');

    fireEvent.click(screen.getByRole('button', { name: /Movie One/i }));
    expect(screen.getByRole('heading', { name: 'Movie One' })).toBeTruthy();
    expect(view.container.querySelector('video')?.getAttribute('src')).toBe('https://example.test/movies.ts');

    fireEvent.click(screen.getByRole('button', { name: /News1/i }));
    expect(screen.getByRole('heading', { name: 'News 24' })).toBeTruthy();
    expect(view.container.querySelector('video')?.getAttribute('src')).toBe('https://example.test/news.ts');

    fireEvent.change(screen.getByPlaceholderText('Search channels'), { target: { value: 'news' } });
    const rail = screen.getByLabelText('Channels');
    expect(within(rail).getByRole('button', { name: /News 24/i })).toBeTruthy();
    expect(within(rail).queryByRole('button', { name: /Movie One/i })).toBeNull();
  });

  it('stores favorite channels and exposes favorites and recent filters', async () => {
    render(<LiveTvPage data={liveTvData} loading={false} error="" />);

    fireEvent.click(screen.getByRole('button', { name: 'Add News 24 to favorites' }));
    expect(JSON.parse(localStorage.getItem('td:live-tv:favorites') || '[]')).toEqual(['news']);

    fireEvent.click(screen.getByRole('button', { name: /Movie One/i }));
    await waitFor(() => expect(JSON.parse(localStorage.getItem('td:live-tv:recent') || '[]')).toEqual(['movies', 'news']));

    const tabs = screen.getByRole('tablist', { name: 'Channel categories' });
    const rail = screen.getByLabelText('Channels');

    fireEvent.click(within(tabs).getByRole('button', { name: /Favorites/i }));
    expect(screen.getByRole('heading', { name: 'News 24' })).toBeTruthy();
    expect(within(rail).queryByRole('button', { name: /Movie One/i })).toBeNull();
    expect(screen.getByRole('button', { name: 'Remove News 24 from favorites' })).toBeTruthy();

    fireEvent.click(within(tabs).getByRole('button', { name: /Recent/i }));
    expect(within(rail).getByRole('button', { name: /News 24/i })).toBeTruthy();
    expect(within(rail).getByRole('button', { name: /Movie One/i })).toBeTruthy();
  });

  it('shows an empty state when no channels are configured', () => {
    render(<LiveTvPage data={{ channels: [] }} loading={false} error="" />);

    expect(screen.getByText('No IPTV channels are available')).toBeTruthy();
  });
});
