import { FormEvent, useEffect, useMemo, useState } from 'react';
import { fetchAdminStatus, runAdminAction, runAdminMaintenance } from '../api';
import { ChartIcon, ChevronRightIcon, FilmIcon, FilterIcon, MusicIcon, PlayIcon, SearchIcon, ShieldIcon } from '../icons';
import { uiModeHref } from '../navigation';
import type { AdminItem, AdminResponse, AdminStatusResponse, User } from '../types';
import { ErrorPanel, LoadingRows } from './common';

type Navigate = (href: string, replace?: boolean) => void;

const QUALITY_OPTIONS = ['480p', '720p', '1080p', '4K'];

function formatBytes(bytes: number): string {
  if (!bytes) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

function adminUrl(params: URLSearchParams): string {
  const qs = params.toString();
  return qs ? `/app/admin?${qs}` : '/app/admin';
}

function statusRunning(status: AdminStatusResponse | null | undefined): boolean {
  if (!status) return false;
  return Boolean(
    status.seed?.running ||
    status.enrich?.running ||
    status.reindex?.running ||
    status.probe?.running ||
    status.episode_fill?.running ||
    status.migrate?.running,
  );
}

function progressPct(state: { total?: number; done?: number; scanned?: number } | undefined): number {
  if (!state?.total) return 0;
  const done = state.done ?? state.scanned ?? 0;
  return Math.max(0, Math.min(100, Math.round((done / state.total) * 100)));
}

function itemSubtitle(item: AdminItem): string {
  if (item.mediaKind === 'audio') {
    return [item.artist, item.albumTitle].filter(Boolean).join(' - ') || 'Music';
  }
  if (item.seriesTitle) {
    const episode = item.season !== null && item.episode !== null
      ? `S${String(item.season).padStart(2, '0')}E${String(item.episode).padStart(2, '0')}`
      : '';
    return [item.seriesTitle, episode].filter(Boolean).join(' - ');
  }
  return item.fileName || 'Standalone';
}

function selectedIds(selected: Set<number>): number[] {
  return Array.from(selected.values());
}

function AdminGate({
  user,
  onSignIn,
}: {
  user: User | null;
  onSignIn: () => void;
}) {
  return (
    <main className="admin-main">
      <div className="empty-state">
        <ShieldIcon />
        <strong>{user ? 'Admin access required' : 'Sign in to manage TeleDirect'}</strong>
        {user ? (
          <a className="secondary-action" href="/app">Back to library</a>
        ) : (
          <button type="button" className="primary-action" onClick={onSignIn}>Sign in</button>
        )}
      </div>
    </main>
  );
}

function AdminHero({ data }: { data: AdminResponse }) {
  const issueCount = data.stats.missing_poster + data.stats.missing_thumb + data.stats.duplicate_extras;
  const classicAdminHref = uiModeHref('classic', '/admin');
  return (
    <section className="admin-hero">
      <div className="admin-hero-copy">
        <p className="eyebrow">Operations</p>
        <h1>Admin console</h1>
        <p>
          {data.catalogueSize.toLocaleString()} visible items.
          {' '}
          {data.filteredCount.toLocaleString()} in the current view.
        </p>
        <div className="admin-hero-actions">
          <a className="primary-action" href={classicAdminHref}>
            <ShieldIcon />
            <span>Classic admin</span>
          </a>
          <a className="secondary-action" href="/admin/dashboard">
            <ChartIcon />
            <span>Dashboard</span>
          </a>
          <a className="secondary-action" href="/admin/trending-gaps">
            <ChevronRightIcon />
            <span>Trending gaps</span>
          </a>
        </div>
      </div>
      <div className="admin-metrics" aria-label="Catalogue health">
        <span>
          <FilmIcon />
          <strong>{data.stats.kinds.movies.toLocaleString()}</strong>
          <small>Movies</small>
        </span>
        <span>
          <PlayIcon />
          <strong>{data.stats.kinds.series_episodes.toLocaleString()}</strong>
          <small>Episodes</small>
        </span>
        <span>
          <MusicIcon />
          <strong>{data.stats.audio_count.toLocaleString()}</strong>
          <small>Tracks</small>
        </span>
        <span className={issueCount ? 'warn' : ''}>
          <FilterIcon />
          <strong>{issueCount.toLocaleString()}</strong>
          <small>Cleanup</small>
        </span>
      </div>
    </section>
  );
}

function AdminControls({
  data,
  query,
  setQuery,
  onSubmit,
  updateParam,
}: {
  data: AdminResponse;
  query: string;
  setQuery: (next: string) => void;
  onSubmit: (event: FormEvent) => void;
  updateParam: (patch: Record<string, string | number | null>) => void;
}) {
  return (
    <section className="admin-controls">
      <form className="admin-search" role="search" onSubmit={onSubmit}>
        <SearchIcon />
        <input
          value={query}
          onChange={(event) => setQuery(event.currentTarget.value)}
          placeholder="Search title, file, tag, artist, bin id"
          autoComplete="off"
        />
        <button type="submit">Search</button>
      </form>

      <div className="admin-select-row">
        <label>
          <span>Filter</span>
          <select
            value={data.filterName}
            onChange={(event) => updateParam({ filter: event.currentTarget.value, page: 1 })}
          >
            {data.filters.map((filter) => (
              <option key={filter.value} value={filter.value}>{filter.label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Sort</span>
          <select
            value={data.sortCol}
            onChange={(event) => updateParam({ sort: event.currentTarget.value, page: 1 })}
          >
            {data.sortOptions.map((sort) => (
              <option key={sort.value} value={sort.value}>{sort.label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Direction</span>
          <select
            value={data.sortDir}
            onChange={(event) => updateParam({ dir: event.currentTarget.value, page: 1 })}
          >
            <option value="desc">Desc</option>
            <option value="asc">Asc</option>
          </select>
        </label>
      </div>
    </section>
  );
}

function AdminStatusPanel({ status }: { status: AdminStatusResponse }) {
  const rows = [
    ['Seed', status.seed, status.seed?.running ? `${status.seed.scanned ?? 0}/${status.seed.total ?? 0} scanned` : 'Idle'],
    ['Enrich', status.enrich, status.enrich?.running ? `${status.enrich.done ?? 0}/${status.enrich.total ?? 0} - ${status.enrich.enriched ?? 0} matched` : 'Idle'],
    ['Re-index', status.reindex, status.reindex?.running ? `${status.reindex.done ?? 0}/${status.reindex.total ?? 0} processed` : 'Idle'],
    ['Codecs', status.probe, status.probe?.running ? `${status.probe.done ?? 0}/${status.probe.total ?? 0} - ${status.probe.found_incompatible ?? 0} flagged` : 'Idle'],
    ['Episodes', status.episode_fill, status.episode_fill?.running ? `${status.episode_fill.done ?? 0}/${status.episode_fill.total ?? 0} - ${status.episode_fill.filled ?? 0} filled` : 'Idle'],
    ['Mongo', status.migrate, status.migrate?.running || status.migrate?.phase === 'failed' ? `${status.migrate.phase || 'running'} - ${status.migrate.done ?? 0}/${status.migrate.total ?? 0}` : 'Idle'],
  ] as const;

  return (
    <section className="admin-panel admin-status-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Live work</p>
          <h2>Pipeline status</h2>
        </div>
        <span>{status.catalogue_size.toLocaleString()} indexed</span>
      </div>
      <div className="admin-status-grid">
        {rows.map(([label, state, detail]) => {
          const running = Boolean(state?.running || state?.phase === 'failed');
          return (
            <article key={label} className={running ? 'running' : ''}>
              <div>
                <strong>{label}</strong>
                <small>{detail}</small>
              </div>
              <i><b style={{ width: `${progressPct(state)}%` }} /></i>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function MaintenancePanel({
  busy,
  onRun,
}: {
  busy: string;
  onRun: (action: string, confirmMessage?: string) => void;
}) {
  const actions = [
    ['enrich', 'Enrich TMDB', 'Match missing video metadata'],
    ['reindex', 'Re-index', 'Rebuild grouping and quality'],
    ['probe-codecs', 'Probe codecs', 'Flag browser playback issues'],
    ['fetch-episodes', 'Episodes', 'Fetch TV episode metadata'],
    ['clear-audio-tmdb', 'Fix audio', 'Clear bad TMDB matches'],
    ['clear-audio-thumbs', 'Audio thumbs', 'Refresh music artwork'],
    ['clear-all-thumbs', 'All thumbs', 'Refresh every thumbnail'],
    ['dedupe', 'De-dupe', 'Delete duplicate uploads'],
    ['prune-stale', 'Prune stale', 'Remove missing BIN rows'],
    ['migrate-to-mongo', 'Mongo', 'Start migration'],
  ] as const;

  return (
    <section className="admin-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Maintenance</p>
          <h2>Run operations</h2>
        </div>
      </div>
      <div className="maintenance-grid">
        {actions.map(([action, label, description]) => {
          const dangerous = action === 'dedupe' || action === 'prune-stale' || action === 'clear-all-thumbs' || action === 'migrate-to-mongo';
          return (
            <button
              key={action}
              type="button"
              className={dangerous ? 'danger-zone' : ''}
              disabled={Boolean(busy)}
              onClick={() => onRun(action, dangerous ? `Run ${label}?` : undefined)}
            >
              <strong>{busy === action ? 'Running...' : label}</strong>
              <span>{description}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function BulkBar({
  selected,
  data,
  busy,
  setSelected,
  onAction,
}: {
  selected: Set<number>;
  data: AdminResponse;
  busy: string;
  setSelected: (next: Set<number>) => void;
  onAction: (action: string, payload?: Record<string, unknown>, confirmMessage?: string) => void;
}) {
  const [tags, setTags] = useState('');
  const [quality, setQuality] = useState('1080p');
  const [seriesTitle, setSeriesTitle] = useState('');
  const [season, setSeason] = useState('1');
  const [tmdbId, setTmdbId] = useState('');
  const [tmdbKind, setTmdbKind] = useState<'tv' | 'movie'>('tv');
  const count = selected.size;

  if (!count) {
    return (
      <section className="admin-bulk idle">
        <span>Select rows to enable bulk actions</span>
      </section>
    );
  }

  return (
    <section className="admin-bulk">
      <div className="admin-bulk-head">
        <strong>{count} selected</strong>
        <button type="button" onClick={() => setSelected(new Set())}>Clear</button>
      </div>
      <div className="admin-bulk-actions">
        <button type="button" disabled={Boolean(busy)} onClick={() => onAction('hide')}>Hide</button>
        <button type="button" disabled={Boolean(busy)} onClick={() => onAction('unhide')}>Unhide</button>
        <button type="button" disabled={Boolean(busy)} onClick={() => onAction('enrich')}>Enrich</button>
        <button type="button" disabled={Boolean(busy)} onClick={() => onAction('probe')}>Probe</button>
        <button
          type="button"
          className="danger"
          disabled={Boolean(busy)}
          onClick={() => onAction('delete', {}, `Delete ${count} entries?`)}
        >
          Delete
        </button>
      </div>
      <div className="admin-bulk-fields">
        <label>
          <span>Tags</span>
          <input value={tags} onChange={(event) => setTags(event.currentTarget.value)} placeholder="space separated" />
          <button type="button" disabled={Boolean(busy)} onClick={() => onAction('retag', { tags })}>Set</button>
        </label>
        <label>
          <span>Quality</span>
          <select value={quality} onChange={(event) => setQuality(event.currentTarget.value)}>
            {QUALITY_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
          <button type="button" disabled={Boolean(busy)} onClick={() => onAction('quality', { quality })}>Apply</button>
        </label>
        <label>
          <span>Series</span>
          <input
            value={seriesTitle}
            onChange={(event) => setSeriesTitle(event.currentTarget.value)}
            list="admin-known-series"
            placeholder="series title"
          />
          <input
            className="short"
            value={season}
            onChange={(event) => setSeason(event.currentTarget.value)}
            inputMode="numeric"
            placeholder="S"
          />
          <button type="button" disabled={Boolean(busy)} onClick={() => onAction('series', { seriesTitle, season })}>Set</button>
        </label>
        <label>
          <span>TMDB</span>
          <input value={tmdbId} onChange={(event) => setTmdbId(event.currentTarget.value)} inputMode="numeric" placeholder="id" />
          <select value={tmdbKind} onChange={(event) => setTmdbKind(event.currentTarget.value as 'tv' | 'movie')}>
            <option value="tv">TV</option>
            <option value="movie">Movie</option>
          </select>
          <button type="button" disabled={Boolean(busy)} onClick={() => onAction('tmdb-id', { tmdbId, tmdbKind })}>Apply</button>
        </label>
      </div>
      <datalist id="admin-known-series">
        {data.knownSeries.map((series) => <option key={series} value={series} />)}
      </datalist>
    </section>
  );
}

function AdminItemRow({
  item,
  selected,
  onSelect,
  onToggleHidden,
}: {
  item: AdminItem;
  selected: boolean;
  onSelect: (checked: boolean) => void;
  onToggleHidden: () => void;
}) {
  return (
    <article className={['admin-row', item.hidden ? 'hidden-row' : '', item.duplicate ? 'duplicate-row' : ''].filter(Boolean).join(' ')}>
      <label className="admin-row-check">
        <input
          type="checkbox"
          checked={selected}
          onChange={(event) => onSelect(event.currentTarget.checked)}
          aria-label={`Select ${item.title}`}
        />
      </label>
      <a className="admin-row-art" href={item.watchHref}>
        {item.posterUrl ? <img src={item.posterUrl} alt="" loading="lazy" decoding="async" /> : <FilmIcon />}
      </a>
      <div className="admin-row-copy">
        <a href={item.watchHref}>
          <strong>{item.title}</strong>
        </a>
        <span>{itemSubtitle(item)}</span>
        <small>{item.fileName || `bin:${item.messageId}`}</small>
        <div className="admin-chip-row">
          {item.year && <i>{item.year}</i>}
          {item.quality && <i>{item.quality}</i>}
          {item.mediaKind === 'audio' && <i>Music</i>}
          {item.hidden && <i>Hidden</i>}
          {item.duplicate && <i className="warn">Duplicate</i>}
          {item.missingPoster && <i className="warn">No poster</i>}
          {item.missingThumb && <i className="warn">No thumb</i>}
        </div>
      </div>
      <div className="admin-row-meta">
        <span>{item.fileSizeLabel || formatBytes(item.fileSize)}</span>
        <span>bin:{item.messageId}</span>
        <span>{item.tmdbId ? `TMDB ${item.tmdbId}` : 'No TMDB'}</span>
      </div>
      <div className="admin-row-actions">
        <button type="button" onClick={onToggleHidden}>{item.hidden ? 'Unhide' : 'Hide'}</button>
        <a href={uiModeHref('classic', `/admin?q=bin:${item.messageId}`)}>Classic edit</a>
      </div>
    </article>
  );
}

function AdminList({
  data,
  selected,
  setSelected,
  onToggleHidden,
  updateParam,
}: {
  data: AdminResponse;
  selected: Set<number>;
  setSelected: (next: Set<number>) => void;
  onToggleHidden: (item: AdminItem) => void;
  updateParam: (patch: Record<string, string | number | null>) => void;
}) {
  const allPageSelected = data.items.length > 0 && data.items.every((item) => selected.has(item.messageId));
  const toggleAll = (checked: boolean) => {
    const next = new Set(selected);
    for (const item of data.items) {
      if (checked) next.add(item.messageId);
      else next.delete(item.messageId);
    }
    setSelected(next);
  };

  return (
    <section className="admin-panel admin-list-panel">
      <div className="admin-list-head">
        <label>
          <input type="checkbox" checked={allPageSelected} onChange={(event) => toggleAll(event.currentTarget.checked)} />
          <span>{data.filteredCount.toLocaleString()} results</span>
        </label>
        <span>Page {data.page} of {data.totalPages}</span>
      </div>
      <div className="admin-list">
        {data.items.map((item) => (
          <AdminItemRow
            key={item.messageId}
            item={item}
            selected={selected.has(item.messageId)}
            onSelect={(checked) => {
              const next = new Set(selected);
              if (checked) next.add(item.messageId);
              else next.delete(item.messageId);
              setSelected(next);
            }}
            onToggleHidden={() => onToggleHidden(item)}
          />
        ))}
      </div>
      <div className="admin-pagination">
        <button type="button" disabled={data.page <= 1} onClick={() => updateParam({ page: data.page - 1 })}>Prev</button>
        <span>{((data.page - 1) * data.pageSize) + 1}-{((data.page - 1) * data.pageSize) + data.items.length} of {data.filteredCount}</span>
        <button type="button" disabled={data.page >= data.totalPages} onClick={() => updateParam({ page: data.page + 1 })}>Next</button>
      </div>
    </section>
  );
}

export function AdminPage({
  user,
  data,
  loading,
  error,
  locationSearch,
  navigate,
  onSignIn,
  reload,
  updateData,
}: {
  user: User | null;
  data: AdminResponse | null;
  loading: boolean;
  error: string;
  locationSearch: string;
  navigate: Navigate;
  onSignIn: () => void;
  reload: () => void | (() => void);
  updateData: (updater: (current: AdminResponse | null) => AdminResponse | null) => void;
}) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const visibleIds = useMemo(() => new Set(data?.items.map((item) => item.messageId) || []), [data]);
  const pipelineRunning = statusRunning(data?.status);

  useEffect(() => {
    if (data) setQuery(data.searchQ);
  }, [data?.searchQ]);

  useEffect(() => {
    setSelected((current) => {
      const next = new Set<number>();
      current.forEach((id) => {
        if (visibleIds.has(id)) next.add(id);
      });
      return next.size === current.size ? current : next;
    });
  }, [visibleIds]);

  useEffect(() => {
    if (!pipelineRunning) return undefined;
    const timer = window.setInterval(() => {
      fetchAdminStatus()
        .then((status) => updateData((current) => current ? { ...current, status } : current))
        .catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [pipelineRunning, updateData]);

  if (!user || !user.is_admin) {
    return <AdminGate user={user} onSignIn={onSignIn} />;
  }

  const updateParam = (patch: Record<string, string | number | null>) => {
    const params = new URLSearchParams(locationSearch);
    Object.entries(patch).forEach(([key, value]) => {
      if (value === null || value === '' || (key === 'page' && Number(value) <= 1)) {
        params.delete(key);
      } else {
        params.set(key, String(value));
      }
    });
    navigate(adminUrl(params));
  };

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    updateParam({ q: query.trim(), page: 1 });
  };

  const runSelectedAction = async (
    action: string,
    payload: Record<string, unknown> = {},
    confirmMessage?: string,
  ) => {
    if (confirmMessage && !window.confirm(confirmMessage)) return;
    setBusy(action);
    setNotice('');
    try {
      const response = await runAdminAction({ action, ids: selectedIds(selected), ...payload });
      setNotice(response.message);
      if (response.status) {
        updateData((current) => current ? { ...current, status: response.status! } : current);
      }
      setSelected(new Set());
      reload();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setBusy('');
    }
  };

  const runSingleHiddenAction = async (item: AdminItem) => {
    const action = item.hidden ? 'unhide' : 'hide';
    setBusy(`${action}:${item.messageId}`);
    setNotice('');
    try {
      const response = await runAdminAction({ action, ids: [item.messageId] });
      setNotice(response.message);
      reload();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setBusy('');
    }
  };

  const runMaintenanceAction = async (action: string, confirmMessage?: string) => {
    if (confirmMessage && !window.confirm(confirmMessage)) return;
    setBusy(action);
    setNotice('');
    try {
      const response = await runAdminMaintenance(action);
      setNotice(response.message);
      if (response.status) {
        updateData((current) => current ? { ...current, status: response.status! } : current);
      }
      reload();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Operation failed');
    } finally {
      setBusy('');
    }
  };

  return (
    <main className="admin-main">
      {loading && !data && <LoadingRows variant="detail" />}
      {error && <ErrorPanel message={error} />}

      {data && (
        <>
          <AdminHero data={data} />
          {notice && <p className="admin-notice" role="status">{notice}</p>}
          <AdminStatusPanel status={data.status} />
          <MaintenancePanel busy={busy} onRun={runMaintenanceAction} />
          <AdminControls
            data={data}
            query={query}
            setQuery={setQuery}
            onSubmit={submitSearch}
            updateParam={updateParam}
          />
          <BulkBar
            selected={selected}
            data={data}
            busy={busy}
            setSelected={setSelected}
            onAction={runSelectedAction}
          />
          <AdminList
            data={data}
            selected={selected}
            setSelected={setSelected}
            onToggleHidden={runSingleHiddenAction}
            updateParam={updateParam}
          />
        </>
      )}
    </main>
  );
}
