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
    .filter((line) => Number.isFinite(line.t))
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

async function fetchLyrics(track: WatchTrack): Promise<LyricsResult> {
  const key = lyricsKey(track);
  const cached = lyricsCache.get(key);
  if (cached) return cached;
  const pending = lyricsRequests.get(key);
  if (pending) return pending;

  const request = (async () => {
    const params = new URLSearchParams({
      track_name: track.title,
      artist_name: track.artist || '',
    });
    if (track.albumTitle) params.set('album_name', track.albumTitle);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), LYRIC_FETCH_TIMEOUT_MS);
    try {
      const response = await fetch(`https://lrclib.net/api/get?${params}`, {
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
      if (!response.ok) {
        lyricsCache.set(key, EMPTY_LYRICS);
        return EMPTY_LYRICS;
      }

      const data = await response.json() as { syncedLyrics?: string; plainLyrics?: string };
      const synced = data.syncedLyrics ? parseLrc(data.syncedLyrics) : [];
      const result = synced.length
        ? { synced, plain: '', unavailable: false }
        : data.plainLyrics
          ? { synced: [], plain: data.plainLyrics, unavailable: false }
          : EMPTY_LYRICS;
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
