import type {
  AdminActionResponse,
  AdminDashboardResponse,
  AdminItemEditPayload,
  AdminResponse,
  AdminStatusResponse,
  AiSuggestResponse,
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
  TmdbPreviewResult,
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

export async function fetchAdmin(search = '', signal?: AbortSignal): Promise<AdminResponse> {
  return request<AdminResponse>(`/api/app/admin${search}`, { signal });
}

export async function fetchAdminStatus(signal?: AbortSignal): Promise<AdminStatusResponse> {
  return request<AdminStatusResponse>('/api/app/admin/status', { signal });
}

export async function runAdminAction(payload: Record<string, unknown>): Promise<AdminActionResponse> {
  return request<AdminActionResponse>('/api/app/admin/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function runAdminMaintenance(action: string, payload: Record<string, unknown> = {}): Promise<AdminActionResponse> {
  return request<AdminActionResponse>('/api/app/admin/maintenance', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, ...payload }),
  });
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

export async function clearAllContinue(): Promise<void> {
  await request<{ ok: boolean }>('/api/cw', { method: 'DELETE', keepalive: true });
}

export async function fetchAdminDashboard(signal?: AbortSignal): Promise<AdminDashboardResponse> {
  return request<AdminDashboardResponse>('/api/app/admin/dashboard', { signal });
}

export async function fetchAdminTrendingGaps(signal?: AbortSignal): Promise<{ gaps: Array<{ title: string; year: string; kind: string; poster: string; vote: string; tmdb_url: string }> }> {
  return request('/api/app/admin/trending-gaps', { signal });
}

export async function refreshAdminTrendingGaps(): Promise<void> {
  await request('/api/app/admin/trending-gaps/refresh', { method: 'POST' });
}

export async function fetchAdminItem(id: number, signal?: AbortSignal): Promise<Record<string, unknown>> {
  return request(`/api/app/admin/item/${id}`, { signal });
}

export async function clearAdminItemTmdb(id: number): Promise<{ ok: boolean; item: unknown }> {
  return request(`/api/app/admin/item/${id}/clear-tmdb`, { method: 'POST' });
}

export async function saveAdminItem(id: number, payload: AdminItemEditPayload): Promise<{ ok: boolean; status: string; item: unknown }> {
  return request(`/api/app/admin/item/${id}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function fetchAiModels(signal?: AbortSignal): Promise<Array<{ id: string; name: string }>> {
  return request<Array<{ id: string; name: string }>>('/admin/ai-models', { signal });
}

export async function aiSuggestItem(id: number, model: string, fields?: string): Promise<AiSuggestResponse> {
  const qs = new URLSearchParams({ model });
  if (fields) qs.set('fields', fields);
  return request<AiSuggestResponse>(`/admin/ai-suggest/${id}?${qs}`, { method: 'POST' });
}

export async function fetchTmdbPreview(tmdbId: number, kind: string, signal?: AbortSignal): Promise<TmdbPreviewResult> {
  const qs = new URLSearchParams({ id: String(tmdbId), kind });
  return request<TmdbPreviewResult>(`/admin/tmdb-preview?${qs}`, { signal });
}

export async function resolveTmdbImdb(imdbInput: string, signal?: AbortSignal): Promise<{ tmdb_id: number; kind: string; imdb_id: string; error?: string }> {
  const qs = new URLSearchParams({ imdb_id: imdbInput });
  return request(`/admin/tmdb-resolve-imdb?${qs}`, { signal });
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
