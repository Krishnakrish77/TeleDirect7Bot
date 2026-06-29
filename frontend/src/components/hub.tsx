import { useCallback, useEffect, useRef, useState } from 'react';
import { clearAllContinue, dismissRecommendation, fetchContinueItems, fetchContinueMap } from '../api';
import { localAppHref } from '../navigation';
import { ChevronRightIcon, FilmIcon, PlayIcon, XIcon } from '../icons';
import type { ContinueEntry, ContinueItem, HeroItem, HubCard, HubParams, HubResponse, RecommendationMeta } from '../types';
import { tmdbImageUrl } from '../utils/tmdb';
import { formatExternalRating } from '../utils/externalRating';
import { MediaCard } from './mediaCard';

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

export function ContinueWatching() {
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
      try {
        const server = await fetchContinueMap(controller.signal);
        Object.entries(server).forEach(([key, value]) => {
          if (!raw[key] || (value.t || 0) > (raw[key].t || 0)) raw[key] = value;
        });
        localStorage.setItem('td:cw', JSON.stringify(raw));
      } catch (_) {
        // Signed-out users and offline sessions use local resume data.
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
  }, []);

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
    setEntries((current) => current.filter((entry) => entry.key !== key));
  };

  const forgetAll = () => {
    setEntries([]);
    void clearAllContinue()
      .then(() => { try { localStorage.removeItem('td:cw'); } catch (_) { /* ignore */ } });
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
          const percent = Math.max(4, Math.min(94, Math.round((entry.pos / entry.dur) * 100)));
          const title = entry.series_title || entry.title;
          const watchHref = entry.watch_url.replace(/^\/watch\//, '/app/watch/');
          return (
            <a key={entry.key} href={watchHref} className="continue-card">
              <img src={entry.poster_path ? tmdbImageUrl(entry.poster_path, 'w342') : entry.thumb_url} alt="" loading="lazy" decoding="async" />
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
              <span className="progress-track"><span style={{ width: `${percent}%` }} /></span>
              <span className="continue-card-type eyebrow">
                {entry.media_kind === 'audio' ? 'Music' : entry.kind === 'series' ? 'Series' : 'Movie'}
              </span>
              <strong>{title}</strong>
              <span>{entry.episode_label || entry.title}</span>
            </a>
          );
        })}
      </div>
    </section>
  );
}

export function ShelfRow({
  shelf,
  saved,
  onToggleSaved,
  onDismiss,
  onMarkWatched,
}: {
  shelf: { name: string; href: string | null; items: HubCard[]; dismissable?: boolean; recMeta?: Array<RecommendationMeta | null> };
  saved: Set<string>;
  onToggleSaved: (card: HubCard) => void;
  onDismiss?: (meta: RecommendationMeta, card: HubCard) => void;
  onMarkWatched?: (card: HubCard) => void;
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
  return (
    <section className="shelf-section">
      <div className="section-heading">
        <div>
          <h2>{shelf.name}</h2>
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
              onMarkWatched={onMarkWatched}
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
  onMarkWatched,
  loading = false,
}: {
  data: HubResponse;
  params: HubParams;
  saved: Set<string>;
  update: (patch: Partial<HubParams>, replace?: boolean) => void;
  onToggleSaved: (card: HubCard) => void;
  onMarkWatched?: (card: HubCard) => void;
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
                onMarkWatched={onMarkWatched}
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
        </div>
      )}
    </section>
  );
}
