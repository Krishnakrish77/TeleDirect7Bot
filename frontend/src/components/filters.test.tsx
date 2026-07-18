import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { HubFilters, HubParams } from '../types';
import { FilterBar, FilterPage } from './filters';

const filters: HubFilters = {
  years: [2026, 2025],
  qualities: ['1080p', '720p'],
  genres: ['Action', 'Drama'],
  tags: [{ name: 'Tamil', count: 4 }, { name: 'Kids', count: 2 }],
  sortOptions: [
    { value: 'newest', label: 'Newest' },
    { value: 'title_az', label: 'Title A-Z' },
  ],
  views: [
    { value: '', label: 'All' },
    { value: 'movies', label: 'Movies' },
    { value: 'series', label: 'Series' },
    { value: 'music', label: 'Music' },
  ],
};

function params(overrides: Partial<HubParams> = {}): HubParams {
  return {
    q: '',
    tag: '',
    quality: '',
    genre: '',
    year: null,
    sort: 'newest',
    view: '',
    offset: 0,
    limit: 60,
    ...overrides,
  };
}

describe('FilterBar', () => {
  it('keeps content type and sort as direct actions', () => {
    const update = vi.fn();

    render(
      <FilterBar
        filters={filters}
        catalogueSize={596}
        params={params()}
        query=""
        setQuery={vi.fn()}
        update={update}
      />,
    );

    expect(screen.getByText('596 titles')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Music' }));
    expect(update).toHaveBeenCalledWith({ view: 'music', offset: 0 });

    fireEvent.click(screen.getByLabelText('Sort'));
    fireEvent.click(screen.getByRole('option', { name: 'Title A-Z' }));
    expect(update).toHaveBeenCalledWith({ sort: 'title_az', offset: 0 });
  });

  it('links advanced filters to the dedicated filter page', () => {
    const update = vi.fn();

    render(
      <FilterBar
        filters={filters}
        catalogueSize={596}
        params={params({ quality: '1080p', genre: 'Action' })}
        query=""
        setQuery={vi.fn()}
        update={update}
      />,
    );

    const filtersLink = screen.getByRole('link', { name: /Filters/i });

    expect(filtersLink.getAttribute('href')).toContain('/app/filters?quality=1080p&genre=Action');
    expect(update).not.toHaveBeenCalled();
  });

  it('resets all filters and query from the browse toolbar', () => {
    const update = vi.fn();
    const setQuery = vi.fn();

    render(
      <FilterBar
        filters={filters}
        catalogueSize={596}
        params={params({ view: 'music', sort: 'title_az', tag: 'Tamil', year: 2026 })}
        query="theme"
        setQuery={setQuery}
        update={update}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));

    expect(setQuery).toHaveBeenCalledWith('');
    expect(update).toHaveBeenCalledWith({
      q: '',
      tag: '',
      quality: '',
      genre: '',
      year: null,
      sort: 'newest',
      view: '',
      offset: 0,
    }, false);
  });
});

describe('FilterPage', () => {
  it('updates filter route params without rendering a modal', () => {
    const navigate = vi.fn();

    render(
      <FilterPage
        filters={filters}
        catalogueSize={596}
        params={params({ view: 'movies', quality: '1080p', genre: 'Action' })}
        query=""
        setQuery={vi.fn()}
        navigate={navigate}
      />,
    );

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByText('596 titles')).toBeTruthy();
    expect(screen.getAllByText('1080p').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Action').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByLabelText('Tag'));
    fireEvent.click(screen.getByRole('option', { name: 'Tamil' }));
    expect(navigate.mock.calls[0][0]).toContain('/app/filters?tag=Tamil&quality=1080p&genre=Action&view=movies');
    expect(navigate.mock.calls[0][1]).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Series' }));
    expect(navigate.mock.calls[1][0]).toContain('/app/filters?quality=1080p&genre=Action&view=series');
    expect(navigate.mock.calls[1][1]).toBe(true);
  });

  it('applies filters by linking back to results and can reset on the filter route', () => {
    const navigate = vi.fn();
    const setQuery = vi.fn();

    render(
      <FilterPage
        filters={filters}
        catalogueSize={596}
        params={params({ q: 'theme', tag: 'Tamil', year: 2026 })}
        query="theme"
        setQuery={setQuery}
        navigate={navigate}
      />,
    );

    expect(screen.getByRole('link', { name: 'Show results' }).getAttribute('href')).toContain('/app?q=theme&tag=Tamil&year=2026');

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(setQuery).toHaveBeenCalledWith('');
    expect(navigate).toHaveBeenCalledWith('/app/filters', true);
  });
});
