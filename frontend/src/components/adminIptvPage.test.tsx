import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { importAdminIptvM3u, importAdminIptvM3uUrl, saveAdminIptvChannel, testAdminIptvStream } from '../api';
import type { AdminIptvResponse, User } from '../types';
import { AdminIptvPage } from './adminIptvPage';

vi.mock('../api', () => ({
  deleteAdminIptvChannel: vi.fn(),
  importAdminIptvM3u: vi.fn(),
  importAdminIptvM3uUrl: vi.fn(),
  saveAdminIptvChannel: vi.fn(),
  testAdminIptvStream: vi.fn(),
}));

const adminUser: User = {
  sub: 1,
  name: 'Admin',
  username: 'admin',
  photo: '',
  is_admin: true,
  exp: 9999999999,
};

const initialData: AdminIptvResponse = {
  mongoAvailable: true,
  channels: [
    {
      id: 'news',
      name: 'News 24',
      streamUrl: 'https://example.test/news.m3u8',
      logoUrl: '',
      category: 'News',
      tvgId: 'news.us',
      tvgName: 'News 24',
      duration: '-1',
      attrs: { 'tvg-id': 'news.us' },
      extras: ['#EXTVLCOPT:http-user-agent=TeleDirect Test'],
      streamHeaders: { userAgent: 'TeleDirect Test' },
      enabled: true,
      sortOrder: 1,
      createdAt: 1,
      updatedAt: 1,
    },
  ],
};

function renderAdmin(data = initialData) {
  function Wrapper() {
    const [state, setState] = useState<AdminIptvResponse | null>(data);
    return (
      <AdminIptvPage
        user={adminUser}
        data={state}
        loading={false}
        error=""
        onSignIn={vi.fn()}
        reload={vi.fn()}
        setData={setState}
      />
    );
  }
  return render(<Wrapper />);
}

describe('AdminIptvPage', () => {
  it('saves a manually configured channel', async () => {
    const saved = {
      id: 'movies',
      name: 'Movie One',
      streamUrl: 'https://example.test/movies.m3u8',
      logoUrl: '',
      category: 'Movies',
      enabled: true,
      sortOrder: 2,
      createdAt: 1,
      updatedAt: 1,
    };
    vi.mocked(saveAdminIptvChannel).mockResolvedValue({
      ok: true,
      channel: saved,
      channels: [...initialData.channels, saved],
    });
    vi.mocked(testAdminIptvStream).mockResolvedValue({ ok: true, message: 'URL accepted' });

    renderAdmin();

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Movie One' } });
    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'Movies' } });
    fireEvent.change(screen.getByLabelText('Stream URL'), { target: { value: 'https://example.test/movies.m3u8' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(saveAdminIptvChannel).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Movie One',
      streamUrl: 'https://example.test/movies.m3u8',
      category: 'Movies',
    })));
    expect(await screen.findByText('Movie One saved')).toBeTruthy();
    expect(screen.getByText('Movie One')).toBeTruthy();
  });

  it('imports pasted M3U content and refreshes the list', async () => {
    const imported = {
      id: 'sports',
      name: 'Sports Live',
      streamUrl: 'https://example.test/sports.m3u8',
      logoUrl: '',
      category: 'Sports',
      enabled: true,
      sortOrder: 0,
      createdAt: 1,
      updatedAt: 1,
    };
    vi.mocked(importAdminIptvM3u).mockResolvedValue({
      ok: true,
      parsed: 1,
      imported: 1,
      skipped: 0,
      channels: [...initialData.channels, imported],
    });

    renderAdmin();

    fireEvent.change(screen.getByPlaceholderText('#EXTM3U'), {
      target: { value: '#EXTM3U\n#EXTINF:-1,Sports Live\nhttps://example.test/sports.m3u8' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import M3U' }));

    await waitFor(() => expect(importAdminIptvM3u).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Imported 1 of 1 channels')).toBeTruthy();
    expect(screen.getByText('Sports Live')).toBeTruthy();
  });

  it('imports an M3U playlist URL and refreshes the list', async () => {
    const imported = {
      id: 'news-in',
      name: 'News India',
      streamUrl: 'https://example.test/news-india.m3u8',
      logoUrl: '',
      category: 'News',
      enabled: true,
      sortOrder: 0,
      createdAt: 1,
      updatedAt: 1,
    };
    vi.mocked(importAdminIptvM3uUrl).mockResolvedValue({
      ok: true,
      parsed: 1,
      imported: 1,
      skipped: 0,
      sourceUrl: 'https://iptv-org.github.io/iptv/languages/hin.m3u',
      channels: [...initialData.channels, imported],
    });

    renderAdmin();

    fireEvent.change(screen.getByLabelText('Playlist URL'), {
      target: { value: 'https://iptv-org.github.io/iptv/languages/hin.m3u' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import URL' }));

    await waitFor(() => expect(importAdminIptvM3uUrl).toHaveBeenCalledWith('https://iptv-org.github.io/iptv/languages/hin.m3u'));
    expect(await screen.findByText('Imported 1 of 1 channels')).toBeTruthy();
    expect(screen.getByText('News India')).toBeTruthy();
  });

  it('round-trips imported IPTV metadata when editing a channel', async () => {
    vi.mocked(saveAdminIptvChannel).mockResolvedValue({
      ok: true,
      channel: initialData.channels[0],
      channels: initialData.channels,
    });
    vi.mocked(testAdminIptvStream).mockResolvedValue({ ok: true, message: 'Stream reachable' });

    renderAdmin();

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Validate' }));
    await waitFor(() => expect(testAdminIptvStream).toHaveBeenCalledWith(
      'https://example.test/news.m3u8',
      { userAgent: 'TeleDirect Test' },
    ));

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(saveAdminIptvChannel).toHaveBeenCalledWith(expect.objectContaining({
      id: 'news',
      tvgId: 'news.us',
      attrs: { 'tvg-id': 'news.us' },
      extras: ['#EXTVLCOPT:http-user-agent=TeleDirect Test'],
      streamHeaders: { userAgent: 'TeleDirect Test' },
    })));
  });
});
