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
