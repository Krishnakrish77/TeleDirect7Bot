import { describe, expect, it } from 'vitest';
import { tmdbImageUrl } from './tmdb';

describe('tmdbImageUrl', () => {
  it('uses the same-origin TMDB image proxy', () => {
    expect(tmdbImageUrl('/abc_DEF-123.jpg', 'w342')).toBe('/api/tmdb-image/w342/abc_DEF-123.jpg');
    expect(tmdbImageUrl('', 'w342')).toBe('');
  });

  it('encodes path segments without flattening nested paths', () => {
    expect(tmdbImageUrl('/nested/poster name.jpg', 'w92')).toBe('/api/tmdb-image/w92/nested/poster%20name.jpg');
  });
});
