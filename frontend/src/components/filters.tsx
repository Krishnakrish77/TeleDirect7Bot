import { FilterIcon } from '../icons';
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

  // Compact pill (browse bar): one control reading the field name until a value
  // is picked, then the value — no redundant "Year Any" prefix. Highlights when
  // a value is active.
  if (compact) {
    const active = Boolean(control.value);
    return (
      <Select value={control.value || undefined} onValueChange={onValueChange}>
        <SelectTrigger
          className={['filter-pill', active ? 'active' : '', className].filter(Boolean).join(' ')}
          aria-label={control.label}
        >
          <SelectValue placeholder={control.label} />
        </SelectTrigger>
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

function activeFilterLabels({
  filters,
  params,
  query,
}: {
  filters: HubFilters;
  params: HubParams;
  query: string;
}) {
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
  return [
    query ? `Search: ${query}` : '',
    params.year ? String(params.year) : '',
    params.quality ? optionLabel(qualityOptions, params.quality) : '',
    params.genre ? optionLabel(genreOptions, params.genre) : '',
    params.tag ? optionLabel(tagOptions, params.tag) : '',
  ].filter(Boolean);
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
  const activeLabels = activeFilterLabels({ filters, params, query });
  const activeFilterCount = activeLabels.length;
  const hasFilters = activeFilterCount > 0 || params.sort !== 'newest' || Boolean(params.view);
  const summary = activeFilterCount
    ? `${activeLabels.slice(0, 2).join(' / ')}${activeFilterCount > 2 ? ` +${activeFilterCount - 2}` : ''}`
    : 'Any year, quality, genre';
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
  const activeLabels = activeFilterLabels({ filters, params, query });
  const hasFilters = activeLabels.length > 0 || params.sort !== 'newest' || Boolean(params.view);
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

        {activeLabels.length > 0 && (
          <div className="applied-filter-row" aria-label="Applied filters">
            {activeLabels.map((label) => (
              <span key={label}>{label}</span>
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
