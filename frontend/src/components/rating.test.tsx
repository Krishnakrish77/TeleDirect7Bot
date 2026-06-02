import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { fetchRating, setRating } from '../api';
import { RatingControls } from './rating';

vi.mock('../api', () => ({
  fetchRating: vi.fn(),
  setRating: vi.fn(),
}));

const fetchRatingMock = vi.mocked(fetchRating);
const setRatingMock = vi.mocked(setRating);

describe('RatingControls', () => {
  it('uses thumb icons for rating actions instead of arrow glyphs', async () => {
    fetchRatingMock.mockResolvedValue({ rating: null, counts: { up: 2, down: 1 } });
    setRatingMock.mockResolvedValue({ rating: 'up', counts: { up: 3, down: 1 } });

    const view = render(<RatingControls messageId={42} />);

    const upButton = await screen.findByRole('button', { name: 'Rate up' });
    const downButton = screen.getByRole('button', { name: 'Rate down' });

    expect(upButton.querySelector('svg')).toBeTruthy();
    expect(downButton.querySelector('svg')).toBeTruthy();
    expect(view.container.textContent).not.toContain('↑');
    expect(view.container.textContent).not.toContain('↓');

    fireEvent.click(upButton);

    expect(setRatingMock).toHaveBeenCalledWith('42', 'up');
  });
});
