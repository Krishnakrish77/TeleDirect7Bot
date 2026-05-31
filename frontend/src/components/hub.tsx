import { useCallback, useEffect, useState } from 'react';
import { fetchContinueItems } from '../api';
import { localAppHref } from '../navigation';
import { ChevronRightIcon, FilmIcon, FilterIcon, PlayIcon, XIcon } from '../icons';
import type { ContinueEntry, ContinueItem, HeroItem, HubCard, HubFilters, HubParams, HubResponse } from '../types';
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
        <h1>{hero.title}</h1>
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
  const [mobileOpen, setMobileOpen] = useState(false);
  const hasFilters = Boolean(
    query ||
    params.tag ||
    params.quality ||
    params.genre ||
    params.year ||
    params.sort !== 'newest',
  );
  const sortLabel = filters.sortOptions.find((option) => option.value === params.sort)?.label || 'Newest';
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

  const clearAll = (replace = false) => {
    setQuery('');
    update({
      q: '',
      tag: '',
      quality: '',
      genre: '',
      year: null,
      sort: 'newest',
      offset: 0,
    }, replace);
  };

  return (
    <>
      <div className="filter-bar desktop-filter-bar">
        <div className="filter-heading">
          <FilterIcon />
          <span>{catalogueSize ? `${catalogueSize.toLocaleString()} titles` : 'Library'}</span>
        </div>
        <label>
          <span>Year</span>
          <select value={params.year || ''} onChange={(event) => update({ year: event.currentTarget.value ? Number(event.currentTarget.value) : null })}>
            <option value="">Any</option>
            {filters.years.map((year) => <option key={year} value={year}>{year}</option>)}
          </select>
        </label>
        <label>
          <span>Quality</span>
          <select value={params.quality} onChange={(event) => update({ quality: event.currentTarget.value })}>
            <option value="">Any</option>
            {filters.qualities.map((quality) => <option key={quality} value={quality}>{quality}</option>)}
          </select>
        </label>
        <label>
          <span>Genre</span>
          <select value={params.genre} onChange={(event) => update({ genre: event.currentTarget.value })}>
            <option value="">Any</option>
            {filters.genres.map((genre) => <option key={genre} value={genre}>{genre}</option>)}
          </select>
        </label>
        <label>
          <span>Sort</span>
          <select value={params.sort} onChange={(event) => update({ sort: event.currentTarget.value })}>
            {filters.sortOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        {hasFilters && (
          <button className="text-button" type="button" onClick={() => clearAll()}>Reset</button>
        )}
      </div>

      <div className="mobile-filter-bar">
        <div className="filter-heading">
          <FilterIcon />
          <span>{catalogueSize ? `${catalogueSize.toLocaleString()} titles` : 'Library'}</span>
        </div>
        <button type="button" className="filter-summary-button" onClick={() => setMobileOpen(true)}>
          <span>Filters</span>
          <strong>{hasFilters ? 'Active' : 'Any'}</strong>
        </button>
        <button type="button" className="filter-summary-button" onClick={() => setMobileOpen(true)}>
          <span>Sort</span>
          <strong>{sortLabel}</strong>
        </button>
      </div>

      {mobileOpen && (
        <div className="filter-sheet-layer" role="dialog" aria-modal="true" aria-label="Filters">
          <button className="filter-sheet-scrim" type="button" onClick={() => setMobileOpen(false)} aria-label="Close filters" />
          <div className="filter-sheet">
            <div className="filter-sheet-heading">
              <div>
                <p className="eyebrow">Browse</p>
                <h2>Filters</h2>
              </div>
              <button className="icon-button" type="button" onClick={() => setMobileOpen(false)} aria-label="Close filters">
                <XIcon />
              </button>
            </div>
            <FilterOptionGroup
              title="Year"
              options={yearOptions}
              value={params.year ? String(params.year) : ''}
              onChange={(value) => update({ year: value ? Number(value) : null }, true)}
            />
            <FilterOptionGroup
              title="Quality"
              options={qualityOptions}
              value={params.quality}
              onChange={(value) => update({ quality: value }, true)}
            />
            <FilterOptionGroup
              title="Genre"
              options={genreOptions}
              value={params.genre}
              onChange={(value) => update({ genre: value }, true)}
            />
            <FilterOptionGroup
              title="Sort"
              options={filters.sortOptions}
              value={params.sort}
              onChange={(value) => update({ sort: value }, true)}
            />
            <div className="filter-sheet-actions">
              <button className="text-button" type="button" onClick={() => clearAll(true)} disabled={!hasFilters}>Reset</button>
              <button className="primary-action" type="button" onClick={() => setMobileOpen(false)}>Done</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function FilterOptionGroup({
  title,
  options,
  value,
  onChange,
}: {
  title: string;
  options: Array<{ value: string; label: string }>;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <section className="filter-option-group">
      <h3>{title}</h3>
      <div className="filter-option-list">
        {options.map((option) => (
          <button
            key={option.value || 'any'}
            type="button"
            className={value === option.value ? 'filter-option active' : 'filter-option'}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </section>
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
  if (!shelf.items.length) return null;
  return (
    <section className="shelf-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Library</p>
          <h2>{shelf.name}</h2>
        </div>
        {shelf.href && (
          <a className="section-link" href={localAppHref(shelf.href) || shelf.href}>
            <span>See all</span>
            <ChevronRightIcon />
          </a>
        )}
      </div>
      <div className="card-row">
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
