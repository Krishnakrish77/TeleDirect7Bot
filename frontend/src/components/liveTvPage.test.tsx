import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
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
    expect(screen.getByRole('button', { name: /News 24/i })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Movie One/i })).toBeNull();
  });

  it('shows an empty state when no channels are configured', () => {
    render(<LiveTvPage data={{ channels: [] }} loading={false} error="" />);

    expect(screen.getByText('No IPTV channels are available')).toBeTruthy();
  });
});
