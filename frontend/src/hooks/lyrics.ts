import { useEffect, useMemo, useState } from 'react';
import type { WatchTrack } from '../types';

export interface LyricLine {
  t: number;
  text: string;
}

export interface LyricsResult {
  synced: LyricLine[];
  plain: string;
  unavailable: boolean;
}

interface LrclibLyrics {
  trackName?: string;
  name?: string;
  artistName?: string;
  albumName?: string;
  duration?: number;
  syncedLyrics?: string | null;
  plainLyrics?: string | null;
}

const EMPTY_LYRICS: LyricsResult = { synced: [], plain: '', unavailable: true };
const lyricsCache = new Map<string, LyricsResult>();
const lyricsRequests = new Map<string, Promise<LyricsResult>>();
const LYRIC_LEAD_SECONDS = 0.35;
const LYRIC_FETCH_TIMEOUT_MS = 6500;

export function clearLyricsCache() {
  lyricsCache.clear();
  lyricsRequests.clear();
}

function lyricsKey(track: WatchTrack): string {
  return [
    track.title.trim().toLowerCase(),
    (track.artist || '').trim().toLowerCase(),
    (track.albumTitle || '').trim().toLowerCase(),
  ].join('::');
}

export function parseLrc(raw: string): LyricLine[] {
  const lines: LyricLine[] = [];
  for (const line of raw.split(/\r?\n/)) {
    const tags = [...line.matchAll(/\[(\d{1,2}):(\d{2}(?:\.\d{1,3})?)\]/g)];
    if (!tags.length) continue;
    const text = line.replace(/\[(\d{1,2}):(\d{2}(?:\.\d{1,3})?)\]/g, '').trim();
    for (const tag of tags) {
      lines.push({
        t: Number(tag[1]) * 60 + Number(tag[2]),
        text,
      });
    }
  }
  return lines
    .filter((line) => Number.isFinite(line.t) && line.text.length > 0)
    .sort((a, b) => a.t - b.t);
}

export function lyricsActiveIndex(lines: LyricLine[], currentTime: number, leadSeconds = LYRIC_LEAD_SECONDS): number {
  if (!lines.length || !Number.isFinite(currentTime)) return -1;
  const target = currentTime + leadSeconds;
  if (target < lines[0].t) return -1;
  let low = 0;
  let high = lines.length - 1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    if (lines[mid].t <= target) low = mid + 1;
    else high = mid - 1;
  }
  return high;
}

function lyricsFromResponse(data: LrclibLyrics | null): LyricsResult | null {
  if (!data) return null;
  const synced = data.syncedLyrics ? parseLrc(data.syncedLyrics) : [];
  if (synced.length) return { synced, plain: '', unavailable: false };
  return data.plainLyrics ? { synced: [], plain: data.plainLyrics, unavailable: false } : null;
}

async function requestLrclib<T>(path: string, params: URLSearchParams, signal: AbortSignal): Promise<T | null> {
  const response = await fetch(`https://lrclib.net/api/${path}?${params}`, {
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) return null;
  return response.json() as Promise<T>;
}

function setParam(params: URLSearchParams, key: string, value: string | null | undefined): void {
  const trimmed = (value || '').trim();
  if (trimmed) params.set(key, trimmed);
}

function normalized(value: string | null | undefined): string {
  return (value || '')
    .toLowerCase()
    .replace(/\s*-\s*from\s+["“][^"”]+["”]/gi, '')
    .replace(/\s*\(from[^)]*\)/gi, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function textScore(query: string | null | undefined, candidate: string | null | undefined, exact: number, contains: number): number {
  const expected = normalized(query);
  const actual = normalized(candidate);
  if (!expected || !actual) return 0;
  if (expected === actual) return exact;
  if (actual.includes(expected) || expected.includes(actual)) return contains;
  return 0;
}

function candidateScore(track: WatchTrack, candidate: LrclibLyrics, result: LyricsResult): number {
  let score = result.synced.length ? 1000 + Math.min(result.synced.length, 80) : 100;
  score += textScore(track.title, candidate.trackName || candidate.name, 220, 90);
  score += textScore(track.artist, candidate.artistName, 140, 45);
  score += textScore(track.albumTitle, candidate.albumName, 50, 15);
  if (track.duration > 0 && candidate.duration && Number.isFinite(candidate.duration)) {
    const diff = Math.abs(track.duration - candidate.duration);
    if (diff <= 2) score += 70;
    else if (diff <= 8) score += 35;
  }
  return score;
}

async function fetchExactLyrics(track: WatchTrack, signal: AbortSignal): Promise<LyricsResult | null> {
  if (!track.artist?.trim()) return null;
  const params = new URLSearchParams({
    track_name: track.title,
    artist_name: track.artist,
  });
  setParam(params, 'album_name', track.albumTitle);
  const data = await requestLrclib<LrclibLyrics>('get', params, signal);
  return lyricsFromResponse(data);
}

async function fetchSearchLyrics(track: WatchTrack, signal: AbortSignal): Promise<LyricsResult | null> {
  const searchParams = [new URLSearchParams({ track_name: track.title })];
  setParam(searchParams[0], 'artist_name', track.artist);
  if (searchParams[0].get('artist_name')) {
    searchParams.push(new URLSearchParams({ track_name: track.title }));
  }
  const seen = new Set<number>();
  const results: LrclibLyrics[] = [];
  for (const params of searchParams) {
    const payload = await requestLrclib<LrclibLyrics[]>('search', params, signal);
    if (!Array.isArray(payload)) continue;
    for (const candidate of payload) {
      const id = Number((candidate as { id?: unknown }).id);
      if (Number.isFinite(id)) {
        if (seen.has(id)) continue;
        seen.add(id);
      }
      results.push(candidate);
    }
    if (results.some((candidate) => Boolean(candidate.syncedLyrics))) break;
  }
  if (!results.length) return null;

  let best: { score: number; result: LyricsResult } | null = null;
  for (const candidate of results) {
    const result = lyricsFromResponse(candidate);
    if (!result) continue;
    const score = candidateScore(track, candidate, result);
    if (!best || score > best.score) best = { score, result };
  }
  return best?.result || null;
}

async function fetchLyrics(track: WatchTrack): Promise<LyricsResult> {
  const key = lyricsKey(track);
  const cached = lyricsCache.get(key);
  if (cached) return cached;
  const pending = lyricsRequests.get(key);
  if (pending) return pending;

  const request = (async () => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), LYRIC_FETCH_TIMEOUT_MS);
    try {
      const exact = await fetchExactLyrics(track, controller.signal);
      if (exact?.synced.length) {
        lyricsCache.set(key, exact);
        return exact;
      }

      const searched = await fetchSearchLyrics(track, controller.signal);
      const result = searched?.synced.length ? searched : exact || searched || EMPTY_LYRICS;
      lyricsCache.set(key, result);
      return result;
    } catch (_) {
      lyricsCache.set(key, EMPTY_LYRICS);
      return EMPTY_LYRICS;
    } finally {
      window.clearTimeout(timeout);
    }
  })();

  lyricsRequests.set(key, request);
  try {
    return await request;
  } finally {
    lyricsRequests.delete(key);
  }
}

export function preloadLyrics(track: WatchTrack | null): Promise<LyricsResult> {
  if (!track) return Promise.resolve(EMPTY_LYRICS);
  return fetchLyrics(track).catch(() => EMPTY_LYRICS);
}

export function useLyrics(track: WatchTrack | null) {
  const cacheKey = useMemo(() => (track ? lyricsKey(track) : ''), [track]);
  const cachedLyrics = cacheKey ? lyricsCache.get(cacheKey) : undefined;
  const [state, setState] = useState<{ cacheKey: string; loading: boolean; lyrics: LyricsResult }>(() => ({
    cacheKey,
    loading: Boolean(track && !cachedLyrics),
    lyrics: cachedLyrics || EMPTY_LYRICS,
  }));
  const visibleState = state.cacheKey === cacheKey
    ? state
    : {
      cacheKey,
      loading: Boolean(track && !cachedLyrics),
      lyrics: cachedLyrics || EMPTY_LYRICS,
    };

  useEffect(() => {
    if (!track) {
      setState({ cacheKey, loading: false, lyrics: EMPTY_LYRICS });
      return undefined;
    }
    const cached = lyricsCache.get(cacheKey);
    if (cached) {
      setState({ cacheKey, loading: false, lyrics: cached });
      return undefined;
    }
    let cancelled = false;
    setState({ cacheKey, loading: true, lyrics: EMPTY_LYRICS });
    fetchLyrics(track)
      .then((lyrics) => {
        if (!cancelled) setState({ cacheKey, loading: false, lyrics });
      })
      .catch((error: Error) => {
        if (!cancelled && error.name !== 'AbortError') {
          setState({ cacheKey, loading: false, lyrics: EMPTY_LYRICS });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [cacheKey, track]);

  return visibleState;
}
