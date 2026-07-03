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

function makeChannel(index: number) {
  return {
    id: `channel-${index}`,
    name: `Channel ${String(index).padStart(3, '0')}`,
    streamUrl: `https://example.test/channel-${index}.ts`,
    logoUrl: `/logo-${index}.png`,
    category: 'General',
    enabled: true,
    sortOrder: index,
    createdAt: 1,
    updatedAt: 1,
  };
}

describe('LiveTvPage', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders channels, filters them, and starts playback only after user intent', async () => {
    const view = render(<LiveTvPage data={liveTvData} loading={false} error="" />);
    const video = view.container.querySelector('video');

    expect(screen.getByRole('heading', { name: 'News 24' })).toBeTruthy();
    expect(video?.getAttribute('src')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Play channel' }));
    await waitFor(() => expect(video?.getAttribute('src')).toBe('/api/live-tv/stream/news'));

    fireEvent.click(screen.getByRole('button', { name: /Movie One/i }));
    expect(screen.getByRole('heading', { name: 'Movie One' })).toBeTruthy();
    await waitFor(() => expect(video?.getAttribute('src')).toBe('/api/live-tv/stream/movies'));

    fireEvent.click(screen.getByRole('tab', { name: /News\s*1/i }));
    expect(screen.getByRole('heading', { name: 'News 24' })).toBeTruthy();
    expect(video?.getAttribute('src')).toBeNull();

    fireEvent.change(screen.getByPlaceholderText('Search channels'), { target: { value: 'news' } });
    const rail = screen.getByLabelText('Channels');
    expect(within(rail).getByRole('button', { name: /News 24/i })).toBeTruthy();
    expect(within(rail).queryByRole('button', { name: /Movie One/i })).toBeNull();

    fireEvent.click(within(rail).getByRole('button', { name: /News 24/i }));
    await waitFor(() => expect(video?.getAttribute('src')).toBe('/api/live-tv/stream/news'));
  });

  it('stores favorite channels and exposes favorites and recent filters', async () => {
    render(<LiveTvPage data={liveTvData} loading={false} error="" />);

    fireEvent.click(screen.getByRole('button', { name: 'Add News 24 to favorites' }));
    expect(JSON.parse(localStorage.getItem('td:live-tv:favorites') || '[]')).toEqual(['news']);

    fireEvent.click(screen.getByRole('button', { name: 'Play channel' }));
    await waitFor(() => expect(JSON.parse(localStorage.getItem('td:live-tv:recent') || '[]')).toEqual(['news']));

    fireEvent.click(screen.getByRole('button', { name: /Movie One/i }));
    await waitFor(() => expect(JSON.parse(localStorage.getItem('td:live-tv:recent') || '[]')).toEqual(['movies', 'news']));

    const tabs = screen.getByRole('tablist', { name: 'Channel categories' });
    const rail = screen.getByLabelText('Channels');

    fireEvent.click(within(tabs).getByRole('tab', { name: /Favorites/i }));
    expect(screen.getByRole('heading', { name: 'News 24' })).toBeTruthy();
    expect(within(rail).queryByRole('button', { name: /Movie One/i })).toBeNull();
    expect(screen.getByRole('button', { name: 'Remove News 24 from favorites' })).toBeTruthy();

    fireEvent.click(within(tabs).getByRole('tab', { name: /Recent/i }));
    expect(within(rail).getByRole('button', { name: /News 24/i })).toBeTruthy();
    expect(within(rail).getByRole('button', { name: /Movie One/i })).toBeTruthy();
  });

  it('summarizes the active channel view and clears empty filters', () => {
    render(<LiveTvPage data={liveTvData} loading={false} error="" />);

    expect(screen.getByText('channels in All channels')).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText('Search channels'), { target: { value: 'zzz' } });

    expect(screen.getByText('No matches for "zzz" in All channels.')).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0]);

    const rail = screen.getByLabelText('Channels');
    expect(within(rail).getByRole('button', { name: /News 24/i })).toBeTruthy();
    expect(within(rail).getByRole('button', { name: /Movie One/i })).toBeTruthy();
    expect(screen.getByText('channels in All channels')).toBeTruthy();
  });

  it('shows helpful empty copy for favorites and recent views', () => {
    render(<LiveTvPage data={liveTvData} loading={false} error="" />);
    const tabs = screen.getByRole('tablist', { name: 'Channel categories' });

    fireEvent.click(within(tabs).getByRole('tab', { name: /Favorites/i }));
    expect(screen.getByText('No favorites yet. Use the heart on a channel to save it here.')).toBeTruthy();

    fireEvent.click(within(tabs).getByRole('tab', { name: /Recent/i }));
    expect(screen.getByText('No recent channels yet. Play a channel and it will appear here.')).toBeTruthy();
  });

  it('shows an empty state when no channels are configured', () => {
    render(<LiveTvPage data={{ channels: [] }} loading={false} error="" />);

    expect(screen.getByText('No IPTV channels are available')).toBeTruthy();
  });

  it('falls back to the broadcast icon when a channel logo fails', async () => {
    const brokenLogoData: LiveTvResponse = {
      channels: [{
        ...liveTvData.channels[0],
        id: 'broken-logo',
        name: 'Broken Logo',
        logoUrl: '/missing-logo.png',
      }],
    };
    const view = render(<LiveTvPage data={brokenLogoData} loading={false} error="" />);

    const nowLogo = view.container.querySelector('.live-now-copy img');
    expect(nowLogo).toBeTruthy();

    fireEvent.error(nowLogo as Element);

    await waitFor(() => {
      expect(view.container.querySelector('.live-now-copy img')).toBeNull();
      const row = view.container.querySelector('.live-channel-row') as HTMLElement;
      expect(row).toBeTruthy();
      expect(row.querySelector('img')).toBeNull();
      expect(Array.from(row.children).some((child) => child.tagName === 'SPAN')).toBe(true);
    });
  });

  it('renders large channel lists in batches', () => {
    const manyChannels: LiveTvResponse = {
      channels: Array.from({ length: 95 }, (_, index) => makeChannel(index + 1)),
    };
    const view = render(<LiveTvPage data={manyChannels} loading={false} error="" />);

    expect(view.container.querySelectorAll('.live-channel-row')).toHaveLength(80);
    expect(screen.queryByRole('button', { name: /Channel 081/i })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Show more/i }));

    expect(view.container.querySelectorAll('.live-channel-row')).toHaveLength(95);
    expect(screen.getByRole('button', { name: /Channel 095/i })).toBeTruthy();
  });
});
