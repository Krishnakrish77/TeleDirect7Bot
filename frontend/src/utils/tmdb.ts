export function tmdbImageUrl(path: string, size: 'w92' | 'w185' | 'w342' | 'w500' | 'w780' | 'original'): string {
  const cleanPath = String(path || '').replace(/^\/+/, '');
  if (!cleanPath) return '';
  return `/api/tmdb-image/${encodeURIComponent(size)}/${cleanPath.split('/').map(encodeURIComponent).join('/')}`;
}
