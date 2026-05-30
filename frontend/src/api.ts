import type {
  ContinueItem,
  HubParams,
  HubResponse,
  MeResponse,
  Suggestion,
  TelegramAuthUser,
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

export async function fetchSuggestions(q: string, signal?: AbortSignal): Promise<Suggestion[]> {
  if (!q.trim()) return [];
  const qs = new URLSearchParams({ q, limit: '8' });
  return request<Suggestion[]>(`/search/suggest?${qs}`, { signal });
}

export async function fetchWatchlist(signal?: AbortSignal): Promise<Set<string>> {
  const data = await request<{ ids: string[] }>('/api/watchlist', { signal });
  return new Set(data.ids || []);
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
