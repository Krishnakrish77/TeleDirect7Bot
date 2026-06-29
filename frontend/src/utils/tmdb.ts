const BASE = 'https://image.tmdb.org/t/p';

export function tmdbImageUrl(path: string, size: 'w92' | 'w185' | 'w342' | 'w500' | 'w780' | 'original'): string {
  return `${BASE}/${size}${path}`;
}
