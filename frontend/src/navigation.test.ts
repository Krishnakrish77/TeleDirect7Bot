import { describe, expect, it } from 'vitest';
import { classicPathForApp, localAppHref, parseRoute } from './navigation';

describe('React app navigation', () => {
  it('parses app watchlist, playlist, live TV, admin IPTV, and stats routes', () => {
    expect(parseRoute('/app/filters')).toEqual({ kind: 'filters' });
    expect(parseRoute('/app/watchlist')).toEqual({ kind: 'watchlist' });
    expect(parseRoute('/app/playlists')).toEqual({ kind: 'playlists' });
    expect(parseRoute('/app/playlist/1234567890abcdef1234567890abcdef')).toEqual({
      kind: 'playlist',
      playlistId: '1234567890abcdef1234567890abcdef',
    });
    expect(parseRoute('/app/live-tv')).toEqual({ kind: 'live-tv' });
    expect(parseRoute('/app/admin/iptv')).toEqual({ kind: 'admin-iptv' });
    expect(parseRoute('/app/stats')).toEqual({ kind: 'stats' });
  });

  it('maps classic watchlist, playlist, live TV, and stats links into the app shell', () => {
    expect(localAppHref('/watchlist')).toBe('/app/watchlist');
    expect(localAppHref('/playlists')).toBe('/app/playlists');
    expect(localAppHref('/playlist/1234567890abcdef1234567890abcdef')).toBe('/app/playlist/1234567890abcdef1234567890abcdef');
    expect(localAppHref('/live-tv')).toBe('/app/live-tv');
    expect(localAppHref('/stats')).toBe('/app/stats');
  });

  it('maps app watchlist, playlist, live TV, and stats routes back to classic URLs', () => {
    expect(classicPathForApp('/app/filters', '?view=movies')).toBe('/?view=movies');
    expect(classicPathForApp('/app/watchlist', '')).toBe('/watchlist');
    expect(classicPathForApp('/app/playlists', '')).toBe('/?view=music');
    expect(classicPathForApp('/app/playlist/1234567890abcdef1234567890abcdef', '')).toBe('/?view=music');
    expect(classicPathForApp('/app/live-tv', '')).toBe('/live-tv');
    expect(classicPathForApp('/app/stats', '')).toBe('/stats');
  });
});
