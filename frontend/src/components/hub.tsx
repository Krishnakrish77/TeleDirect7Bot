import { useCallback, useEffect, useRef, useState } from 'react';
import { clearAllContinue, deleteContinueEntry, dismissRecommendation, fetchContinueItems, fetchContinueMap } from '../api';
import { localAppHref } from '../navigation';
import { ChevronRightIcon, FilmIcon, PlayIcon, UserIcon, XIcon } from '../icons';
import type { ContinueEntry, ContinueItem, HeroItem, HubCard, HubParams, HubResponse, RecommendationMeta } from '../types';
import { tmdbImageUrl } from '../utils/tmdb';
import { formatExternalRating } from '../utils/externalRating';
import { MediaCard } from './mediaCard';

type HubShelf = { name: string; href: string | null; items: HubCard[]; dismissable?: boolean; recMeta?: Array<RecommendationMeta | null> };
export const HOME_SHELF_LIMIT = 7;

const SHELF_PRIORITY: Array<[RegExp, number]> = [
  [/^recommended for you$/i, 0],
  [/^because you /i, 1],
  [/^recently added$/i, 2],
  [/^new episodes$/i, 3],
  [/^trending$/i, 4],
  [/^most played$/i, 5],
  [/^music$/i, 6],
  [/^series$/i, 7],
  [/^recently added movies$/i, 8],
  [/^hidden gems$/i, 9],
];

export function shelfPresentation(name: string): { title: string; eyebrow: string } {
  const normalised = name.trim().toLowerCase();
  if (normalised === 'recommended for you') return { title: name, eyebrow: 'For you' };
  if (normalised === 'recently added') return { title: 'New in your library', eyebrow: 'Latest' };
  if (normalised === 'new episodes') return { title: name, eyebrow: 'Series updates' };
  if (normalised === 'trending') return { title: 'Trending now', eyebrow: 'In demand' };
  if (normalised === 'most played') return { title: 'Most played', eyebrow: 'Replay value' };
  if (normalised === 'recently added movies') return { title: 'New movies', eyebrow: 'Movies' };
  if (normalised === 'series') return { title: 'Series', eyebrow: 'Shows' };
  if (normalised === 'music') return { title: 'Music', eyebrow: 'Audio' };
  if (normalised === 'hidden gems') return { title: 'Worth a look', eyebrow: 'Discovery' };
  return { title: name, eyebrow: 'Browse' };
}

export function sortHomeShelves(shelves: HubShelf[]): HubShelf[] {
  const rank = (name: string) => SHELF_PRIORITY.find(([pattern]) => pattern.test(name))?.[1] ?? 20;
  return [...shelves].sort((a, b) => {
    const byRank = rank(a.name) - rank(b.name);
    if (byRank !== 0) return byRank;
    return a.name.localeCompare(b.name);
  });
}

export function budgetHomeShelves(shelves: HubShelf[], limit = HOME_SHELF_LIMIT): HubShelf[] {
  const safeLimit = Number.isFinite(limit) ? Math.max(1, Math.floor(limit)) : HOME_SHELF_LIMIT;
  return sortHomeShelves(shelves.filter((shelf) => shelf.items.length > 0)).slice(0, safeLimit);
}

export function HeroStage({ heroes }: { heroes: HeroItem[] }) {
  const [active, setActive] = useState(0);
  const hero = heroes[active] || heroes[0];

  useEffect(() => {
    if (heroes.length < 2) return;
    let timer: number | undefined;
    const start = () => {
      window.clearInterval(timer);
      timer = window.setInterval(() => {
        setActive((current) => (current + 1) % heroes.length);
      }, 7000);
    };
    const onVisibility = () => document.hidden ? window.clearInterval(timer) : start();
    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [heroes.length]);

  if (!hero) return null;

  const bg = hero.backdropUrl || hero.posterUrl;

  return (
    <section className="hero-stage" aria-label={hero.title}>
      <img className="hero-bg" src={bg} alt="" decoding="async" fetchPriority="high" />
      <div className="hero-vignette" />
      <div className="hero-content">
        <p className="eyebrow">{hero.eyebrow}</p>
        <h1 dir="auto">{hero.title}</h1>
        {hero.overview && <p className="hero-overview">{hero.overview}</p>}
        <div className="hero-meta">
          {hero.meta.map((part) => <span key={part}>{part}</span>)}
          {formatExternalRating(hero.externalRating) && (
            <span className="hero-rating">{formatExternalRating(hero.externalRating)}</span>
          )}
        </div>
        <div className="hero-actions">
          <a className="primary-action" href={hero.playHref}>
            <PlayIcon />
            <span>Play</span>
          </a>
          <a className="secondary-action" href={hero.detailsHref}>
            <span>Details</span>
            <ChevronRightIcon />
          </a>
        </div>
      </div>
      {heroes.length > 1 && (
        <div className="hero-strip" aria-label="Featured titles">
          {heroes.map((item, index) => (
            <button
              key={item.itemId}
              type="button"
              className={index === active ? 'active' : ''}
              onClick={() => setActive(index)}
              aria-label={item.title}
              aria-current={index === active ? 'true' : undefined}
            >
              <img src={item.posterUrl} alt="" loading="lazy" decoding="async" />
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

export function ContinueWatching({ serverSyncEnabled = false }: { serverSyncEnabled?: boolean }) {
  const [entries, setEntries] = useState<Array<ContinueEntry & ContinueItem>>([]);

  const load = useCallback(() => {
    const controller = new AbortController();
    let raw: Record<string, Omit<ContinueEntry, 'key'>> = {};
    try {
      raw = JSON.parse(localStorage.getItem('td:cw') || '{}') || {};
    } catch (_) {
      raw = {};
    }

    const hydrate = async () => {
      if (serverSyncEnabled) {
        try {
          const server = await fetchContinueMap(controller.signal);
          // Server is canonical: prune local entries the server has deleted,
          // then merge in newer server values.
          Object.keys(raw).forEach((key) => { if (!(key in server)) delete raw[key]; });
          Object.entries(server).forEach(([key, value]) => {
            if (!raw[key] || (value.t || 0) > (raw[key].t || 0)) raw[key] = value;
          });
          localStorage.setItem('td:cw', JSON.stringify(raw));
        } catch (_) {
          // Offline signed-in sessions use local resume data.
        }
      }

      const local = Object.entries(raw)
        .map(([key, value]) => ({ key, ...value }))
        .filter((entry) => entry.dur > 0 && entry.pos / entry.dur > 0.02 && entry.pos / entry.dur < 0.95)
        .sort((a, b) => (b.t || 0) - (a.t || 0))
        .slice(0, 10);

      if (!local.length) {
        setEntries([]);
        return;
      }

      try {
        const items = await fetchContinueItems(local.map((entry) => entry.key), controller.signal);
        const byKey = new Map(items.map((item) => [item.key, item]));
        setEntries(local.flatMap((entry) => {
          const item = byKey.get(entry.key);
          return item ? [{ ...entry, ...item }] : [];
        }));
      } catch (_) {
        setEntries([]);
      }
    };
    void hydrate();
    return () => controller.abort();
  }, [serverSyncEnabled]);

  useEffect(() => {
    const cleanup = load();
    const onStorage = (event: StorageEvent) => {
      if (event.key && event.key !== 'td:cw') return;
      load();
    };
    window.addEventListener('storage', onStorage);
    return () => {
      if (cleanup) cleanup();
      window.removeEventListener('storage', onStorage);
    };
  }, [load]);

  if (!entries.length) return null;

  const forget = (key: string) => {
    try {
      const data = JSON.parse(localStorage.getItem('td:cw') || '{}') || {};
      delete data[key];
      localStorage.setItem('td:cw', JSON.stringify(data));
    } catch (_) {
      // local-only convenience state; ignore storage failures.
    }
    if (serverSyncEnabled) void deleteContinueEntry(key).catch(() => undefined);
    setEntries((current) => current.filter((entry) => entry.key !== key));
  };

  const forgetAll = () => {
    setEntries([]);
    try {
      localStorage.removeItem('td:cw');
    } catch (_) {
      // local-only convenience state; ignore storage failures.
    }
    if (serverSyncEnabled) void clearAllContinue().catch(() => undefined);
  };

  return (
    <section className="continue-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Resume</p>
          <h2>Continue playing</h2>
        </div>
        <button type="button" className="secondary-action compact-action" onClick={forgetAll}>
          Clear all
        </button>
      </div>
      <div className="continue-row">
        {entries.map((entry) => {
          const pct = entry.dur > 0 ? entry.pos / entry.dur : 0;
          const percent = Math.max(4, Math.min(94, Math.round(pct * 100)));
          const title = entry.series_title || entry.title;

          // ≥85% through a series episode → pivot to next-episode card
          const showNext = pct >= 0.85 && Boolean(entry.next_episode);
          const next = entry.next_episode;
          const displayUrl = showNext && next
            ? next.watch_url.replace(/^\/watch\//, '/app/watch/')
            : entry.watch_url.replace(/^\/watch\//, '/app/watch/');
          const displayPoster = showNext && next
            ? (next.poster_path ? tmdbImageUrl(next.poster_path, 'w342') : next.thumb_url)
            : (entry.poster_path ? tmdbImageUrl(entry.poster_path, 'w342') : entry.thumb_url);

          return (
            <a key={entry.key} href={displayUrl} className={showNext ? 'continue-card up-next-card' : 'continue-card'}>
              <img src={displayPoster} alt="" loading="lazy" decoding="async" />
              <button
                type="button"
                className="forget-button"
                onClick={(event) => {
                  event.preventDefault();
                  forget(entry.key);
                }}
                aria-label="Remove"
              >
                <XIcon />
              </button>
              {showNext ? (
                <>
                  <span className="progress-track"><span style={{ width: '0%' }} /></span>
                  <span className="continue-card-type eyebrow up-next-label">Up next</span>
                  <strong>{title}</strong>
                  <span>{next!.episode_label || next!.title}</span>
                </>
              ) : (
                <>
                  <span className="progress-track"><span style={{ width: `${percent}%` }} /></span>
                  <span className="continue-card-type eyebrow">
                    {entry.media_kind === 'audio' ? 'Music' : entry.kind === 'series' ? 'Series' : 'Movie'}
                  </span>
                  <strong>{title}</strong>
                  <span>{entry.episode_label || entry.title}</span>
                </>
              )}
            </a>
          );
        })}
      </div>
    </section>
  );
}

export function RecommendationTeaser({ onSignIn }: { onSignIn: () => void }) {
  return (
    <section className="recommendation-teaser" aria-label="Personalized recommendations">
      <div className="recommendation-teaser-copy">
        <p className="eyebrow">For you</p>
        <h2>Personal picks unlock after sign-in</h2>
        <p>Ratings, saves, and play history tune the rows on this Home screen.</p>
      </div>
      <button type="button" className="primary-action compact-action" onClick={onSignIn}>
        <UserIcon />
        <span>Sign in</span>
      </button>
    </section>
  );
}

export function ShelfRow({
  shelf,
  saved,
  onToggleSaved,
  onDismiss,
}: {
  shelf: HubShelf;
  saved: Set<string>;
  onToggleSaved: (card: HubCard) => void;
  onDismiss?: (meta: RecommendationMeta, card: HubCard) => void;
}) {
  const rowRef = useRef<HTMLDivElement | null>(null);
  const [canScrollBack, setCanScrollBack] = useState(false);
  const [canScrollForward, setCanScrollForward] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  const updateScrollState = useCallback(() => {
    const row = rowRef.current;
    if (!row) return;
    const maxScroll = row.scrollWidth - row.clientWidth;
    setCanScrollBack(row.scrollLeft > 2);
    setCanScrollForward(row.scrollLeft < maxScroll - 2);
  }, []);

  useEffect(() => {
    updateScrollState();
    const row = rowRef.current;
    if (!row) return;
    row.addEventListener('scroll', updateScrollState, { passive: true });
    window.addEventListener('resize', updateScrollState);
    return () => {
      row.removeEventListener('scroll', updateScrollState);
      window.removeEventListener('resize', updateScrollState);
    };
  }, [shelf.items.length, updateScrollState]);

  const scrollByPage = (direction: -1 | 1) => {
    const row = rowRef.current;
    if (!row) return;
    row.scrollBy({ left: direction * row.clientWidth * 0.82, behavior: 'smooth' });
    window.setTimeout(updateScrollState, 260);
  };

  if (!shelf.items.length) return null;
  const presentation = shelfPresentation(shelf.name);
  return (
    <section className="shelf-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{presentation.eyebrow}</p>
          <h2>{presentation.title}</h2>
        </div>
        <div className="section-actions">
          {shelf.href && (
            <a className="section-link" href={localAppHref(shelf.href) || shelf.href}>
              <span>See all</span>
              <ChevronRightIcon />
            </a>
          )}
          <div className="rail-controls" aria-label={`${shelf.name} carousel controls`}>
            <button type="button" className="icon-button" onClick={() => scrollByPage(-1)} disabled={!canScrollBack} aria-label="Scroll back">
              <ChevronRightIcon />
            </button>
            <button type="button" className="icon-button" onClick={() => scrollByPage(1)} disabled={!canScrollForward} aria-label="Scroll forward">
              <ChevronRightIcon />
            </button>
          </div>
        </div>
      </div>
      <div className="card-row" ref={rowRef}>
        {shelf.items.map((card, index) => {
          const recMeta = shelf.dismissable ? shelf.recMeta?.[index] || card.recMeta || null : null;
          if (dismissed.has(card.itemId)) return null;
          return (
            <MediaCard
              key={`${card.type}:${card.itemId}`}
              card={card}
              saved={saved.has(card.itemId)}
              onToggleSaved={onToggleSaved}
              dismissMeta={recMeta}
              onDismiss={(meta, dismissedCard) => {
                setDismissed((current) => new Set(current).add(dismissedCard.itemId));
                if (onDismiss) onDismiss(meta, dismissedCard);
                else void dismissRecommendation(meta.tmdbId, meta.kind).catch(() => undefined);
              }}
            />
          );
        })}
      </div>
    </section>
  );
}

export function GridView({
  data,
  params,
  saved,
  update,
  onToggleSaved,
  loading = false,
}: {
  data: HubResponse;
  params: HubParams;
  saved: Set<string>;
  update: (patch: Partial<HubParams>, replace?: boolean) => void;
  onToggleSaved: (card: HubCard) => void;
  loading?: boolean;
}) {
  const isMusicGrid =
    params.view === 'music' ||
    (data.items.length > 0 && data.items.every((card) => card.aspect === 'square'));
  const priorityCount = 8;
  const isLoadingMore = loading && params.offset > 0;
  const resultCountLabel = `${data.total.toLocaleString()} result${data.total === 1 ? '' : 's'}`;
  const resultEyebrow = loading && !isLoadingMore
    ? 'Updating results'
    : resultCountLabel;

  return (
    <section className={loading ? 'grid-section grid-refetching' : 'grid-section'} aria-busy={loading}>
      <div className="section-heading">
        <div>
          <p className="eyebrow">{resultEyebrow}</p>
          <h2>{params.q ? `Search: ${params.q}` : 'Browse'}</h2>
        </div>
      </div>
      {loading && (
        <p className="grid-refresh-note" role="status">
          Updating results...
        </p>
      )}
      {data.items.length ? (
        <>
          <div className={isMusicGrid ? 'media-grid music-grid' : 'media-grid'}>
            {data.items.map((card, index) => (
              <MediaCard
                key={`${card.type}:${card.itemId}`}
                card={card}
                saved={saved.has(card.itemId)}
                priority={index < priorityCount}
                onToggleSaved={onToggleSaved}
                interactionDisabled={loading}
              />
            ))}
          </div>
          {data.nextOffset !== null && (!loading || isLoadingMore) && (
            <div className="load-more-wrap">
              <button
                type="button"
                className="secondary-action"
                disabled={loading}
                onClick={() => update({ offset: data.nextOffset || 0 }, true)}
              >
                <span>{isLoadingMore ? 'Loading...' : 'More'}</span>
                <ChevronRightIcon />
              </button>
            </div>
          )}
          {data.nextOffset === null && !loading && (
            <p className="result-footer">Showing all {data.total.toLocaleString()} result{data.total === 1 ? '' : 's'}</p>
          )}
        </>
      ) : (
        <div className="empty-state">
          <FilmIcon />
          <strong>{data.emptyText}</strong>
          <span>Try a broader search or clear a filter to see more titles.</span>
        </div>
      )}
    </section>
  );
}
