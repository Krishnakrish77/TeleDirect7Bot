import { useCallback, useEffect, useState } from 'react';
import { addWatchlist, fetchDetail, fetchHub, fetchMe, fetchSuggestions, fetchWatchlist, hubParamsKey, removeWatchlist } from '../api';
import type { AppRoute } from '../navigation';
import type { DetailResponse, HubParams, HubResponse, MeResponse, Suggestion, User } from '../types';

export function useHub(params: HubParams, enabled = true) {
  const [data, setData] = useState<HubResponse | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState('');
  const requestKey = hubParamsKey(params);
  const stale = Boolean(enabled && data && hubParamsKey(data.params) !== requestKey);

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
      .then(setData)
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'Unable to load the library');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, requestKey]);

  return { data, loading, error, stale };
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

  return { saved, toggle };
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
