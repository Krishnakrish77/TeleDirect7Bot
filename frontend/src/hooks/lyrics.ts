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

export function clearLyricsCache() {
  lyricsCache.clear();
  lyricsRequests.clear();
}

function lyricsKey(track: WatchTrack): string {
  return `${track.title.trim().toLowerCase()}::${(track.artist || '').trim().toLowerCase()}`;
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
    const response = await fetch(`https://lrclib.net/api/get?${params}`, {
      headers: { Accept: 'application/json' },
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
  })();

  lyricsRequests.set(key, request);
  try {
    return await request;
  } finally {
    lyricsRequests.delete(key);
  }
}

export function useLyrics(track: WatchTrack | null) {
  const cacheKey = useMemo(() => (track ? lyricsKey(track) : ''), [track]);
  const [state, setState] = useState<{ loading: boolean; lyrics: LyricsResult }>({
    loading: Boolean(track),
    lyrics: EMPTY_LYRICS,
  });

  useEffect(() => {
    if (!track) {
      setState({ loading: false, lyrics: EMPTY_LYRICS });
      return undefined;
    }
    const cached = lyricsCache.get(cacheKey);
    if (cached) {
      setState({ loading: false, lyrics: cached });
      return undefined;
    }
    let cancelled = false;
    setState({ loading: true, lyrics: EMPTY_LYRICS });
    fetchLyrics(track)
      .then((lyrics) => {
        if (!cancelled) setState({ loading: false, lyrics });
      })
      .catch((error: Error) => {
        if (!cancelled && error.name !== 'AbortError') {
          setState({ loading: false, lyrics: EMPTY_LYRICS });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [cacheKey, track]);

  return state;
}
