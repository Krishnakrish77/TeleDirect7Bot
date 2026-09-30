import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
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

  // Category switching moved from tabs to a Radix select. The select renders
  // a combobox; picking an option flips `activeCategory` through onValueChange.
  async function selectCategory(screen: typeof import('@testing-library/react').screen, label: string) {
    fireEvent.click(screen.getByRole('combobox', { name: 'Channel category' }));
    const listbox = await screen.getByRole('listbox');
    fireEvent.click(within(listbox).getByText(new RegExp(`^${label.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')}`)));
  }

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

    await selectCategory(screen, 'News');
    expect(screen.getByRole('heading', { name: 'Movie One' })).toBeTruthy();
    expect(video?.getAttribute('src')).toBe('/api/live-tv/stream/movies');

    fireEvent.change(screen.getByPlaceholderText('Search channels'), { target: { value: 'news' } });
    const rail = screen.getByLabelText('Channels');
    expect(within(rail).getByRole('button', { name: /News 24/i })).toBeTruthy();
    expect(within(rail).queryByRole('button', { name: /Movie One/i })).toBeNull();
    expect(video?.getAttribute('src')).toBe('/api/live-tv/stream/movies');

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

    const rail = screen.getByLabelText('Channels');

    await selectCategory(screen, 'Favorites');
    expect(screen.getByRole('heading', { name: 'Movie One' })).toBeTruthy();
    expect(within(rail).queryByRole('button', { name: /Movie One/i })).toBeNull();
    expect(screen.getByRole('button', { name: 'Add Movie One to favorites' })).toBeTruthy();

    await selectCategory(screen, 'Recent');
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

  it('shows helpful empty copy for favorites and recent views', async () => {
    render(<LiveTvPage data={liveTvData} loading={false} error="" />);

    await selectCategory(screen, 'Favorites');
    expect(screen.getByText('No favorites yet. Use the heart on a channel to save it here.')).toBeTruthy();

    await selectCategory(screen, 'Recent');
    expect(screen.getByText('No recent channels yet. Play a channel and it will appear here.')).toBeTruthy();
  });

  it('shows an empty state when no channels are configured', () => {
    render(<LiveTvPage data={{ channels: [] }} loading={false} error="" />);

    expect(screen.getByText('No IPTV channels are available')).toBeTruthy();
  });

  it('hides probed-offline channels when Active only is toggled', async () => {
    const api = await import('../api');
    const fetchMock = vi
      .spyOn(api, 'fetchLiveTvHealth')
      .mockResolvedValue({ statuses: { news: 'down', movies: 'ok' } });
    const view = render(<LiveTvPage data={liveTvData} loading={false} error="" />);
    const rail = screen.getByLabelText('Channels');
    // No health data yet: every channel listed.
    expect(within(rail).getByRole('button', { name: /News 24/i })).toBeTruthy();

    // Health lands while the toggle is still off — nothing hidden yet.
    await waitFor(() => expect(within(rail).getByRole('button', { name: /Movie One/i }).className).toContain('active'));

    fireEvent.click(screen.getByRole('button', { name: /Active only/i }));
    expect(within(rail).queryByRole('button', { name: /News 24/i })).toBeNull();
    expect(within(rail).getByRole('button', { name: /Movie One/i })).toBeTruthy();
    view.unmount();
    fetchMock.mockRestore();
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

  it('surfaces compound "A;B" categories under both tags', () => {
    const data: LiveTvResponse = {
      channels: [
        { id: 'conan', name: 'Detective Conan', streamUrl: 'https://example.test/conan.ts', logoUrl: '', category: 'Animation;Kids', enabled: true, sortOrder: 1, createdAt: 1, updatedAt: 1 },
        { id: 'cnn', name: 'CNN', streamUrl: 'https://example.test/cnn.ts', logoUrl: '', category: 'News', enabled: true, sortOrder: 2, createdAt: 1, updatedAt: 1 },
      ],
    };
    render(<LiveTvPage data={data} loading={false} error="" />);

    // Dropdown lists both exploded tags with per-tag counts, not a literal "Animation;Kids" chip
    fireEvent.click(screen.getByRole('combobox', { name: 'Channel category' }));
    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getByText(/^Animation/)).toBeTruthy();
    expect(within(listbox).getByText(/^Kids/)).toBeTruthy();
    expect(within(listbox).queryByText(/Animation;Kids/)).toBeNull();
    fireEvent.click(within(listbox).getByText(/^Kids/));

    // Conan shows under Kids; CNN does not
    const rail = screen.getByLabelText('Channels');
    expect(within(rail).getByRole('button', { name: /Detective Conan/i })).toBeTruthy();
    expect(within(rail).queryByRole('button', { name: /CNN/i })).toBeNull();
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
