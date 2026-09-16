import { FilterIcon, XIcon } from '../icons';
import { appUrl } from '../navigation';
import type { FilterOption, HubFilters, HubParams, ViewValue } from '../types';
import { Button } from './ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';

type FilterControl = {
  id: string;
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (value: string) => void;
};

function optionLabel(options: FilterOption[], value: string) {
  return options.find((option) => option.value === value)?.label || '';
}

function SelectControl({ control, className = '', compact = false }: { control: FilterControl; className?: string; compact?: boolean }) {
  const options = (
    <SelectContent>
      {control.options.map((option) => (
        <SelectItem key={option.value || 'any'} value={option.value || '__any'}>{option.label}</SelectItem>
      ))}
    </SelectContent>
  );
  const onValueChange = (value: string) => control.onChange(value === '__any' ? '' : value);

  // Compact pill (browse bar): reads "Field · Value" while a value is active so
  // every orange pill stays self-describing. Metadata pills also get an × that
  // clears just this filter without opening the menu; sort is a chooser, so it
  // never gets one.
  if (compact) {
    const active = Boolean(control.value);
    const clearable = active && control.id !== 'sort';
    // A stale/foreign value (e.g. a URL hand-edit) has no matching option;
    // fall back to the raw value instead of rendering a blank pill.
    const valueLabel = control.value ? (optionLabel(control.options, control.value) || control.value) : '';
    return (
      <Select value={control.value || undefined} onValueChange={onValueChange}>
        <span className="filter-pill-group">
          <SelectTrigger
            className={['filter-pill', active ? 'active' : '', clearable ? 'clearable' : '', className].filter(Boolean).join(' ')}
            aria-label={control.label}
          >
            {active ? (
              <span className="filter-pill-value">
                <span className="filter-pill-field">{control.label}</span>
                <span className="filter-pill-sep">·</span>
                <span className="filter-pill-text">{valueLabel}</span>
              </span>
            ) : (
              <SelectValue placeholder={control.label} />
            )}
          </SelectTrigger>
          {clearable && (
            <button
              type="button"
              className="filter-pill-clear"
              aria-label={`Clear ${control.label} filter`}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                control.onChange('');
              }}
            >
              <XIcon />
            </button>
          )}
        </span>
        {options}
      </Select>
    );
  }

  // Full layout (Filters page): labelled row with room to breathe.
  return (
    <label className={['filter-select-control', className].filter(Boolean).join(' ')}>
      <span>{control.label}</span>
      <Select value={control.value || undefined} onValueChange={onValueChange}>
        <SelectTrigger className="filter-select-trigger" aria-label={control.label}>
          <SelectValue placeholder="Any" />
        </SelectTrigger>
        {options}
      </Select>
    </label>
  );
}

function filterOptions(filters: HubFilters, params: HubParams, update: (patch: Partial<HubParams>, replace?: boolean) => void) {
  const viewOptions = filters.views.length ? filters.views : [
    { value: '', label: 'All' },
    { value: 'movies', label: 'Movies' },
    { value: 'series', label: 'Series' },
    { value: 'music', label: 'Music' },
  ];
  const yearOptions = [
    { value: '', label: 'Any year' },
    ...filters.years.map((year) => ({ value: String(year), label: String(year) })),
  ];
  const qualityOptions = [
    { value: '', label: 'Any quality' },
    ...filters.qualities.map((quality) => ({ value: quality, label: quality })),
  ];
  const genreOptions = [
    { value: '', label: 'Any genre' },
    ...filters.genres.map((genre) => ({ value: genre, label: genre })),
  ];
  const tagOptions = [
    { value: '', label: 'Any tag' },
    ...filters.tags.map((tag) => ({ value: tag.name, label: tag.name })),
  ];
  const sortOptions = filters.sortOptions.length ? filters.sortOptions : [{ value: 'newest', label: 'Newest' }];
  const metadataControls: FilterControl[] = [
    {
      id: 'year',
      label: 'Year',
      value: params.year ? String(params.year) : '',
      options: yearOptions,
      onChange: (value) => update({ year: value ? Number(value) : null, offset: 0 }),
    },
    {
      id: 'quality',
      label: 'Quality',
      value: params.quality,
      options: qualityOptions,
      onChange: (value) => update({ quality: value, offset: 0 }),
    },
    {
      id: 'genre',
      label: 'Genre',
      value: params.genre,
      options: genreOptions,
      onChange: (value) => update({ genre: value, offset: 0 }),
    },
    {
      id: 'tag',
      label: 'Tag',
      value: params.tag,
      options: tagOptions,
      onChange: (value) => update({ tag: value, offset: 0 }),
    },
  ];
  const sortControl: FilterControl = {
    id: 'sort',
    label: 'Sort',
    value: params.sort,
    options: sortOptions,
    onChange: (value) => update({ sort: value, offset: 0 }),
  };

  return { viewOptions, yearOptions, qualityOptions, genreOptions, tagOptions, metadataControls, sortControl };
}

type AppliedFilter = { key: string; field: string; value: string; clear: Partial<HubParams> };

function appliedFilterChips({
  filters,
  params,
  query,
}: {
  filters: HubFilters;
  params: HubParams;
  query: string;
}): AppliedFilter[] {
  const qualityOptions = [
    { value: '', label: 'Any quality' },
    ...filters.qualities.map((quality) => ({ value: quality, label: quality })),
  ];
  const genreOptions = [
    { value: '', label: 'Any genre' },
    ...filters.genres.map((genre) => ({ value: genre, label: genre })),
  ];
  const tagOptions = [
    { value: '', label: 'Any tag' },
    ...filters.tags.map((tag) => ({ value: tag.name, label: tag.name })),
  ];
  const chips: AppliedFilter[] = [];
  if (query) chips.push({ key: 'q', field: 'Search', value: query, clear: { q: '' } });
  if (params.year) chips.push({ key: 'year', field: 'Year', value: String(params.year), clear: { year: null } });
  if (params.quality) chips.push({ key: 'quality', field: 'Quality', value: optionLabel(qualityOptions, params.quality) || params.quality, clear: { quality: '' } });
  if (params.genre) chips.push({ key: 'genre', field: 'Genre', value: optionLabel(genreOptions, params.genre) || params.genre, clear: { genre: '' } });
  if (params.tag) chips.push({ key: 'tag', field: 'Tag', value: optionLabel(tagOptions, params.tag) || params.tag, clear: { tag: '' } });
  return chips;
}

function clearParams(): Partial<HubParams> {
  return {
    q: '',
    tag: '',
    quality: '',
    genre: '',
    year: null,
    sort: 'newest',
    view: '',
    offset: 0,
  };
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
  const { viewOptions, metadataControls, sortControl } = filterOptions(filters, params, update);
  const activeFilterCount = appliedFilterChips({ filters, params, query }).length;
  const hasFilters = activeFilterCount > 0 || params.sort !== 'newest' || Boolean(params.view);
  const clearAll = (replace = false) => {
    setQuery('');
    update(clearParams(), replace);
  };

  return (
    <section className="filter-panel" aria-label="Browse filters">
      <div className="filter-count" aria-label="Catalogue size">
        <FilterIcon />
        <strong>{catalogueSize ? `${catalogueSize.toLocaleString()} titles` : 'Library'}</strong>
      </div>

      {/* Category chips — always visible, scroll on mobile */}
      <div className="filter-view-row" role="group" aria-label="Content type">
        {viewOptions.map((option) => (
          <Button
            key={option.value || 'all'}
            type="button"
            variant="ghost"
            size="sm"
            className={params.view === option.value ? 'filter-view-chip active' : 'filter-view-chip'}
            aria-pressed={params.view === option.value}
            onClick={() => update({ view: option.value as ViewValue, offset: 0 })}
          >
            {option.label}
          </Button>
        ))}
      </div>

      {/* Inline controls: Year / Quality / Genre / Tag — desktop only */}
      <div className="filter-inline-controls" aria-label="Advanced filters">
        {metadataControls.map((control) => (
          <SelectControl key={control.id} control={control} compact />
        ))}
      </div>

      {/* Right side: Sort + Reset (desktop) or Sort + Filters link (mobile) */}
      <div className="filter-action-row">
        <SelectControl control={sortControl} className="filter-sort-control" compact />
        {hasFilters && (
          <Button className="filter-clear-button" variant="outline" size="sm" type="button" onClick={() => clearAll()}>
            Reset
          </Button>
        )}
        <Button asChild variant="outline" size="sm" className="filter-drawer-button">
          <a href={appUrl({ ...params, offset: 0 }, '/filters')}>
            <FilterIcon />
            <span>Filters</span>
            {(activeFilterCount > 0 || params.sort !== 'newest') && (
              <small>{activeFilterCount + (params.sort !== 'newest' ? 1 : 0)}</small>
            )}
          </a>
        </Button>
      </div>
    </section>
  );
}

export function FilterPage({
  filters,
  catalogueSize,
  params,
  query,
  setQuery,
  navigate,
}: {
  filters: HubFilters;
  catalogueSize: number;
  params: HubParams;
  query: string;
  setQuery: (next: string) => void;
  navigate: (href: string, replace?: boolean) => void;
}) {
  const updateFilterRoute = (patch: Partial<HubParams>, replace = true) => {
    const next = { ...params, ...patch, offset: 0 };
    navigate(appUrl(next, '/filters'), replace);
  };
  const { viewOptions, metadataControls, sortControl } = filterOptions(filters, params, updateFilterRoute);
  const chips = appliedFilterChips({ filters, params, query });
  const hasFilters = chips.length > 0 || params.sort !== 'newest' || Boolean(params.view);
  const clearAll = () => {
    setQuery('');
    navigate(appUrl(clearParams(), '/filters'), true);
  };

  return (
    <main className="page-main filter-page">
      <div className="page-title filter-page-title">
        <div>
          <p className="eyebrow">Browse</p>
          <h1>Filters</h1>
        </div>
        <span>{catalogueSize ? `${catalogueSize.toLocaleString()} titles` : 'Library'}</span>
      </div>

      <section className="filter-page-panel" aria-label="Content type">
        <div className="filter-view-row">
          {viewOptions.map((option) => (
            <Button
              key={option.value || 'all'}
              type="button"
              variant="ghost"
              size="sm"
              className={params.view === option.value ? 'filter-view-chip active' : 'filter-view-chip'}
              aria-pressed={params.view === option.value}
              onClick={() => updateFilterRoute({ view: option.value as ViewValue, offset: 0 }, true)}
            >
              {option.label}
            </Button>
          ))}
        </div>

        <div className="filter-page-selects">
          <SelectControl control={sortControl} />
          {metadataControls.map((control) => (
            <SelectControl key={control.id} control={control} />
          ))}
        </div>

        {chips.length > 0 && (
          <div className="applied-filter-row" aria-label="Applied filters">
            {chips.map((chip) => (
              <span key={chip.key} className="applied-filter-chip">
                <span className="applied-filter-field">{chip.field}</span>
                <span className="applied-filter-value">{chip.value}</span>
                <button
                  type="button"
                  className="applied-filter-clear"
                  aria-label={`Remove ${chip.field} filter`}
                  onClick={() => updateFilterRoute({ ...chip.clear, offset: 0 }, true)}
                >
                  <XIcon />
                </button>
              </span>
            ))}
          </div>
        )}

        <div className="filter-page-actions">
          <Button className="filter-clear-button" variant="outline" type="button" onClick={clearAll} disabled={!hasFilters}>Reset</Button>
          <Button asChild><a href={appUrl({ ...params, offset: 0 })}>Show results</a></Button>
        </div>
      </section>
    </main>
  );
}
