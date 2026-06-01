import type {
  ContinueItem,
  DetailResponse,
  HubParams,
  HubResponse,
  MeResponse,
  StatsResponse,
  AudioTrackOption,
  ContinueEntry,
  ContinueMap,
  SubtitleTrack,
  RatingResponse,
  Suggestion,
  TelegramAuthUser,
  WatchResponse,
  WatchlistPageResponse,
} from './types';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: 'same-origin',
  });
  if (!response.ok) {
    throw new ApiError(response.statusText || 'Request failed', response.status);
  }
  return (await response.json()) as T;
}

export function hubSearchParams(params: Partial<HubParams>): URLSearchParams {
  const qs = new URLSearchParams();
  if (params.q) qs.set('q', params.q);
  if (params.tag) qs.set('tag', params.tag);
  if (params.quality) qs.set('quality', params.quality);
  if (params.genre) qs.set('genre', params.genre);
  if (params.year) qs.set('year', String(params.year));
  if (params.sort && params.sort !== 'newest') qs.set('sort', params.sort);
  if (params.view) qs.set('view', params.view);
  if (params.offset) qs.set('offset', String(params.offset));
  if (params.limit && params.limit !== 24) qs.set('limit', String(params.limit));
  return qs;
}

export function hubParamsKey(params: Partial<HubParams>): string {
  return hubSearchParams(params).toString();
}

export async function fetchHub(
  params: Partial<HubParams>,
  signal?: AbortSignal,
): Promise<HubResponse> {
  const qs = hubSearchParams(params);
  const suffix = qs.toString() ? `?${qs}` : '';
  return request<HubResponse>(`/api/hub${suffix}`, { signal });
}

export async function fetchMe(signal?: AbortSignal): Promise<MeResponse> {
  return request<MeResponse>('/api/me', { signal });
}

export async function fetchWatch(key: string, signal?: AbortSignal): Promise<WatchResponse> {
  return request<WatchResponse>(`/api/watch/${encodeURIComponent(key)}`, { signal });
}

export async function fetchDetail(
  kind: 'movie' | 'series' | 'album' | 'artist' | 'person',
  key: string,
  search = '',
  signal?: AbortSignal,
): Promise<DetailResponse> {
  const suffix = search || '';
  const pathKey = encodeURIComponent(key).replace(/%3A/gi, ':');
  return request<DetailResponse>(`/api/app/${kind}/${pathKey}${suffix}`, { signal });
}

export async function fetchSubtitles(base: string, signal?: AbortSignal): Promise<SubtitleTrack[]> {
  if (!base) return [];
  return request<SubtitleTrack[]>(`${base}/list.json`, { signal });
}

export async function fetchAudioTracks(base: string, signal?: AbortSignal): Promise<AudioTrackOption[]> {
  if (!base) return [];
  return request<AudioTrackOption[]>(`${base}/audio-list.json`, { signal });
}

export async function fetchSuggestions(q: string, signal?: AbortSignal): Promise<Suggestion[]> {
  if (!q.trim()) return [];
  const qs = new URLSearchParams({ q, limit: '8' });
  return request<Suggestion[]>(`/search/suggest?${qs}`, { signal });
}

export async function fetchWatchlist(signal?: AbortSignal): Promise<Set<string>> {
  const data = await request<{ ids: string[] }>('/api/watchlist', { signal });
  return new Set(data.ids || []);
}

export async function fetchAppWatchlist(signal?: AbortSignal): Promise<WatchlistPageResponse> {
  return request<WatchlistPageResponse>('/api/app/watchlist', { signal });
}

export async function fetchStats(signal?: AbortSignal): Promise<StatsResponse> {
  return request<StatsResponse>('/api/app/stats', { signal });
}

export async function addWatchlist(itemId: string): Promise<void> {
  await request(`/api/watchlist/${encodeURIComponent(itemId)}`, { method: 'POST' });
}

export async function removeWatchlist(itemId: string): Promise<void> {
  await request(`/api/watchlist/${encodeURIComponent(itemId)}`, { method: 'DELETE' });
}

export async function fetchContinueItems(keys: string[], signal?: AbortSignal): Promise<ContinueItem[]> {
  if (!keys.length) return [];
  const qs = new URLSearchParams({ keys: keys.join(',') });
  return request<ContinueItem[]>(`/api/items?${qs}`, { signal });
}

export async function fetchContinueMap(signal?: AbortSignal): Promise<ContinueMap> {
  return request<ContinueMap>('/api/cw', { signal });
}

export async function saveContinueEntry(key: string, entry: Omit<ContinueEntry, 'key'>): Promise<void> {
  await request<{ ok: boolean }>(`/api/cw/${encodeURIComponent(key)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(entry),
    keepalive: true,
  });
}

export async function deleteContinueEntry(key: string): Promise<void> {
  await request<{ ok: boolean }>(`/api/cw/${encodeURIComponent(key)}`, {
    method: 'DELETE',
    keepalive: true,
  });
}

export async function recordWatchHistory(key: string, title: string): Promise<void> {
  await request<{ ok: boolean }>(`/api/wh/${encodeURIComponent(key)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
    keepalive: true,
  });
}

export async function fetchRating(messageId: string | number, signal?: AbortSignal): Promise<RatingResponse> {
  return request<RatingResponse>(`/api/rate/${encodeURIComponent(String(messageId))}`, { signal });
}

export async function setRating(messageId: string | number, rating: 'up' | 'down'): Promise<RatingResponse> {
  return request<RatingResponse>(`/api/rate/${encodeURIComponent(String(messageId))}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rating }),
  });
}

export async function dismissRecommendation(tmdbId: number, kind: 'movie' | 'tv'): Promise<void> {
  await request<{ ok: boolean }>('/api/dismiss', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tmdb_id: tmdbId, kind }),
  });
}

export async function signInTelegram(user: TelegramAuthUser): Promise<{ ok: boolean; token?: string }> {
  return request<{ ok: boolean; token?: string }>('/auth/telegram', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(user),
  });
}

export async function signOut(): Promise<void> {
  await request('/auth/logout', { method: 'POST' });
}
