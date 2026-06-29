import type { ExternalRating } from '../types';

export function formatExternalRating(rating?: ExternalRating | null): string {
  if (!rating) return '';
  const value = typeof rating.value === 'number' ? rating.value : Number(rating.value);
  if (!Number.isFinite(value) || value <= 0) return '';
  const provider = String(rating.provider || 'TMDB').trim() || 'TMDB';
  const label = String(rating.label || value.toFixed(1)).trim();
  return `${provider} ${label}`;
}
