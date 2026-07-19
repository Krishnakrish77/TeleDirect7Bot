import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSuggestions } from '../hooks/data';
import type { MeResponse, Suggestion, User } from '../types';
import { Header, PrimaryNav } from './layout';

vi.mock('../hooks/data', () => ({
  useSuggestions: vi.fn(),
}));

const user: User = {
  sub: 1,
  name: 'Viewer',
  username: 'viewer',
  photo: '',
  is_admin: false,
  exp: 9999999999,
};

const me: MeResponse = {
  user,
  botUsername: 'TeleDirectBot',
  app: {
    name: 'TeleDirect',
    spaPath: '/app',
  },
};

function renderHeader(props: Partial<Parameters<typeof Header>[0]> = {}) {
  return render(
    <Header
      me={me}
      user={user}
      query=""
      setQuery={vi.fn()}
      searchRef={createRef<HTMLInputElement>()}
      accountOpen={false}
      setAccountOpen={vi.fn()}
      classicUiHref="/ui/classic?next=%2F"
      onSearchSubmit={vi.fn()}
      onSearchClear={vi.fn()}
      onSuggestionNavigate={vi.fn()}
      onSignIn={vi.fn()}
      onSignOut={vi.fn()}
      {...props}
    />,
  );
}

const suggestions: Suggestion[] = [
  {
    title: 'Kalki',
    year: 2024,
    kind: 'movie',
    url: '/movie/kalki',
    poster_path: '/abc_DEF-123.jpg',
    secure_hash: 'hash',
    message_id: 42,
  },
  {
    title: 'Kaithi',
    year: 2019,
    kind: 'movie',
    url: '/movie/kaithi',
    poster_path: '',
    secure_hash: 'hash2',
    message_id: 43,
  },
];

beforeEach(() => {
  vi.mocked(useSuggestions).mockReturnValue([]);
});

describe('PrimaryNav', () => {
  it('shows the five public destinations without a more menu', () => {
    render(<PrimaryNav user={null} activeView="" activeSection="home" />);

    expect(screen.queryByRole('link', { name: /Watchlist/i })).toBeNull();
    expect(screen.queryByRole('link', { name: /Search/i })).toBeNull();
    expect(screen.getByRole('link', { name: /Live TV/i }).getAttribute('href')).toBe('/app/live-tv');
    expect(screen.getByRole('link', { name: /Series/i }).getAttribute('href')).toBe('/app?view=series');
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
  });

  it('keeps the personal library in the signed-in account menu', () => {
    renderHeader({ accountOpen: true });

    expect(screen.getByRole('menuitem', { name: /Watchlist/i }).getAttribute('href')).toBe('/app/watchlist');
    expect(screen.getByRole('menuitem', { name: /Liked songs/i }).getAttribute('href')).toBe('/app/liked-songs');
    expect(screen.getByRole('menuitem', { name: /Playlists/i }).getAttribute('href')).toBe('/app/playlists');
    expect(screen.getByRole('menuitem', { name: /Stats/i }).getAttribute('href')).toBe('/app/stats');
  });
});

describe('Header search', () => {
  it('falls back to the user initial when the Telegram avatar URL fails', () => {
    const view = renderHeader({ user: { ...user, photo: 'https://cdn.telegram.test/avatar.jpg' } });
    const avatar = view.container.querySelector('.profile-avatar img');
    expect(avatar).not.toBeNull();
    fireEvent.error(avatar!);
    expect(view.container.querySelector('.profile-avatar img')).toBeNull();
    expect(screen.getByText('V')).toBeTruthy();
  });

  it('submits search explicitly', () => {
    const onSearchSubmit = vi.fn();
    renderHeader({ query: 'kalki', onSearchSubmit });

    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(onSearchSubmit).toHaveBeenCalledTimes(1);
  });

  it('clears search explicitly', () => {
    const onSearchClear = vi.fn();
    renderHeader({ query: 'kalki', onSearchClear });

    fireEvent.click(screen.getByRole('button', { name: 'Clear search' }));
    expect(onSearchClear).toHaveBeenCalledTimes(1);
  });

  it('routes admins to the React admin panel', () => {
    renderHeader({
      user: { ...user, is_admin: true },
      accountOpen: true,
    });

    expect(screen.getByRole('menuitem', { name: /Admin panel/i }).getAttribute('href')).toBe('/app/admin');
    expect(screen.getByRole('menuitem', { name: /Playlists/i }).getAttribute('href')).toBe('/app/playlists');
    expect(screen.getByRole('menuitem', { name: /Stats/i }).getAttribute('href')).toBe('/app/stats');
  });

  it('supports keyboard navigation through search suggestions', () => {
    const onSuggestionNavigate = vi.fn();
    vi.mocked(useSuggestions).mockReturnValue(suggestions);
    renderHeader({ query: 'ka', onSuggestionNavigate });

    const input = screen.getByPlaceholderText('Search library');
    fireEvent.focus(input);
    fireEvent.keyDown(input, { key: 'ArrowDown' });

    expect(screen.getByRole('option', { name: /Kalki/i }).getAttribute('aria-selected')).toBe('true');

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(screen.getByRole('option', { name: /Kaithi/i }).getAttribute('aria-selected')).toBe('true');

    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSuggestionNavigate).toHaveBeenCalledWith('/app/movie/kaithi');
  });

  it('uses the same-origin TMDB image proxy for search suggestions', () => {
    vi.mocked(useSuggestions).mockReturnValue(suggestions);
    const view = renderHeader({ query: 'ka' });

    fireEvent.focus(screen.getByPlaceholderText('Search library'));

    const images = view.container.querySelectorAll('.suggestion-art img');
    expect(images[0].getAttribute('src')).toBe('/api/tmdb-image/w92/abc_DEF-123.jpg');
    expect(images[1].getAttribute('src')).toBe('/thumb/hash243.jpg');
  });

  it('cache-busts audio fallback art in search suggestions', () => {
    vi.mocked(useSuggestions).mockReturnValue([
      {
        title: 'Indra',
        year: null,
        kind: 'album',
        url: '/album/indra',
        poster_path: '',
        secure_hash: 'songhash',
        message_id: 99,
        media_kind: 'audio',
      },
    ]);
    const view = renderHeader({ query: 'indra' });

    fireEvent.focus(screen.getByPlaceholderText('Search library'));

    const image = view.container.querySelector('.suggestion-art img');
    expect(image?.getAttribute('src')).toBe('/thumb/songhash99.jpg?v=audio3');
  });

  it('closes search suggestions when clicking outside', () => {
    vi.mocked(useSuggestions).mockReturnValue(suggestions);
    renderHeader({ query: 'ka' });

    fireEvent.focus(screen.getByPlaceholderText('Search library'));
    expect(screen.getByRole('listbox')).toBeTruthy();

    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
