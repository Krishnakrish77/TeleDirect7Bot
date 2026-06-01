import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { HubFilters, HubParams } from '../types';
import { FilterBar } from './filters';

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

    fireEvent.change(screen.getByLabelText('Sort'), { target: { value: 'title_az' } });
    expect(update).toHaveBeenCalledWith({ sort: 'title_az', offset: 0 });
  });

  it('uses a compact inline advanced filter group with tags included', () => {
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

    fireEvent.click(screen.getByRole('button', { name: /Filters/i }));
    const advanced = screen.getByLabelText('Advanced filters');

    expect(screen.getByRole('button', { name: /Filters/i }).getAttribute('aria-expanded')).toBe('true');

    fireEvent.change(within(advanced).getByLabelText('Tag'), { target: { value: 'Tamil' } });
    expect(update).toHaveBeenCalledWith({ tag: 'Tamil', offset: 0 });

    fireEvent.change(within(advanced).getByLabelText('Year'), { target: { value: '2026' } });
    expect(update).toHaveBeenCalledWith({ year: 2026, offset: 0 });
  });

  it('resets all filters and query from the expanded filter group', () => {
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

    fireEvent.click(screen.getByRole('button', { name: /Filters/i }));
    fireEvent.click(within(screen.getByLabelText('Advanced filters')).getByRole('button', { name: 'Reset' }));

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
    }, true);
  });
});
