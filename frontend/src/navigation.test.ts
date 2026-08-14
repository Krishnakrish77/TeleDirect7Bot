import { describe, expect, it } from 'vitest';
import { localAppHref, parseRoute } from './navigation';

describe('React app navigation', () => {
  it('parses canonical root routes while accepting old app links', () => {
    expect(parseRoute('/filters')).toEqual({ kind: 'filters' });
    expect(parseRoute('/books')).toEqual({ kind: 'books' });
    expect(parseRoute('/watchlist')).toEqual({ kind: 'watchlist' });
    expect(parseRoute('/playlists')).toEqual({ kind: 'playlists' });
    expect(parseRoute('/playlist/1234567890abcdef1234567890abcdef')).toEqual({
      kind: 'playlist',
      playlistId: '1234567890abcdef1234567890abcdef',
    });
    expect(parseRoute('/live-tv')).toEqual({ kind: 'live-tv' });
    expect(parseRoute('/admin/iptv')).toEqual({ kind: 'admin-iptv' });
    expect(parseRoute('/stats')).toEqual({ kind: 'stats' });
    expect(parseRoute('/app/watch/track-key')).toEqual({ kind: 'watch', key: 'track-key' });
  });

  it('normalizes retired app routes into canonical root paths', () => {
    expect(localAppHref('/app/watchlist')).toBe('/watchlist');
    expect(localAppHref('/app/playlists')).toBe('/playlists');
    expect(localAppHref('/app/playlist/1234567890abcdef1234567890abcdef')).toBe('/playlist/1234567890abcdef1234567890abcdef');
    expect(localAppHref('/app/live-tv')).toBe('/live-tv');
    expect(localAppHref('/app/stats')).toBe('/stats');
    expect(localAppHref('/app/watch/track-key')).toBe('/play/track-key');
  });
});
