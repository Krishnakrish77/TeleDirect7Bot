import { useCallback, useEffect, useRef, useState } from 'react';
import { addWatchlist, fetchAdmin, fetchAppWatchlist, fetchDetail, fetchHub, fetchLikedSongs, fetchMe, fetchPlaylistDetail, fetchPlaylists, fetchStats, fetchSuggestions, fetchWatchlist, hubParamsKey, removeWatchlist } from '../api';
import type { AppRoute } from '../navigation';
import type { AdminResponse, DetailResponse, HubParams, HubResponse, MeResponse, PlaylistDetailResponse, PlaylistsResponse, StatsResponse, Suggestion, User, WatchlistPageResponse } from '../types';

function pageFamilyKey(params: HubParams): string {
  return hubParamsKey({ ...params, offset: 0 });
}

export function useHub(params: HubParams, enabled = true) {
  const [data, setData] = useState<HubResponse | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState('');
  const requestKey = hubParamsKey(params);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      setError('');
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchHub(params, controller.signal)
      .then((response) => {
        setData((current) => {
          if (
            params.offset > 0 &&
            current?.mode === 'grid' &&
            response.mode === 'grid' &&
            pageFamilyKey(current.params) === pageFamilyKey(response.params)
          ) {
            const seen = new Set(current.items.map((item) => item.itemId));
            const items = current.items.slice();
            for (const item of response.items) {
              if (!seen.has(item.itemId)) {
                seen.add(item.itemId);
                items.push(item);
              }
            }
            return { ...response, items };
          }
          return response;
        });
      })
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load the library');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, requestKey]);

  return { data, loading, error };
}

export function useDetail(route: AppRoute, locationSearch: string) {
  const [data, setData] = useState<DetailResponse | null>(null);
  const [loading, setLoading] = useState(route.kind === 'detail');
  const [error, setError] = useState('');
  const detailKind = route.kind === 'detail' ? route.detailKind : '';
  const detailKey = route.kind === 'detail' ? route.key : '';

  useEffect(() => {
    if (!detailKind || !detailKey) {
      setLoading(false);
      setError('');
      setData(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchDetail(detailKind as 'movie' | 'series' | 'album' | 'artist' | 'person', detailKey, locationSearch, controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load this page');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [detailKind, detailKey, locationSearch]);

  return { data, loading, error };
}

export function useMe() {
  const [me, setMe] = useState<MeResponse | null>(null);

  const reload = useCallback(() => {
    const controller = new AbortController();
    fetchMe(controller.signal).then(setMe).catch(() => setMe(null));
    return () => controller.abort();
  }, []);

  useEffect(() => reload(), [reload]);

  return { me, reload };
}

export function useWatchlist(user: User | null | undefined) {
  const [saved, setSaved] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!user) {
      setSaved(new Set());
      return;
    }
    const controller = new AbortController();
    fetchWatchlist(controller.signal)
      .then(setSaved)
      .catch(() => setSaved(new Set()));
    return () => controller.abort();
  }, [user]);

  const toggle = useCallback(async (itemId: string) => {
    const wasSaved = saved.has(itemId);
    const next = new Set(saved);
    if (wasSaved) next.delete(itemId);
    else next.add(itemId);
    setSaved(next);
    try {
      if (wasSaved) await removeWatchlist(itemId);
      else await addWatchlist(itemId);
    } catch (_) {
      setSaved(saved);
    }
  }, [saved]);

  const remove = useCallback(async (itemId: string) => {
    const next = new Set(saved);
    next.delete(itemId);
    setSaved(next);
    try {
      await removeWatchlist(itemId);
    } catch (_) {
      setSaved(saved);
    }
  }, [saved]);

  return { saved, toggle, remove };
}

export function useWatchlistItems(user: User | null | undefined, enabled = true) {
  const [data, setData] = useState<WatchlistPageResponse | null>(null);
  const [loading, setLoading] = useState(Boolean(user && enabled));
  const [error, setError] = useState('');

  useEffect(() => {
    if (!enabled || !user) {
      setData(null);
      setLoading(false);
      setError('');
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchAppWatchlist(controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load your watchlist');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, user]);

  const removeItem = useCallback((itemId: string) => {
    setData((current) => current
      ? { ...current, items: current.items.filter((item) => item.item_id !== itemId) }
      : current);
  }, []);

  return { data, loading, error, removeItem };
}

export function useLikedSongs(user: User | null | undefined, enabled = true) {
  const [data, setData] = useState<WatchlistPageResponse | null>(null);
  const [loading, setLoading] = useState(Boolean(user && enabled));
  const [error, setError] = useState('');

  useEffect(() => {
    if (!enabled || !user) {
      setData(null);
      setLoading(false);
      setError('');
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchLikedSongs(controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load liked songs');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, user]);

  const removeItem = useCallback((itemId: string) => {
    setData((current) => current
      ? { ...current, items: current.items.filter((item) => item.item_id !== itemId) }
      : current);
  }, []);

  return { data, loading, error, removeItem };
}

export function usePlaylists(user: User | null | undefined, enabled = true) {
  const [data, setData] = useState<PlaylistsResponse | null>(null);
  const [loading, setLoading] = useState(Boolean(user && enabled));
  const [error, setError] = useState('');
  const controllerRef = useRef<AbortController | null>(null);

  const reload = useCallback(() => {
    controllerRef.current?.abort();
    if (!enabled || !user) {
      controllerRef.current = null;
      setData(null);
      setLoading(false);
      setError('');
      return undefined;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setError('');
    fetchPlaylists(controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load playlists');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, user]);

  useEffect(() => reload(), [reload]);

  return { data, loading, error, reload, setData };
}

export function usePlaylistDetail(user: User | null | undefined, playlistId: string, enabled = true) {
  const [data, setData] = useState<PlaylistDetailResponse | null>(null);
  const [loading, setLoading] = useState(Boolean(user && playlistId && enabled));
  const [error, setError] = useState('');

  const reload = useCallback(() => {
    if (!enabled || !user || !playlistId) {
      setData(null);
      setLoading(false);
      setError('');
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchPlaylistDetail(playlistId, controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load playlist');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, playlistId, user]);

  useEffect(() => reload(), [reload]);

  return { data, loading, error, reload, setData };
}

export function useStats(user: User | null | undefined, enabled = true) {
  const [data, setData] = useState<StatsResponse | null>(null);
  const [loading, setLoading] = useState(Boolean(user && enabled));
  const [error, setError] = useState('');

  useEffect(() => {
    if (!enabled || !user) {
      setData(null);
      setLoading(false);
      setError('');
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchStats(controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load your stats');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, user]);

  return { data, loading, error };
}

export function useAdmin(user: User | null | undefined, enabled = true, search = '') {
  const [data, setData] = useState<AdminResponse | null>(null);
  const [loading, setLoading] = useState(Boolean(user?.is_admin && enabled));
  const [error, setError] = useState('');

  const reload = useCallback(() => {
    if (!enabled || !user?.is_admin) {
      setData(null);
      setLoading(false);
      setError('');
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchAdmin(search, controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load admin data');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, search, user?.is_admin]);

  useEffect(() => reload(), [reload]);

  const updateData = useCallback((updater: (current: AdminResponse | null) => AdminResponse | null) => {
    setData(updater);
  }, []);

  return { data, loading, error, reload, updateData };
}

export function useSuggestions(q: string) {
  const [items, setItems] = useState<Suggestion[]>([]);

  useEffect(() => {
    if (!q.trim()) {
      setItems([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetchSuggestions(q, controller.signal).then(setItems).catch(() => setItems([]));
    }, 160);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [q]);

  return items;
}
