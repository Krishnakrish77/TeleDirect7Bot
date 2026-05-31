import { describe, expect, it } from 'vitest';
import { classicPathForApp, localAppHref, parseRoute } from './navigation';

describe('React app navigation', () => {
  it('parses app watchlist and stats routes', () => {
    expect(parseRoute('/app/watchlist')).toEqual({ kind: 'watchlist' });
    expect(parseRoute('/app/stats')).toEqual({ kind: 'stats' });
  });

  it('maps classic watchlist and stats links into the app shell', () => {
    expect(localAppHref('/watchlist')).toBe('/app/watchlist');
    expect(localAppHref('/stats')).toBe('/app/stats');
  });

  it('maps app watchlist and stats routes back to classic URLs', () => {
    expect(classicPathForApp('/app/watchlist', '')).toBe('/watchlist');
    expect(classicPathForApp('/app/stats', '')).toBe('/stats');
  });
});
