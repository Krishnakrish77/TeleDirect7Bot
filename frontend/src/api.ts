import type {
  AdminActionResponse,
  AdminDashboardResponse,
  AdminItemEditPayload,
  AdminResponse,
  AdminMergeSeriesResponse,
  AdminSeriesOption,
  AdminStatusResponse,
  AiRecResponse,
  AiSuggestResponse,
  ContinueItem,
  DetailResponse,
  HubParams,
  HubResponse,
  IptvChannelPayload,
  LiveTvResponse,
  AdminIptvResponse,
  AdminIptvActionResponse,
  MeResponse,
  PlaylistDetailResponse,
  PlaylistsResponse,
  StatsResponse,
  AudioTrackOption,
  ContinueEntry,
  ContinueMap,
  SubtitleTrack,
  SubtitleSearchResult,
  RatingResponse,
  Suggestion,
  TelegramAuthUser,
  TmdbPreviewResult,
  WatchResponse,
  WatchTrack,
  WatchlistPageResponse,
} from './types';
import { getDeviceId, getDeviceLabel } from './utils/device';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function isHtmlResponse(contentType: string, text: string): boolean {
  const normalizedType = contentType.toLowerCase();
  const body = text.trim().toLowerCase();
  return normalizedType.includes('text/html') || body.startsWith('<!doctype') || body.startsWith('<html');
}

function nonJsonErrorMessage(response: Response, text: string, contentType: string): string {
  const status = response.status || 0;
  const statusLabel = status ? ` (${status})` : '';
  const fallback = response.statusText || `Request failed${statusLabel}`;

  if (isHtmlResponse(contentType, text)) {
    if (status === 401 || status === 403) return 'Admin access required. Sign in again and retry.';
    if (status === 0 || response.ok) return 'Server returned an HTML page instead of JSON. Sign in again and retry.';
    if ([502, 503, 504].includes(status)) return `Server returned an HTML error page${statusLabel}. Try again shortly.`;
    return `Request failed${statusLabel}.`;
  }

  const clean = text.trim();
  return clean ? clean.slice(0, 500) : fallback;
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
    let message = response.statusText || 'Request failed';
    const contentType = (response.headers.get('content-type') || '').toLowerCase();
    try {
      if (contentType.includes('json')) {
        const data = await response.json() as { error?: string; message?: string };
        message = data.error || data.message || message;
      } else {
        const text = await response.text();
        message = nonJsonErrorMessage(response, text, contentType);
      }
    } catch (_) {
      // Keep the status text when the error body is unreadable.
    }
    throw new ApiError(message, response.status);
  }
  const contentType = (response.headers.get('content-type') || '').toLowerCase();
  if (!contentType.includes('json')) {
    const text = await response.text();
    throw new ApiError(nonJsonErrorMessage(response, text, contentType), response.status);
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

export async function fetchAiRecommendations(refresh = false, signal?: AbortSignal): Promise<AiRecResponse> {
  return request<AiRecResponse>(`/api/app/ai/recommendations${refresh ? '?refresh=1' : ''}`, { signal });
}

export async function askAiRecommendations(query: string, signal?: AbortSignal): Promise<AiRecResponse> {
  return request<AiRecResponse>('/api/app/ai/recommendations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
    signal,
  });
}

export type RecommendationFeedbackEvent = {
  action: 'impression' | 'open' | 'play' | 'save' | 'unsave' | 'dismiss';
  source: 'home' | 'ai';
  itemId: string;
  tmdbId?: number | null;
  tmdbKind?: 'movie' | 'tv' | '';
  shelf?: string;
  position?: number;
};

// Best-effort by design: feedback must never delay navigation or make a card
// interaction feel broken. sendBeacon survives a click-to-navigate; fetch is
// the fallback for browsers that do not support it.
export function trackRecommendationEvents(events: RecommendationFeedbackEvent[]): void {
  if (!events.length) return;
  const body = JSON.stringify({ events: events.slice(0, 40) });
  try {
    if (navigator.sendBeacon?.('/api/app/recommendations/events', new Blob([body], { type: 'application/json' }))) return;
  } catch (_) {
    // Fall through to fetch.
  }
  void request<{ ok: boolean }>('/api/app/recommendations/events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => undefined);
}

export async function fetchWatch(key: string, signal?: AbortSignal): Promise<WatchResponse> {
  return request<WatchResponse>(`/api/watch/${encodeURIComponent(key)}`, { signal });
}

// Radio station: related tracks seeded by a track key or an artist slug.
// `exclude` (track keys already queued) keeps refills from repeating.
export async function fetchRadio(
  seed: { track?: string; artist?: string; exclude?: string[] },
  signal?: AbortSignal,
): Promise<{ tracks: WatchTrack[] }> {
  const qs = new URLSearchParams();
  if (seed.track) qs.set('seed', seed.track);
  if (seed.artist) qs.set('artist', seed.artist);
  if (seed.exclude?.length) qs.set('exclude', seed.exclude.join(','));
  return request<{ tracks: WatchTrack[] }>(`/api/app/radio?${qs.toString()}`, { signal });
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

export async function searchUserSubtitles(key: string, language = ''): Promise<{ results: SubtitleSearchResult[] }> {
  const suffix = language ? `?language=${encodeURIComponent(language)}` : '';
  return request<{ results: SubtitleSearchResult[] }>(`/api/app/subtitles/${encodeURIComponent(key)}/search${suffix}`);
}

export async function attachUserSubtitle(key: string, id: string): Promise<{ ok: boolean; vtt: string; label: string; language: string }> {
  return request<{ ok: boolean; vtt: string; label: string; language: string }>(`/api/app/subtitles/${encodeURIComponent(key)}/attach`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }),
  });
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

export async function fetchLikedSongs(signal?: AbortSignal): Promise<WatchlistPageResponse> {
  return request<WatchlistPageResponse>('/api/app/liked-songs', { signal });
}

export async function fetchPlaylists(signal?: AbortSignal): Promise<PlaylistsResponse> {
  return request<PlaylistsResponse>('/api/app/playlists', { signal });
}

export async function fetchPlaylistDetail(playlistId: string, signal?: AbortSignal): Promise<PlaylistDetailResponse> {
  return request<PlaylistDetailResponse>(`/api/app/playlists/${encodeURIComponent(playlistId)}`, { signal });
}

export async function createPlaylist(name: string): Promise<PlaylistDetailResponse> {
  return request<PlaylistDetailResponse>('/api/app/playlists', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
}

export async function renamePlaylist(playlistId: string, name: string): Promise<PlaylistDetailResponse> {
  return request<PlaylistDetailResponse>(`/api/app/playlists/${encodeURIComponent(playlistId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
}

export async function deletePlaylist(playlistId: string): Promise<void> {
  await request<{ ok: boolean }>(`/api/app/playlists/${encodeURIComponent(playlistId)}`, { method: 'DELETE' });
}

export async function addTrackToPlaylist(playlistId: string, track: WatchTrack): Promise<PlaylistDetailResponse> {
  return request<PlaylistDetailResponse>(`/api/app/playlists/${encodeURIComponent(playlistId)}/tracks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messageId: track.messageId, secureHash: track.secureHash }),
  });
}

export async function removeTrackFromPlaylist(playlistId: string, messageId: number): Promise<PlaylistDetailResponse> {
  return request<PlaylistDetailResponse>(
    `/api/app/playlists/${encodeURIComponent(playlistId)}/tracks/${encodeURIComponent(String(messageId))}`,
    { method: 'DELETE' },
  );
}

export async function reorderPlaylistTracks(playlistId: string, messageIds: number[]): Promise<PlaylistDetailResponse> {
  return request<PlaylistDetailResponse>(`/api/app/playlists/${encodeURIComponent(playlistId)}/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messageIds }),
  });
}

export async function fetchStats(signal?: AbortSignal): Promise<StatsResponse> {
  return request<StatsResponse>('/api/app/stats', { signal });
}

export async function fetchLiveTvChannels(signal?: AbortSignal): Promise<LiveTvResponse> {
  return request<LiveTvResponse>('/api/live-tv/channels', { signal });
}

export async function fetchAdmin(search = '', signal?: AbortSignal): Promise<AdminResponse> {
  return request<AdminResponse>(`/api/app/admin${search}`, { signal });
}

export async function fetchAdminIptv(signal?: AbortSignal): Promise<AdminIptvResponse> {
  return request<AdminIptvResponse>('/api/app/admin/iptv', { signal });
}

export async function saveAdminIptvChannel(payload: IptvChannelPayload): Promise<AdminIptvActionResponse> {
  const id = payload.id?.trim();
  return request<AdminIptvActionResponse>(id
    ? `/api/app/admin/iptv/channel/${encodeURIComponent(id)}`
    : '/api/app/admin/iptv/channel', {
    method: id ? 'PATCH' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminIptvChannel(id: string): Promise<AdminIptvActionResponse> {
  return request<AdminIptvActionResponse>(`/api/app/admin/iptv/channel/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function importAdminIptvM3u(m3u: string): Promise<AdminIptvActionResponse> {
  return request<AdminIptvActionResponse>('/api/app/admin/iptv/import-m3u', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ m3u }),
  });
}

export async function importAdminIptvM3uUrl(url: string): Promise<AdminIptvActionResponse> {
  return request<AdminIptvActionResponse>('/api/app/admin/iptv/import-m3u-url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
}

export async function testAdminIptvStream(streamUrl: string, streamHeaders: Record<string, string> = {}): Promise<{ ok: boolean; message: string }> {
  return request('/api/app/admin/iptv/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ streamUrl, streamHeaders }),
  });
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

export async function fetchAdminSeriesList(signal?: AbortSignal): Promise<AdminSeriesOption[]> {
  return request<AdminSeriesOption[]>('/admin/series-list', { signal });
}

export async function mergeAdminSeries(sourceKey: string, targetKey: string): Promise<AdminMergeSeriesResponse> {
  return request<AdminMergeSeriesResponse>('/admin/merge-series', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_key: sourceKey, target_key: targetKey }),
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

// Server-side progress sync is only meaningful for signed-in users.
// Anonymous viewers keep their resume state locally (td:cw); firing these
// writes for them just 401s on every 30s tick and on unload. App sets this
// once /api/me resolves.
let _serverSyncEnabled = false;
export function setServerSyncEnabled(on: boolean): void {
  _serverSyncEnabled = on;
}

// Returns whether the server accepted the write. `false` means it was rejected
// as stale or tombstoned (deleted/completed elsewhere) — callers use this to
// drop a local entry. Defaults to true on network error / anon (don't delete
// on a transient failure).
export async function saveContinueEntry(key: string, entry: Omit<ContinueEntry, 'key'>): Promise<boolean> {
  if (!_serverSyncEnabled) return true;
  const res = await request<{ ok: boolean; accepted?: boolean }>(`/api/cw/${encodeURIComponent(key)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // Stamp the writing device so other devices can show "watched on <label>".
    body: JSON.stringify({ ...entry, deviceId: getDeviceId(), deviceLabel: getDeviceLabel() }),
    keepalive: true,
  });
  return res.accepted !== false;
}

export async function deleteContinueEntry(key: string): Promise<void> {
  if (!_serverSyncEnabled) return;
  await request<{ ok: boolean }>(`/api/cw/${encodeURIComponent(key)}`, {
    method: 'DELETE',
    keepalive: true,
  });
}

export async function clearAllContinue(): Promise<void> {
  if (!_serverSyncEnabled) return;
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

export async function uploadAdminSubtitle(id: number, file: File): Promise<{ ok: boolean; message: string; item: unknown }> {
  const form = new FormData();
  form.append('file', file, file.name);
  return request(`/api/app/admin/item/${id}/subtitles`, { method: 'POST', body: form });
}

export async function deleteAdminSubtitle(id: number, binMessageId: number): Promise<{ ok: boolean; message: string; item: unknown }> {
  return request(`/api/app/admin/item/${id}/subtitles/${binMessageId}`, { method: 'DELETE' });
}

export async function fetchAiModels(signal?: AbortSignal): Promise<Array<{ id: string; name: string }>> {
  return request<Array<{ id: string; name: string }>>('/api/app/admin/ai-models', { signal });
}

export async function aiSuggestItem(id: number, model: string, fields?: string): Promise<AiSuggestResponse> {
  const qs = new URLSearchParams({ model });
  if (fields) qs.set('fields', fields);
  return request<AiSuggestResponse>(`/api/app/admin/item/${id}/ai-suggest?${qs}`, { method: 'POST' });
}

export async function fetchTmdbPreview(tmdbId: number, kind: string, signal?: AbortSignal): Promise<TmdbPreviewResult> {
  const qs = new URLSearchParams({ id: String(tmdbId), kind });
  return request<TmdbPreviewResult>(`/api/app/admin/tmdb-preview?${qs}`, { signal });
}

export async function resolveTmdbImdb(imdbInput: string, signal?: AbortSignal): Promise<{ tmdb_id: number; kind: string; imdb_id: string; error?: string }> {
  const qs = new URLSearchParams({ imdb_id: imdbInput });
  return request(`/api/app/admin/tmdb-resolve-imdb?${qs}`, { signal });
}

export async function recordWatchHistory(key: string, title: string): Promise<void> {
  if (!_serverSyncEnabled) return;
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
