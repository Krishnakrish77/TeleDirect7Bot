import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { aiSuggestItem, clearAdminItemTmdb, fetchAdminItem, fetchAdminSeriesList, fetchAdminStatus, fetchAiModels, fetchTmdbPreview, mergeAdminSeries, resolveTmdbImdb, runAdminAction, runAdminMaintenance, saveAdminItem } from '../api';
import { FilmIcon, FilterIcon, MusicIcon, PlayIcon, SearchIcon, ShieldIcon, XIcon } from '../icons';

export function AdminNav({
  routeKind,
  locationSearch,
}: {
  routeKind: string;
  locationSearch: string;
}) {
  const tab = new URLSearchParams(locationSearch).get('tab');
  const is = (kind: string, t?: string) =>
    routeKind === kind && (t === undefined ? !tab : tab === t);
  return (
    <nav className="admin-nav" role="tablist" aria-label="Admin sections">
      <a role="tab" aria-selected={is('admin')} className={is('admin') ? 'active' : ''} href="/app/admin">Catalogue</a>
      <a role="tab" aria-selected={is('admin', 'ops')} className={is('admin', 'ops') ? 'active' : ''} href="/app/admin?tab=ops">Operations</a>
      <a role="tab" aria-selected={routeKind === 'admin-dashboard'} className={routeKind === 'admin-dashboard' ? 'active' : ''} href="/app/admin/dashboard">Dashboard</a>
      <a role="tab" aria-selected={routeKind === 'admin-trending'} className={routeKind === 'admin-trending' ? 'active' : ''} href="/app/admin/trending">Trending</a>
      <a role="tab" aria-selected={routeKind === 'admin-iptv'} className={routeKind === 'admin-iptv' ? 'active' : ''} href="/app/admin/iptv">IPTV</a>
    </nav>
  );
}
import type { AdminItem, AdminItemEditPayload, AdminResponse, AdminSeriesOption, AdminStatusResponse, AiSuggestResponse, TmdbPreviewResult, User } from '../types';
import { ErrorPanel, LoadingRows } from './common';
import { tmdbImageUrl } from '../utils/tmdb';

const AI_MODELS_TTL = 5 * 60 * 1000;
let _cachedAiModels: { models: Array<{ id: string; name: string }>; ts: number } | null = null;

function getCachedAiModels(): Array<{ id: string; name: string }> | null {
  if (_cachedAiModels && Date.now() - _cachedAiModels.ts < AI_MODELS_TTL) return _cachedAiModels.models;
  return null;
}

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

function resolveSeriesKey(value: string, series: AdminSeriesOption[]): string {
  const input = value.trim();
  if (!input) return '';
  const match = series.find((item) => (
    item.key === input ||
    item.title.toLowerCase() === input.toLowerCase()
  ));
  return match?.key || input;
}

export function AdminGate({
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
  return (
    <section className="admin-hero">
      <div className="admin-hero-top">
        <div className="admin-hero-copy">
          <h1>Admin console</h1>
          <p>
            {data.catalogueSize.toLocaleString()} items total.
            {' '}
            {data.filteredCount.toLocaleString()} match the current view.
          </p>
        </div>
      </div>
      <div className="admin-metrics" aria-label="Catalogue health">
        <span>
          <FilmIcon />
          <small>Movies</small>
          <strong>{data.stats.kinds.movies.toLocaleString()}</strong>
          <em>{data.stats.missing_poster.toLocaleString()} missing posters</em>
        </span>
        <span>
          <PlayIcon />
          <small>Episodes</small>
          <strong>{data.stats.kinds.series_episodes.toLocaleString()}</strong>
          <em>{data.stats.missing_thumb.toLocaleString()} missing thumbs</em>
        </span>
        <span>
          <MusicIcon />
          <small>Tracks</small>
          <strong>{data.stats.audio_count.toLocaleString()}</strong>
          <em>{data.stats.album_count.toLocaleString()} albums</em>
        </span>
        <span className={issueCount ? 'warn' : ''}>
          <FilterIcon />
          <small>Cleanup</small>
          <strong>{issueCount.toLocaleString()}</strong>
          <em>{issueCount ? 'needs review' : 'no open issues'}</em>
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
  onMergeSeries,
}: {
  busy: string;
  onRun: (action: string, confirmMessage?: string) => void;
  onMergeSeries: (sourceKey: string, targetKey: string) => Promise<void>;
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
    <section className="admin-panel admin-maintenance-panel">
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
      <MergeSeriesTool busy={busy} onMergeSeries={onMergeSeries} />
    </section>
  );
}

function MergeSeriesTool({
  busy,
  onMergeSeries,
}: {
  busy: string;
  onMergeSeries: (sourceKey: string, targetKey: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [series, setSeries] = useState<AdminSeriesOption[]>([]);
  const [source, setSource] = useState('');
  const [target, setTarget] = useState('');
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open || loaded) return undefined;
    const controller = new AbortController();
    setLoading(true);
    fetchAdminSeriesList(controller.signal)
      .then((items) => {
        if (!controller.signal.aborted) {
          setSeries(items);
          setLoaded(true);
        }
      })
      .catch((err: Error) => {
        if (!controller.signal.aborted) {
          setError(err.message || 'Unable to load series');
          setLoaded(true);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [loaded, open]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const sourceKey = resolveSeriesKey(source, series);
    const targetKey = resolveSeriesKey(target, series);
    setError('');
    if (!sourceKey || !targetKey) {
      setError('Choose both source and target series.');
      return;
    }
    if (sourceKey === targetKey) {
      setError('Source and target must be different.');
      return;
    }
    if (!window.confirm(`Merge "${source}" into "${target}"?`)) return;
    try {
      await onMergeSeries(sourceKey, targetKey);
      setSource('');
      setTarget('');
      setSeries([]);
      setLoaded(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Merge failed');
    }
  };

  return (
    <div className="admin-merge-tool">
      <button
        type="button"
        className="secondary-action compact-action"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
      >
        Merge series
      </button>
      {open && (
        <form className="admin-merge-form" onSubmit={submit}>
          <datalist id="admin-merge-series-list">
            {series.map((item) => (
              <option key={item.key} value={item.title}>{`${item.key} - ${item.count}`}</option>
            ))}
          </datalist>
          <label>
            <span>Source</span>
            <input
              value={source}
              onChange={(event) => setSource(event.currentTarget.value)}
              list="admin-merge-series-list"
              placeholder={loading ? 'Loading series...' : 'series to merge'}
            />
          </label>
          <label>
            <span>Target</span>
            <input
              value={target}
              onChange={(event) => setTarget(event.currentTarget.value)}
              list="admin-merge-series-list"
              placeholder="series to keep"
            />
          </label>
          <button type="submit" disabled={Boolean(busy) || loading}>
            {busy === 'merge-series' ? 'Merging...' : 'Merge'}
          </button>
          {error && <p className="admin-merge-error">{error}</p>}
        </form>
      )}
    </div>
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

  if (!count) return null;

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
  onEdit,
}: {
  item: AdminItem;
  selected: boolean;
  onSelect: (checked: boolean) => void;
  onToggleHidden: () => void;
  onEdit: () => void;
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
      <div className="admin-row-title">
        <a className="admin-row-art" href={item.watchHref}>
          {item.posterUrl ? <img src={item.posterUrl} alt="" loading="lazy" decoding="async" /> : <FilmIcon />}
        </a>
        <div className="admin-row-copy">
          <a href={item.watchHref}>
            <strong>{item.title}</strong>
          </a>
          <span>{itemSubtitle(item)}</span>
          <small>{item.fileName || `bin:${item.messageId}`}</small>
        </div>
      </div>
      <div className="admin-chip-row admin-row-issues">
        {item.year && <i>{item.year}</i>}
        {item.quality && <i>{item.quality}</i>}
        {item.mediaKind === 'audio' && <i>Music</i>}
        {item.hidden && <i>Hidden</i>}
        {item.duplicate && <i className="warn">Duplicate</i>}
        {item.missingPoster && <i className="warn">No poster</i>}
        {item.missingThumb && <i className="warn">No thumb</i>}
        {!item.hidden && !item.duplicate && !item.missingPoster && !item.missingThumb && <i className="ok">No issues</i>}
      </div>
      <span className={item.tmdbId ? 'admin-row-tmdb matched' : 'admin-row-tmdb missing'}>{item.tmdbId ? `TMDB ${item.tmdbId}` : 'No TMDB'}</span>
      <div className="admin-row-actions">
        <button type="button" onClick={onToggleHidden}>{item.hidden ? 'Unhide' : 'Hide'}</button>
        <button type="button" onClick={onEdit}>Edit</button>
      </div>
    </article>
  );
}

// ── Edit Modal ────────────────────────────────────────────────────────────────

function FieldLabel({ name, locked, onUnlock }: { name: string; locked: boolean; onUnlock: () => void }) {
  return (
    <span className="edit-field-label">
      {name}
      {locked && (
        <span className="edit-lock-badge">
          🔒 locked
          <button type="button" onClick={onUnlock} title="Unlock">✕</button>
        </span>
      )}
    </span>
  );
}

function EditModal({
  messageId,
  hasGemini,
  onClose,
  onSaved,
}: {
  messageId: number;
  hasGemini: boolean;
  onClose: () => void;
  onSaved: (updated: AdminItem) => void;
}) {
  type FormState = AdminItemEditPayload & {
    imdbInput: string;
    thumbUrlInput: string;
    aiModel: string;
  };

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [activeSection, setActiveSection] = useState<'identity' | 'metadata' | 'advanced'>('identity');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiFieldLoading, setAiFieldLoading] = useState('');
  const [aiReasoning, setAiReasoning] = useState('');
  const [aiError, setAiError] = useState('');
  const [aiModels, setAiModels] = useState<Array<{ id: string; name: string }>>(getCachedAiModels() || []);
  const [tmdbPreview, setTmdbPreview] = useState<TmdbPreviewResult | null>(null);
  const [tmdbPreviewLoading, setTmdbPreviewLoading] = useState(false);
  const [imdbLoading, setImdbLoading] = useState(false);
  const [imdbError, setImdbError] = useState('');
  const tmdbDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [isAudio, setIsAudio] = useState(false);

  const [form, setForm] = useState<FormState>({
    title: '', year: null, tags: '', description: '', fileName: '',
    seriesTitle: '', season: null, episode: null, episodeEnd: null,
    introStart: null, introEnd: null, recapStart: null, recapEnd: null, chapters: '',
    artist: '', albumTitle: '', trackNumber: null,
    thumbUrl: '', thumbUrlInput: '', tmdbId: null, tmdbKind: 'movie',
    adminLocked: [], imdbInput: '', aiModel: 'gemini-2.5-flash-lite',
  });

  useEffect(() => {
    const controller = new AbortController();
    fetchAdminItem(messageId, controller.signal)
      .then((data) => {
        const d = data as Record<string, unknown>;
        setIsAudio(d['mediaKind'] === 'audio');
        setForm((prev) => ({
          ...prev,
          title:        String(d['title'] || ''),
          year:         (d['year'] as number | null) ?? null,
          tags:         Array.isArray(d['tags']) ? (d['tags'] as string[]).join(' ') : '',
          description:  String(d['description'] || ''),
          fileName:     String(d['fileName'] || ''),
          seriesTitle:  String(d['seriesTitle'] || ''),
          season:       (d['season'] as number | null) ?? null,
          episode:      (d['episode'] as number | null) ?? null,
          episodeEnd:   (d['episodeEnd'] as number | null) ?? null,
          introStart:   (d['introStart'] as number | null) ?? null,
          introEnd:     (d['introEnd'] as number | null) ?? null,
          recapStart:   (d['recapStart'] as number | null) ?? null,
          recapEnd:     (d['recapEnd'] as number | null) ?? null,
          chapters:     String(d['chapters'] || ''),
          artist:       String(d['artist'] || ''),
          albumTitle:   String(d['albumTitle'] || ''),
          trackNumber:  (d['trackNumber'] as number | null) ?? null,
          tmdbId:       (d['tmdbId'] as number | null) ?? null,
          tmdbKind:     (d['tmdbKind'] as 'movie' | 'tv') || 'movie',
          adminLocked:  Array.isArray(d['adminLocked']) ? d['adminLocked'] as string[] : [],
        }));
        setLoading(false);
      })
      .catch((err: Error) => {
        if (!controller.signal.aborted) {
          setError(err.message);
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [messageId]);

  useEffect(() => {
    if (!hasGemini || getCachedAiModels()) return;
    fetchAiModels()
      .then((models) => { _cachedAiModels = { models, ts: Date.now() }; setAiModels(models); })
      .catch(() => undefined);
  }, [hasGemini]);

  // Cancel pending TMDB preview debounce on unmount.
  useEffect(() => () => {
    if (tmdbDebounceRef.current) clearTimeout(tmdbDebounceRef.current);
  }, []);

  const setField = useCallback(<K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  }, []);

  const unlockField = (field: string) => {
    setForm((prev) => ({ ...prev, adminLocked: prev.adminLocked.filter((f) => f !== field) }));
  };

  // Always holds the latest tmdbKind so the debounced preview reads current, not stale, kind.
  const tmdbKindRef = useRef(form.tmdbKind);
  useEffect(() => { tmdbKindRef.current = form.tmdbKind; }, [form.tmdbKind]);

  const handleTmdbIdChange = (value: string) => {
    const num = value ? parseInt(value, 10) : null;
    setField('tmdbId', isNaN(num as number) ? null : num);
    if (tmdbDebounceRef.current) clearTimeout(tmdbDebounceRef.current);
    if (num && num > 0) {
      tmdbDebounceRef.current = setTimeout(() => {
        setTmdbPreviewLoading(true);
        fetchTmdbPreview(num, tmdbKindRef.current)
          .then(setTmdbPreview)
          .catch(() => setTmdbPreview(null))
          .finally(() => setTmdbPreviewLoading(false));
      }, 500);
    } else {
      setTmdbPreview(null);
    }
  };

  const handleTmdbKindChange = (kind: 'movie' | 'tv') => {
    setField('tmdbKind', kind);
    if (form.tmdbId) {
      setTmdbPreviewLoading(true);
      fetchTmdbPreview(form.tmdbId, kind)
        .then(setTmdbPreview)
        .catch(() => setTmdbPreview(null))
        .finally(() => setTmdbPreviewLoading(false));
    }
  };

  const handleResolveImdb = async () => {
    if (!form.imdbInput.trim()) return;
    setImdbLoading(true);
    setImdbError('');
    try {
      const res = await resolveTmdbImdb(form.imdbInput.trim());
      if (res.error) { setImdbError(res.error); return; }
      setForm((prev) => ({ ...prev, tmdbId: res.tmdb_id, tmdbKind: res.kind as 'movie' | 'tv' }));
      setTmdbPreviewLoading(true);
      fetchTmdbPreview(res.tmdb_id, res.kind)
        .then(setTmdbPreview)
        .catch(() => setTmdbPreview(null))
        .finally(() => setTmdbPreviewLoading(false));
    } catch (err) {
      setImdbError(err instanceof Error ? err.message : 'Resolve failed');
    } finally {
      setImdbLoading(false);
    }
  };

  const applyAiSuggestion = useCallback((res: AiSuggestResponse) => {
    if (res.reasoning) setAiReasoning(res.reasoning);
    setForm((prev) => ({
      ...prev,
      ...(res.title       && { title: res.title }),
      ...(res.year        && { year: res.year }),
      ...(res.file_name   && { fileName: res.file_name }),
      ...(res.series_title && { seriesTitle: res.series_title }),
      ...(res.season      && { season: res.season }),
      ...(res.episode     && { episode: res.episode }),
      ...(res.tags        && { tags: res.tags }),
      ...(res.description && { description: res.description }),
      ...(res.artist      && { artist: res.artist }),
      ...(res.album_title && { albumTitle: res.album_title }),
      ...(res.track_number && { trackNumber: res.track_number }),
    }));
  }, []);

  const handleAiSuggest = async () => {
    setAiLoading(true);
    setAiError('');
    setAiReasoning('');
    try {
      const res: AiSuggestResponse = await aiSuggestItem(messageId, form.aiModel);
      if (res.error) { setAiError(res.error); return; }
      applyAiSuggestion(res);
    } catch (err) {
      setAiError(err instanceof Error ? err.message : 'AI suggest failed');
    } finally {
      setAiLoading(false);
    }
  };

  const handleAiFieldSuggest = async (field: string) => {
    setAiFieldLoading(field);
    setAiError('');
    try {
      const res: AiSuggestResponse = await aiSuggestItem(messageId, form.aiModel, field);
      if (res.error) { setAiError(res.error); return; }
      applyAiSuggestion(res);
    } catch (err) {
      setAiError(err instanceof Error ? err.message : 'AI suggest failed');
    } finally {
      setAiFieldLoading('');
    }
  };

  const aiFieldButton = (field: string, label: string) => hasGemini ? (
    <button
      type="button"
      className="edit-ai-field-btn"
      onClick={() => void handleAiFieldSuggest(field)}
      disabled={Boolean(aiFieldLoading) || aiLoading}
    >
      {aiFieldLoading === field ? '...' : label}
    </button>
  ) : null;

  const handleSave = async () => {
    if (!form.title.trim()) { setError('Title is required'); return; }
    setSaving(true);
    setError('');
    try {
      const payload: AdminItemEditPayload = {
        title: form.title, year: form.year, tags: form.tags, description: form.description,
        fileName: form.fileName, seriesTitle: form.seriesTitle,
        season: form.season, episode: form.episode, episodeEnd: form.episodeEnd,
        introStart: form.introStart, introEnd: form.introEnd,
        recapStart: form.recapStart, recapEnd: form.recapEnd, chapters: form.chapters,
        artist: form.artist, albumTitle: form.albumTitle, trackNumber: form.trackNumber,
        thumbUrl: form.thumbUrlInput,
        tmdbId: form.tmdbId, tmdbKind: form.tmdbKind, adminLocked: form.adminLocked,
      };
      const res = await saveAdminItem(messageId, payload);
      if (res.item) onSaved(res.item as AdminItem);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="modal-layer" role="dialog" aria-modal="true" aria-label="Edit item">
      <button className="modal-scrim" type="button" onClick={onClose} aria-label="Close" />
      <div className="edit-modal-panel">
        <div className="edit-modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <h2 style={{ flex: 1 }}>Edit bin:{messageId}</h2>
            {hasGemini && (
              <div className="edit-ai-row" style={{ margin: 0 }}>
                <select
                  className="edit-field-input"
                  value={form.aiModel}
                  onChange={(e) => setField('aiModel', e.currentTarget.value)}
                  disabled={!aiModels.length}
                >
                  {aiModels.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                  {!aiModels.length && <option value="gemini-2.5-flash-lite">gemini-2.5-flash-lite</option>}
                </select>
                <button type="button" className="edit-ai-btn" onClick={handleAiSuggest} disabled={aiLoading}>
                  {aiLoading ? '⏳ Searching…' : '✨ Suggest'}
                </button>
              </div>
            )}
            <button className="icon-button modal-close" type="button" onClick={onClose} aria-label="Close"><XIcon /></button>
          </div>
        </div>
        <div className="edit-modal-tabs">
          {(['identity', 'metadata', 'advanced'] as const).map((s) => (
            <button
              key={s}
              type="button"
              className={activeSection === s ? 'edit-modal-tab-btn active' : 'edit-modal-tab-btn'}
              onClick={() => setActiveSection(s)}
            >
              {s === 'identity' ? 'Identity' : s === 'metadata' ? (isAudio ? 'Music' : 'Metadata') : 'Advanced'}
            </button>
          ))}
        </div>

        <div className="edit-modal-body">
          {loading && <LoadingRows />}
          {!loading && (
            <>
              {(error || aiError) && <p className="edit-error">{error || aiError}</p>}
              {aiReasoning && <p className="edit-ai-reasoning"><strong>AI:</strong> {aiReasoning}</p>}

              {/* ── Identity tab ── */}
              {activeSection === 'identity' && (
                <>
                  <label className="edit-field">
                    <FieldLabel name="Title" locked={form.adminLocked.includes('title')} onUnlock={() => unlockField('title')} />
                    <div className="edit-field-row">
                      <input className="edit-field-input" value={form.title} onChange={(e) => setField('title', e.currentTarget.value)} required />
                      {aiFieldButton('title', 'Title')}
                    </div>
                  </label>
                  <label className="edit-field edit-field-narrow">
                    <FieldLabel name="Year" locked={form.adminLocked.includes('year')} onUnlock={() => unlockField('year')} />
                    <input className="edit-field-input" type="number" min="1900" max="2099" value={form.year ?? ''} onChange={(e) => setField('year', e.currentTarget.value ? parseInt(e.currentTarget.value) : null)} />
                  </label>
                  <label className="edit-field">
                    <span className="edit-field-label">Tags</span>
                    <div className="edit-field-row">
                      <input className="edit-field-input" value={form.tags} onChange={(e) => setField('tags', e.currentTarget.value)} placeholder="space-separated, no # prefix" />
                      {aiFieldButton('tags', 'Tags')}
                    </div>
                  </label>
                  <label className="edit-field">
                    <span className="edit-field-label">Description</span>
                    <div className="edit-field-row edit-field-row-top">
                      <textarea className="edit-field-input" rows={3} value={form.description} onChange={(e) => setField('description', e.currentTarget.value)} />
                      {aiFieldButton('description', 'Write')}
                    </div>
                  </label>
                  <label className="edit-field">
                    <span className="edit-field-label">Display name <span className="edit-field-hint">(filename override)</span></span>
                    <div className="edit-field-row">
                      <input className="edit-field-input" value={form.fileName} onChange={(e) => setField('fileName', e.currentTarget.value)} />
                      {aiFieldButton('file_name', 'Clean')}
                    </div>
                  </label>
                </>
              )}

              {/* ── Metadata tab ── */}
              {activeSection === 'metadata' && (
                <>
                  {isAudio ? (
                    <div className="edit-section">
                      <p className="edit-section-label">Music</p>
                      <label className="edit-field">
                        <span className="edit-field-label">Artist</span>
                        <div className="edit-field-row">
                          <input className="edit-field-input" value={form.artist} onChange={(e) => setField('artist', e.currentTarget.value)} />
                          {aiFieldButton('artist', 'Artist')}
                        </div>
                      </label>
                      <label className="edit-field">
                        <span className="edit-field-label">Album</span>
                        <div className="edit-field-row">
                          <input className="edit-field-input" value={form.albumTitle} onChange={(e) => setField('albumTitle', e.currentTarget.value)} />
                          {aiFieldButton('album_title', 'Album')}
                        </div>
                      </label>
                      <label className="edit-field edit-field-narrow">
                        <span className="edit-field-label">Track #</span>
                        <div className="edit-field-row">
                          <input className="edit-field-input" type="number" min="1" value={form.trackNumber ?? ''} onChange={(e) => setField('trackNumber', e.currentTarget.value ? parseInt(e.currentTarget.value) : null)} />
                          {aiFieldButton('track_number', 'Track')}
                        </div>
                      </label>
                    </div>
                  ) : (
                    <div className="edit-section">
                      <p className="edit-section-label">Series <span className="edit-field-hint">(groups into a /series/ page)</span></p>
                      <label className="edit-field">
                        <FieldLabel name="Series title" locked={form.adminLocked.includes('series_title')} onUnlock={() => unlockField('series_title')} />
                        <input className="edit-field-input" value={form.seriesTitle} onChange={(e) => setField('seriesTitle', e.currentTarget.value)} />
                      </label>
                      <div className="edit-field-row">
                        <label className="edit-field">
                          <span className="edit-field-label">Season</span>
                          <input className="edit-field-input" type="number" min="1" value={form.season ?? ''} onChange={(e) => setField('season', e.currentTarget.value ? parseInt(e.currentTarget.value) : null)} />
                        </label>
                        <label className="edit-field">
                          <span className="edit-field-label">Ep start</span>
                          <input className="edit-field-input" type="number" min="0" value={form.episode ?? ''} onChange={(e) => setField('episode', e.currentTarget.value ? parseInt(e.currentTarget.value) : null)} />
                        </label>
                        <label className="edit-field">
                          <span className="edit-field-label">Ep end</span>
                          <input className="edit-field-input" type="number" min="2" value={form.episodeEnd ?? ''} onChange={(e) => setField('episodeEnd', e.currentTarget.value ? parseInt(e.currentTarget.value) : null)} />
                        </label>
                      </div>
                    </div>
                  )}
                  <div className="edit-section">
                    <p className="edit-section-label">TMDB</p>
                    <label className="edit-field">
                      <span className="edit-field-label">IMDb id or URL</span>
                      <div className="edit-field-row">
                        <input className="edit-field-input" style={{ flex: 1 }} value={form.imdbInput} onChange={(e) => setField('imdbInput', e.currentTarget.value)} placeholder="tt1234567 or https://imdb.com/title/tt1234567/" onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void handleResolveImdb(); } }} />
                        <button type="button" className="primary-action compact-action" onClick={() => void handleResolveImdb()} disabled={imdbLoading}>
                          {imdbLoading ? 'Resolving…' : 'Resolve →'}
                        </button>
                      </div>
                      {imdbError && <p className="edit-error" style={{ marginTop: '0.25rem' }}>{imdbError}</p>}
                    </label>
                    <div className="edit-field-row" style={{ marginTop: '0.5rem' }}>
                      <label className="edit-field" style={{ flex: 1 }}>
                        <span className="edit-field-label">TMDB id</span>
                        <input className="edit-field-input" type="number" min="1" value={form.tmdbId ?? ''} onChange={(e) => handleTmdbIdChange(e.currentTarget.value)} placeholder="e.g. 27205" />
                      </label>
                      <label className="edit-field">
                        <span className="edit-field-label">Kind</span>
                        <select className="edit-field-input" value={form.tmdbKind} onChange={(e) => handleTmdbKindChange(e.currentTarget.value as 'movie' | 'tv')}>
                          <option value="movie">Movie</option>
                          <option value="tv">TV</option>
                        </select>
                      </label>
                    </div>
                    {tmdbPreviewLoading && <p className="edit-field-hint" style={{ marginTop: '0.5rem' }}>Fetching from TMDB…</p>}
                    {tmdbPreview && !tmdbPreviewLoading && (
                      <div className="edit-tmdb-preview">
                        {tmdbPreview.poster_path && <img src={tmdbImageUrl(tmdbPreview.poster_path, 'w92')} alt="" />}
                        <div>
                          <p><strong>{tmdbPreview.title}</strong>{tmdbPreview.year ? ` (${tmdbPreview.year})` : ''}</p>
                          <p className="edit-field-hint">{tmdbPreview.kind}{tmdbPreview.imdb_id ? ` · ${tmdbPreview.imdb_id}` : ''}</p>
                          <p className="edit-field-hint" style={{ WebkitLineClamp: 3, display: '-webkit-box', WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{tmdbPreview.overview}</p>
                        </div>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* ── Advanced tab ── */}
              {activeSection === 'advanced' && (
                <>
                  <div className="edit-section">
                    <p className="edit-section-label">Thumbnail <span className="edit-field-hint">(paste URL to override)</span></p>
                    <div className="edit-field-row">
                      <input className="edit-field-input" style={{ flex: 1 }} value={form.thumbUrlInput} onChange={(e) => setField('thumbUrlInput', e.currentTarget.value)} placeholder="https://image.tmdb.org/t/p/w500/… or any .jpg URL" />
                      <button type="button" className="secondary-action compact-action" onClick={() => setField('thumbUrlInput', '__clear__')}>Clear</button>
                    </div>
                  </div>
                  {!isAudio && (
                    <>
                      <div className="edit-section">
                        <p className="edit-section-label">Playback markers</p>
                        <div className="edit-field-row">
                          <label className="edit-field">
                            <span className="edit-field-label">Intro start</span>
                            <input className="edit-field-input" type="number" min="0" step="0.5" value={form.introStart ?? ''} onChange={(e) => setField('introStart', e.currentTarget.value ? parseFloat(e.currentTarget.value) : null)} />
                          </label>
                          <label className="edit-field">
                            <span className="edit-field-label">Intro end</span>
                            <input className="edit-field-input" type="number" min="0" step="0.5" value={form.introEnd ?? ''} onChange={(e) => setField('introEnd', e.currentTarget.value ? parseFloat(e.currentTarget.value) : null)} />
                          </label>
                        </div>
                        <div className="edit-field-row">
                          <label className="edit-field">
                            <span className="edit-field-label">Recap start</span>
                            <input className="edit-field-input" type="number" min="0" step="0.5" value={form.recapStart ?? ''} onChange={(e) => setField('recapStart', e.currentTarget.value ? parseFloat(e.currentTarget.value) : null)} />
                          </label>
                          <label className="edit-field">
                            <span className="edit-field-label">Recap end</span>
                            <input className="edit-field-input" type="number" min="0" step="0.5" value={form.recapEnd ?? ''} onChange={(e) => setField('recapEnd', e.currentTarget.value ? parseFloat(e.currentTarget.value) : null)} />
                          </label>
                        </div>
                      </div>
                      <div className="edit-section">
                        <p className="edit-section-label">Chapters <span className="edit-field-hint">one per line: 75 Opening</span></p>
                        <textarea
                          className="edit-field-input"
                          rows={5}
                          value={form.chapters}
                          onChange={(e) => setField('chapters', e.currentTarget.value)}
                          placeholder={'0 Opening\n75 First turn\n180 Final scene'}
                        />
                      </div>
                    </>
                  )}
                </>
              )}
            </>
          )}
        </div>

        <div className="edit-modal-footer">
          <button type="button" className="secondary-action" style={{ marginRight: 'auto', color: 'var(--error, #f87171)', borderColor: 'var(--error, #f87171)' }} onClick={async () => { if (!confirm('Clear all TMDB enrichment for this item?')) return; try { const res = await clearAdminItemTmdb(messageId); if (res.item) onSaved(res.item as AdminItem); onClose(); } catch (_) { /* ignore */ } }}>
            Clear TMDB
          </button>
          <button type="button" className="secondary-action" onClick={onClose}>Cancel</button>
          <button type="button" className="primary-action" onClick={() => void handleSave()} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

function AdminList({
  data,
  selected,
  setSelected,
  onToggleHidden,
  onEdit,
  updateParam,
}: {
  data: AdminResponse;
  selected: Set<number>;
  setSelected: (next: Set<number>) => void;
  onToggleHidden: (item: AdminItem) => void;
  onEdit: (item: AdminItem) => void;
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
      <div className="admin-table-head" aria-hidden="true">
        <span />
        <span />
        <span>Title</span>
        <span>Details</span>
        <span>Actions</span>
      </div>
      <div className="admin-list">
        {data.items.length ? (
          data.items.map((item) => (
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
              onEdit={() => onEdit(item)}
            />
          ))
        ) : (
          <div className="admin-empty-list">
            <strong>No matching items</strong>
            <span>Adjust the search or filters to widen the catalogue view.</span>
          </div>
        )}
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
  const [editingId, setEditingId] = useState<number | null>(null);
  const tab = new URLSearchParams(locationSearch).get('tab');
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

  const runMergeSeriesAction = async (sourceKey: string, targetKey: string) => {
    setBusy('merge-series');
    setNotice('');
    try {
      const response = await mergeAdminSeries(sourceKey, targetKey);
      setNotice(`Merged ${response.merged} episode${response.merged === 1 ? '' : 's'} into ${response.target_title}`);
      reload();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Merge failed');
      throw err;
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
          {tab === 'ops' ? (
            <section className="admin-workspace" aria-label="Operations">
              <AdminStatusPanel status={data.status} />
              <MaintenancePanel busy={busy} onRun={runMaintenanceAction} onMergeSeries={runMergeSeriesAction} />
            </section>
          ) : (
            <section className="admin-workspace" aria-label="Catalogue worklist">
              {pipelineRunning && (
                <div className="admin-pipeline-banner">
                  <span className="pipeline-dot" />
                  Pipeline running —{' '}
                  <a href="/app/admin?tab=ops">view status</a>
                </div>
              )}
              <AdminControls
                data={data}
                query={query}
                setQuery={setQuery}
                onSubmit={submitSearch}
                updateParam={updateParam}
              />
              <AdminList
                data={data}
                selected={selected}
                setSelected={setSelected}
                onToggleHidden={runSingleHiddenAction}
                onEdit={(item) => setEditingId(item.messageId)}
                updateParam={updateParam}
              />
              <BulkBar
                selected={selected}
                data={data}
                busy={busy}
                setSelected={setSelected}
                onAction={runSelectedAction}
              />
            </section>
          )}
        </>
      )}
      {editingId !== null && (
        <EditModal
          messageId={editingId}
          hasGemini={Boolean(data?.capabilities?.gemini)}
          onClose={() => setEditingId(null)}
          onSaved={(updated) => {
            updateData((current) => current ? {
              ...current,
              items: current.items.map((it) => it.messageId === updated.messageId ? updated : it),
            } : current);
            setEditingId(null);
          }}
        />
      )}
    </main>
  );
}
