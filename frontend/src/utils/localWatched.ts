const WATCHED_KEY = 'td:watched:v1';
const MAX_WATCHED_KEYS = 500;

let watchedCache: Record<string, number> | null = null;

function readWatchedMap(): Record<string, number> {
  if (watchedCache !== null) return watchedCache;
  let parsed: Record<string, number> = {};
  try {
    const raw = JSON.parse(localStorage.getItem(WATCHED_KEY) || '{}') || {};
    if (raw && typeof raw === 'object') {
      const entries = Object.entries(raw).filter(
        (entry): entry is [string, number] => Boolean(entry[0]) && typeof entry[1] === 'number',
      );
      parsed = Object.fromEntries(entries);
    }
  } catch {
    // Local watched state is best-effort only.
  }
  watchedCache = parsed;
  queueMicrotask(() => {
    watchedCache = null;
  });
  return parsed;
}

export function isLocallyWatched(key?: string): boolean {
  if (!key) return false;
  return Boolean(readWatchedMap()[key]);
}

export function markLocallyWatched(key?: string): void {
  if (!key) return;
  try {
    const data = { ...readWatchedMap(), [key]: Date.now() };
    const entries: Array<[string, number]> = Object.entries(data)
      .sort(([, a], [, b]) => Number(b) - Number(a))
      .slice(0, MAX_WATCHED_KEYS);
    localStorage.setItem(WATCHED_KEY, JSON.stringify(Object.fromEntries(entries)));
    watchedCache = Object.fromEntries(entries);
    queueMicrotask(() => {
      watchedCache = null;
    });
  } catch {
    // Ignore quota/private-mode failures.
  }
}
