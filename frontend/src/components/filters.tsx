import { useState } from 'react';
import { FilterIcon } from '../icons';
import type { FilterOption, HubFilters, HubParams, ViewValue } from '../types';

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

function SelectControl({ control, className = '' }: { control: FilterControl; className?: string }) {
  return (
    <label className={['filter-select-control', className].filter(Boolean).join(' ')}>
      <span>{control.label}</span>
      <select value={control.value} onChange={(event) => control.onChange(event.currentTarget.value)} aria-label={control.label}>
        {control.options.map((option) => (
          <option key={option.value || 'any'} value={option.value}>{option.label}</option>
        ))}
      </select>
    </label>
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
  const [filtersOpen, setFiltersOpen] = useState(false);
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
  const activeLabels = [
    query ? `Search: ${query}` : '',
    params.year ? String(params.year) : '',
    params.quality ? optionLabel(qualityOptions, params.quality) : '',
    params.genre ? optionLabel(genreOptions, params.genre) : '',
    params.tag ? optionLabel(tagOptions, params.tag) : '',
  ].filter(Boolean);
  const activeFilterCount = activeLabels.length;
  const hasFilters = activeFilterCount > 0 || params.sort !== 'newest' || Boolean(params.view);
  const summary = activeFilterCount
    ? `${activeLabels.slice(0, 2).join(' / ')}${activeFilterCount > 2 ? ` +${activeFilterCount - 2}` : ''}`
    : 'Any year, quality, genre';
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
    <section className="filter-panel" aria-label="Browse filters">
      <div className="filter-panel-header">
        <div className="filter-heading">
          <FilterIcon />
          <span>{catalogueSize ? `${catalogueSize.toLocaleString()} titles` : 'Library'}</span>
        </div>
        {hasFilters && (
          <button className="filter-clear-button" type="button" onClick={() => clearAll()}>
            Reset
          </button>
        )}
      </div>

      <div className="filter-view-row" role="group" aria-label="Content type">
        {viewOptions.map((option) => (
          <button
            key={option.value || 'all'}
            type="button"
            className={params.view === option.value ? 'filter-view-chip active' : 'filter-view-chip'}
            aria-pressed={params.view === option.value}
            onClick={() => update({ view: option.value as ViewValue, offset: 0 })}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="filter-action-row">
        <button
          type="button"
          className="filter-drawer-button"
          onClick={() => setFiltersOpen((open) => !open)}
          aria-expanded={filtersOpen}
          aria-controls="advanced-filters"
        >
          <span>Filters</span>
          <strong>{summary}</strong>
          <small>{activeFilterCount ? `${activeFilterCount} active` : 'Optional'}</small>
        </button>
        <SelectControl control={sortControl} className="filter-sort-control" />
      </div>

      <div
        id="advanced-filters"
        className={filtersOpen ? 'filter-inline-controls expanded' : 'filter-inline-controls'}
        aria-label="Advanced filters"
      >
        {metadataControls.map((control) => (
          <SelectControl key={control.id} control={control} />
        ))}
        {filtersOpen && (
          <div className="filter-inline-actions">
            <button className="filter-clear-button" type="button" onClick={() => clearAll(true)} disabled={!hasFilters}>Reset</button>
            <button className="primary-action" type="button" onClick={() => setFiltersOpen(false)}>Done</button>
          </div>
        )}
      </div>
    </section>
  );
}
