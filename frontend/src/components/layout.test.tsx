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
    poster_path: '',
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
  it('keeps watchlist unavailable from guest navigation', () => {
    render(<PrimaryNav user={null} activeView="" activeSection="home" />);

    expect(screen.queryByRole('link', { name: /Watchlist/i })).toBeNull();
    expect(screen.queryByRole('link', { name: /Search/i })).toBeNull();
    expect(screen.getByRole('link', { name: /Live TV/i }).getAttribute('href')).toBe('/app/live-tv');
  });

  it('shows watchlist only for signed-in users', () => {
    render(<PrimaryNav user={user} activeView="" activeSection="watchlist" />);

    expect(screen.getByRole('link', { name: /Watchlist/i }).getAttribute('href')).toBe('/app/watchlist');
  });
});

describe('Header search', () => {
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

  it('closes search suggestions when clicking outside', () => {
    vi.mocked(useSuggestions).mockReturnValue(suggestions);
    renderHeader({ query: 'ka' });

    fireEvent.focus(screen.getByPlaceholderText('Search library'));
    expect(screen.getByRole('listbox')).toBeTruthy();

    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
