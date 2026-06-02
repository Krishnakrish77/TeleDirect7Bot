import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MeResponse, User } from '../types';
import { Header, PrimaryNav } from './layout';

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
      onSignIn={vi.fn()}
      onSignOut={vi.fn()}
      {...props}
    />,
  );
}

describe('PrimaryNav', () => {
  it('keeps watchlist unavailable from guest navigation', () => {
    render(<PrimaryNav user={null} activeView="" activeSection="home" />);

    expect(screen.queryByRole('link', { name: /Watchlist/i })).toBeNull();
    expect(screen.queryByRole('link', { name: /Search/i })).toBeNull();
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
});
