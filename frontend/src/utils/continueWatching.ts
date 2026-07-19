import { fetchContinueMap, saveContinueEntry } from '../api';
import type { ContinueEntry } from '../types';

export const CONTINUE_STORAGE_KEY = 'td:cw';
const CONTINUE_TOMBSTONES_KEY = 'td:cw:tombstones';
const CONTINUE_CLEAR_KEY = 'td:cw:clearedAt';
const CONTINUE_TOMBSTONE_TTL_MS = 30 * 24 * 60 * 60 * 1000;

export type LocalContinueMap = Record<string, Omit<ContinueEntry, 'key'>>;

export function readLocalContinue(): LocalContinueMap {
  try {
    const parsed = JSON.parse(localStorage.getItem(CONTINUE_STORAGE_KEY) || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch (_) {
    return {};
  }
}

export function writeLocalContinue(map: LocalContinueMap) {
  try {
    if (Object.keys(map).length) localStorage.setItem(CONTINUE_STORAGE_KEY, JSON.stringify(map));
    else localStorage.removeItem(CONTINUE_STORAGE_KEY);
  } catch (_) {
    // Private mode / quota — local resume is best-effort.
  }
}

export function upsertLocalContinue(key: string, entry: Omit<ContinueEntry, 'key'>) {
  const map = readLocalContinue();
  map[key] = entry;
  writeLocalContinue(map);
}

export function readContinueTombstones(): Record<string, number> {
  try {
    const parsed = JSON.parse(localStorage.getItem(CONTINUE_TOMBSTONES_KEY) || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch (_) {
    return {};
  }
}

export function writeContinueTombstones(tombstones: Record<string, number>) {
  const cutoff = Date.now() - CONTINUE_TOMBSTONE_TTL_MS;
  const entries = Object.entries(tombstones).filter(([, value]) => Number.isFinite(value) && value > cutoff);
  if (entries.length) localStorage.setItem(CONTINUE_TOMBSTONES_KEY, JSON.stringify(Object.fromEntries(entries)));
  else localStorage.removeItem(CONTINUE_TOMBSTONES_KEY);
}

export function addContinueTombstone(key: string, at = Date.now()) {
  try {
    const tombstones = readContinueTombstones();
    tombstones[key] = Math.max(tombstones[key] || 0, at);
    writeContinueTombstones(tombstones);
  } catch (_) {
    // Tombstones only protect best-effort local resume state.
  }
}

export function readContinueClearTombstone() {
  const value = Number(localStorage.getItem(CONTINUE_CLEAR_KEY) || 0);
  return Number.isFinite(value) ? value : 0;
}

export function addContinueClearTombstone(at = Date.now()) {
  try {
    localStorage.setItem(CONTINUE_CLEAR_KEY, String(Math.max(readContinueClearTombstone(), at)));
  } catch (_) {
    // Tombstones only protect best-effort local resume state.
  }
}

export function isContinueSuppressed(
  key: string,
  value: Omit<ContinueEntry, 'key'> | undefined | null,
  tombstones = readContinueTombstones(),
  clearAt = readContinueClearTombstone(),
) {
  const updatedAt = value?.t || 0;
  return Boolean((clearAt && updatedAt <= clearAt) || (tombstones[key] && updatedAt <= tombstones[key]));
}

let _inFlight: Promise<LocalContinueMap> | null = null;

/**
 * Two-way eventual sync between local `td:cw` and the server:
 *  - pull server progress into local (newer `t` wins, honouring tombstones) so
 *    every surface — shelf, video, audio — resumes cross-device from local;
 *  - push local entries newer than the server (the robust login merge). The
 *    server rejects stale writes via its monotonic guard.
 * Safe/no-op for anonymous users (server calls 401 → caught). Concurrent calls
 * (App + shelf firing on the same focus event) coalesce into one round-trip.
 */
export async function syncContinueWatching(): Promise<LocalContinueMap> {
  if (_inFlight) return _inFlight;
  _inFlight = (async () => {
    let server: LocalContinueMap = {};
    try {
      server = (await fetchContinueMap()) as unknown as LocalContinueMap;
    } catch (_) {
      return readLocalContinue(); // offline / anonymous → keep local as-is
    }
    const local = readLocalContinue();
    const tombstones = readContinueTombstones();
    const clearAt = readContinueClearTombstone();
    const merged: LocalContinueMap = { ...local };
    const toPush: Array<[string, Omit<ContinueEntry, 'key'>]> = [];
    for (const key of new Set([...Object.keys(local), ...Object.keys(server)])) {
      const s = server[key];
      const l = local[key];
      if (s && isContinueSuppressed(key, s, tombstones, clearAt)) continue;
      if (s && (!l || (s.t || 0) > (l.t || 0))) {
        merged[key] = s;
      } else if (l && (!s || (l.t || 0) > (s.t || 0)) && !isContinueSuppressed(key, l, tombstones, clearAt)) {
        toPush.push([key, l]);
      }
    }
    writeLocalContinue(merged);
    for (const [key, entry] of toPush) {
      void saveContinueEntry(key, { ...entry, startedAt: entry.t }).catch(() => undefined);
    }
    return merged;
  })();
  try {
    return await _inFlight;
  } finally {
    _inFlight = null;
  }
}
