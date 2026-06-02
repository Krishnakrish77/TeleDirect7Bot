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

function appBase(): string {
  return window.location.pathname.startsWith('/static/app') ? '/static/app/' : '/app';
}

export function appUrl(params: Partial<HubParams>, path = ''): string {
  const qs = hubSearchParams(params);
  const base = appBase().replace(/\/$/, '');
  const target = `${base}${path}`;
  return qs.toString() ? `${target}?${qs}` : target;
}

function sameParams(left: HubParams, right: HubParams): boolean {
  return hubParamsKey(left) === hubParamsKey(right);
}

export function localAppHref(href: string | null): string | null {
  if (!href) return null;
  if (href === '/app') return appBase();
  if (href.startsWith('/app?')) return `${appBase()}${href.slice('/app'.length)}`;
  if (href === '/watchlist') return '/app/watchlist';
  if (href === '/liked-songs') return '/app/liked-songs';
  if (href === '/playlists') return '/app/playlists';
  if (/^\/playlist\/[a-f0-9]{32}$/.test(href)) return `/app${href}`;
  if (href === '/stats') return '/app/stats';
  if (href === '/admin') return '/app/admin';
  if (/^\/(movie|series|album|artist|person)\//.test(href)) return `/app${href}`;
  return href;
}

export function classicPathForApp(pathname: string, search: string): string {
  if (pathname === '/app' || pathname === '/static/app/app') {
    return `/${search}`;
  }
  const watch = pathname.match(/^\/app\/watch\/([^/?#]+)/);
  if (watch) return `/watch/${watch[1]}${search}`;
  if (pathname === '/app/watchlist') return `/watchlist${search}`;
  if (pathname === '/app/playlists') return '/?view=music';
  const playlist = pathname.match(/^\/app\/playlist\/([a-f0-9]{32})/);
  if (playlist) return '/?view=music';
  if (pathname === '/app/stats') return `/stats${search}`;
  if (pathname === '/app/admin' || pathname.startsWith('/app/admin/')) return `/admin${search}`;
  if (pathname === '/app/filters') return `/${search}`;
  const detail = pathname.match(/^\/app\/(movie|series|album|artist|person)\/([^/?#]+)/);
  if (detail) return `/${detail[1]}/${detail[2]}${search}`;
  return '/';
}

export function uiModeHref(mode: 'react' | 'classic', nextPath: string): string {
  return `/ui/${mode}?next=${encodeURIComponent(nextPath)}`;
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
  return `${url.pathname}${url.search}${url.hash}`;
}

function isReactAppPath(pathname: string): boolean {
  return pathname === '/app' || pathname.startsWith('/app/');
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
  | { kind: 'watchlist' }
  | { kind: 'liked-songs' }
  | { kind: 'playlists' }
  | { kind: 'playlist'; playlistId: string }
  | { kind: 'stats' }
  | { kind: 'admin' }
  | { kind: 'admin-dashboard' }
  | { kind: 'admin-trending' }
  | { kind: 'watch'; key: string }
  | { kind: 'detail'; detailKind: 'movie' | 'series' | 'album' | 'artist' | 'person'; key: string };

export function parseRoute(pathname: string): AppRoute {
  if (pathname === '/app/filters') return { kind: 'filters' };
  if (pathname === '/app/watchlist') return { kind: 'watchlist' };
  if (pathname === '/app/liked-songs') return { kind: 'liked-songs' };
  if (pathname === '/app/playlists') return { kind: 'playlists' };
  const playlist = pathname.match(/^\/app\/playlist\/([a-f0-9]{32})/);
  if (playlist) return { kind: 'playlist', playlistId: playlist[1] };
  if (pathname === '/app/stats') return { kind: 'stats' };
  if (pathname === '/app/admin/dashboard') return { kind: 'admin-dashboard' };
  if (pathname === '/app/admin/trending') return { kind: 'admin-trending' };
  if (pathname === '/app/admin') return { kind: 'admin' };
  const watch = pathname.match(/^\/app\/watch\/([^/?#]+)/);
  if (watch) return { kind: 'watch', key: decodeURIComponent(watch[1]) };
  const detail = pathname.match(/^\/app\/(movie|series|album|artist|person)\/([^/?#]+)/);
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
