import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchContinueItems } from '../api';
import { localAppHref } from '../navigation';
import { ChevronRightIcon, FilmIcon, FilterIcon, PlayIcon, XIcon } from '../icons';
import type { ContinueEntry, ContinueItem, FilterOption, HeroItem, HubCard, HubFilters, HubParams, HubResponse, ViewValue } from '../types';
import { MediaCard } from './mediaCard';

export function HeroStage({ heroes }: { heroes: HeroItem[] }) {
  const [active, setActive] = useState(0);
  const hero = heroes[active] || heroes[0];

  useEffect(() => {
    if (heroes.length < 2) return;
    const timer = window.setInterval(() => {
      setActive((current) => (current + 1) % heroes.length);
    }, 7000);
    return () => window.clearInterval(timer);
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
            >
              <img src={item.posterUrl} alt="" loading="lazy" decoding="async" />
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

export function FilterBar({
  filters,
  catalogueSize,
  params,
  query,
  setQuery,
  update,
}: {
  filters: HubFilters;
  catalogueSize: number;
  params: HubParams;
  query: string;
  setQuery: (next: string) => void;
  update: (patch: Partial<HubParams>, replace?: boolean) => void;
}) {
  const hasFilters = Boolean(
    query ||
    params.tag ||
    params.quality ||
    params.genre ||
    params.year ||
    params.sort !== 'newest',
  );
  const yearOptions = [
    { value: '', label: 'Any' },
    ...filters.years.map((year) => ({ value: String(year), label: String(year) })),
  ];
  const qualityOptions = [
    { value: '', label: 'Any' },
    ...filters.qualities.map((quality) => ({ value: quality, label: quality })),
  ];
  const genreOptions = [
    { value: '', label: 'Any' },
    ...filters.genres.map((genre) => ({ value: genre, label: genre })),
  ];
  const viewOptions = filters.views.length ? filters.views : [
    { value: '', label: 'All' },
    { value: 'movies', label: 'Movies' },
    { value: 'series', label: 'Series' },
    { value: 'music', label: 'Music' },
  ];
  const controls: Array<{
    id: string;
    label: string;
    value: string;
    options: FilterOption[];
    onChange: (value: string) => void;
  }> = [];

  if (!params.view || query) {
    controls.push({
      id: 'view',
      label: 'Type',
      value: params.view,
      options: viewOptions,
      onChange: (value) => update({ view: value as ViewValue }),
    });
  }

  controls.push(
    {
      id: 'year',
      label: 'Year',
      value: params.year ? String(params.year) : '',
      options: yearOptions,
      onChange: (value) => update({ year: value ? Number(value) : null }),
    },
    {
      id: 'quality',
      label: 'Quality',
      value: params.quality,
      options: qualityOptions,
      onChange: (value) => update({ quality: value }),
    },
    {
      id: 'genre',
      label: 'Genre',
      value: params.genre,
      options: genreOptions,
      onChange: (value) => update({ genre: value }),
    },
    {
      id: 'sort',
      label: 'Sort',
      value: params.sort,
      options: filters.sortOptions,
      onChange: (value) => update({ sort: value }),
    },
  );

  const clearAll = (replace = false) => {
    setQuery('');
    update({
      q: '',
      tag: '',
      quality: '',
      genre: '',
      year: null,
      sort: 'newest',
      view: '',
      offset: 0,
    }, replace);
  };

  return (
    <div className="filter-bar" aria-label="Browse filters">
      <div className="filter-heading">
        <FilterIcon />
        <span>{catalogueSize ? `${catalogueSize.toLocaleString()} titles` : 'Library'}</span>
      </div>
      <div className="filter-controls">
        {controls.map((control) => (
          <label key={control.id} className="filter-control">
            <span>{control.label}</span>
            <select value={control.value} onChange={(event) => control.onChange(event.currentTarget.value)} aria-label={control.label}>
              {control.options.map((option) => (
                <option key={option.value || 'any'} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {hasFilters && (
        <button className="text-button" type="button" onClick={() => clearAll()}>Reset</button>
      )}
    </div>
  );
}

export function ContinueWatching() {
  const [entries, setEntries] = useState<Array<ContinueEntry & ContinueItem>>([]);

  const load = useCallback(() => {
    let raw: Record<string, Omit<ContinueEntry, 'key'>> = {};
    try {
      raw = JSON.parse(localStorage.getItem('td:cw') || '{}') || {};
    } catch (_) {
      raw = {};
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

    const controller = new AbortController();
    fetchContinueItems(local.map((entry) => entry.key), controller.signal)
      .then((items) => {
        const byKey = new Map(items.map((item) => [item.key, item]));
        setEntries(local.flatMap((entry) => {
          const item = byKey.get(entry.key);
          return item ? [{ ...entry, ...item }] : [];
        }));
      })
      .catch(() => setEntries([]));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const cleanup = load();
    const onStorage = () => load();
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

  return (
    <section className="continue-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Resume</p>
          <h2>Continue playing</h2>
        </div>
      </div>
      <div className="continue-row">
        {entries.map((entry) => {
          const percent = Math.max(4, Math.min(94, Math.round((entry.pos / entry.dur) * 100)));
          const title = entry.series_title || entry.title;
          return (
            <a key={entry.key} href={entry.watch_url} className="continue-card">
              <img src={entry.poster_path ? `https://image.tmdb.org/t/p/w342${entry.poster_path}` : entry.thumb_url} alt="" loading="lazy" decoding="async" />
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
}: {
  shelf: { name: string; href: string | null; items: HubCard[] };
  saved: Set<string>;
  onToggleSaved: (card: HubCard) => void;
}) {
  const rowRef = useRef<HTMLDivElement | null>(null);
  const [canScrollBack, setCanScrollBack] = useState(false);
  const [canScrollForward, setCanScrollForward] = useState(false);

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
        {shelf.items.map((card) => (
          <MediaCard
            key={`${card.type}:${card.itemId}`}
            card={card}
            saved={saved.has(card.itemId)}
            onToggleSaved={onToggleSaved}
          />
        ))}
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
}: {
  data: HubResponse;
  params: HubParams;
  saved: Set<string>;
  update: (patch: Partial<HubParams>, replace?: boolean) => void;
  onToggleSaved: (card: HubCard) => void;
}) {
  const isMusicGrid =
    params.view === 'music' ||
    (data.items.length > 0 && data.items.every((card) => card.aspect === 'square'));
  const priorityCount = 8;

  return (
    <section className="grid-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{data.total.toLocaleString()} results</p>
          <h2>{params.q ? `Search: ${params.q}` : 'Browse'}</h2>
        </div>
      </div>
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
              />
            ))}
          </div>
          {data.nextOffset !== null && (
            <div className="load-more-wrap">
              <button
                type="button"
                className="secondary-action"
                onClick={() => update({ offset: data.nextOffset || 0 }, true)}
              >
                <span>More</span>
                <ChevronRightIcon />
              </button>
            </div>
          )}
          {data.nextOffset === null && (
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
