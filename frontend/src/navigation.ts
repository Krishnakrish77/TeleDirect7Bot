import { MouseEvent, useCallback, useEffect, useRef, useState } from 'react';
import { hubParamsKey, hubSearchParams } from './api';
import type { HubParams, ViewValue } from './types';

const DEFAULT_PARAMS: HubParams = {
  q: '',
  tag: '',
  quality: '',
  genre: '',
  year: null,
  sort: 'newest',
  view: '',
  offset: 0,
  limit: 24,
};

function parseParams(): HubParams {
  const qs = new URLSearchParams(window.location.search);
  const yearRaw = qs.get('year');
  const offsetRaw = qs.get('offset');
  const limitRaw = qs.get('limit');
  const view = (qs.get('view') || '') as ViewValue;
  return {
    ...DEFAULT_PARAMS,
    q: qs.get('q') || '',
    tag: qs.get('tag') || '',
    quality: qs.get('quality') || '',
    genre: qs.get('genre') || '',
    year: yearRaw ? Number(yearRaw) || null : null,
    sort: qs.get('sort') || 'newest',
    view: ['', 'list', 'movies', 'series', 'music'].includes(view) ? view : '',
    offset: offsetRaw ? Math.max(0, Number(offsetRaw) || 0) : 0,
    limit: limitRaw ? Math.max(12, Math.min(60, Number(limitRaw) || 24)) : 24,
  };
}

export function appUrl(params: Partial<HubParams>, path = ''): string {
  const qs = hubSearchParams(params);
  const target = path || '/';
  return qs.toString() ? `${target}?${qs}` : target;
}

function sameParams(left: HubParams, right: HubParams): boolean {
  return hubParamsKey(left) === hubParamsKey(right);
}

export function localAppHref(href: string | null): string | null {
  if (!href) return null;
  if (href === '/app') return '/';
  if (href.startsWith('/app?')) return `/${href.slice('/app?'.length) ? `?${href.slice('/app?'.length)}` : ''}`;
  if (href.startsWith('/app/watch/')) return `/play/${href.slice('/app/watch/'.length)}`;
  if (href.startsWith('/app/')) return `/${href.slice('/app/'.length)}`;
  return href;
}

interface AppLocation {
  pathname: string;
  search: string;
  hash: string;
  key: string;
}

function readLocation(): AppLocation {
  const { pathname, search, hash } = window.location;
  return { pathname, search, hash, key: `${pathname}${search}${hash}` };
}

function normalizeAppHref(href: string): string {
  const url = new URL(href, window.location.origin);
  const path = localAppHref(url.pathname) || url.pathname;
  return `${path}${url.search}${url.hash}`;
}

function isReactAppPath(pathname: string): boolean {
  return pathname === '/' || pathname === '/app' || pathname.startsWith('/app/') ||
    /^(?:\/(?:filters|books|watchlist|requests|liked-songs|playlists|playlist|live-tv|stats|admin|play|movie|series|album|artist|person))(?:\/|$)/.test(pathname);
}

export function useAppNavigation() {
  const [location, setLocation] = useState<AppLocation>(() => readLocation());

  useEffect(() => {
    const onPop = () => setLocation(readLocation());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((href: string, replace = false) => {
    const next = normalizeAppHref(href);
    if (replace) {
      window.history.replaceState(null, '', next);
    } else if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.pushState(null, '', next);
    }
    setLocation(readLocation());
    if (!replace) window.scrollTo({ top: 0, behavior: 'auto' });
  }, []);

  const onLinkClick = useCallback((event: MouseEvent<HTMLDivElement>) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.altKey || event.ctrlKey || event.shiftKey) {
      return;
    }
    const target = event.target as Element | null;
    const anchor = target?.closest<HTMLAnchorElement>('a');
    if (!anchor || anchor.target || anchor.hasAttribute('download')) return;
    const url = new URL(anchor.href);
    if (url.origin !== window.location.origin || !isReactAppPath(url.pathname)) return;
    event.preventDefault();
    navigate(`${url.pathname}${url.search}${url.hash}`);
  }, [navigate]);

  return { location, navigate, onLinkClick };
}

export type AppRoute =
  | { kind: 'hub' }
  | { kind: 'filters' }
  | { kind: 'books' }
  | { kind: 'watchlist' }
  | { kind: 'requests' }
  | { kind: 'liked-songs' }
  | { kind: 'playlists' }
  | { kind: 'playlist'; playlistId: string }
  | { kind: 'live-tv' }
  | { kind: 'stats' }
  | { kind: 'admin' }
  | { kind: 'admin-iptv' }
  | { kind: 'admin-dashboard' }
  | { kind: 'admin-trending' }
  | { kind: 'admin-requests' }
  | { kind: 'watch'; key: string }
  | { kind: 'detail'; detailKind: 'movie' | 'series' | 'album' | 'artist' | 'person'; key: string };

export function parseRoute(pathname: string): AppRoute {
  const canonicalPath = localAppHref(pathname) || pathname;
  if (canonicalPath === '/filters') return { kind: 'filters' };
  if (canonicalPath === '/books') return { kind: 'books' };
  if (canonicalPath === '/watchlist') return { kind: 'watchlist' };
  if (canonicalPath === '/requests') return { kind: 'requests' };
  if (canonicalPath === '/liked-songs') return { kind: 'liked-songs' };
  if (canonicalPath === '/playlists') return { kind: 'playlists' };
  const playlist = canonicalPath.match(/^\/playlist\/([a-f0-9]{32})/);
  if (playlist) return { kind: 'playlist', playlistId: playlist[1] };
  if (canonicalPath === '/live-tv') return { kind: 'live-tv' };
  if (canonicalPath === '/stats') return { kind: 'stats' };
  if (canonicalPath === '/admin/dashboard') return { kind: 'admin-dashboard' };
  if (canonicalPath === '/admin/trending') return { kind: 'admin-trending' };
  if (canonicalPath === '/admin/requests') return { kind: 'admin-requests' };
  if (canonicalPath === '/admin/iptv') return { kind: 'admin-iptv' };
  if (canonicalPath === '/admin') return { kind: 'admin' };
  const watch = canonicalPath.match(/^\/play\/([^/?#]+)/);
  if (watch) return { kind: 'watch', key: decodeURIComponent(watch[1]) };
  const detail = canonicalPath.match(/^\/(movie|series|album|artist|person)\/([^/?#]+)/);
  if (detail) {
    return {
      kind: 'detail',
      detailKind: detail[1] as 'movie' | 'series' | 'album' | 'artist' | 'person',
      key: decodeURIComponent(detail[2]),
    };
  }
  return { kind: 'hub' };
}

export function useHubParams(locationKey: string, navigate: (href: string, replace?: boolean) => void) {
  const [params, setParams] = useState<HubParams>(() => parseParams());
  const paramsRef = useRef(params);

  useEffect(() => {
    const next = parseParams();
    paramsRef.current = next;
    setParams((current) => sameParams(current, next) ? current : next);
  }, [locationKey]);

  const update = useCallback((patch: Partial<HubParams>, replace = false) => {
    const current = paramsRef.current;
    const next: HubParams = { ...current, ...patch };
    if (
      patch.q !== undefined ||
      patch.tag !== undefined ||
      patch.quality !== undefined ||
      patch.genre !== undefined ||
      patch.year !== undefined ||
      patch.sort !== undefined ||
      patch.view !== undefined
    ) {
      next.offset = 0;
    }

    if (!sameParams(current, next)) {
      paramsRef.current = next;
      setParams(next);
    }
    navigate(appUrl(next), replace);
  }, [navigate]);

  return { params, update };
}
