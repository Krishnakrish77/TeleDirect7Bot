import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { Dialog, DialogContent, DialogTitle } from './dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './select';

// Radix scrolls the selected option into view after opening. jsdom does not
// implement that browser method, but its absence is unrelated to portal layer
// coverage here.
Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
  configurable: true,
  value: () => undefined,
});

it('renders its portal above modal content', async () => {
  render(
    <Dialog open>
      <DialogContent>
        <DialogTitle>Edit title</DialogTitle>
        <Select defaultValue="movie">
          <SelectTrigger aria-label="Kind"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="movie">Movie</SelectItem>
            <SelectItem value="tv">TV</SelectItem>
          </SelectContent>
        </Select>
      </DialogContent>
    </Dialog>,
  );

  fireEvent.click(screen.getByRole('combobox', { name: 'Kind' }));

  const option = await screen.findByRole('option', { name: 'Movie' });
  expect(screen.getByRole('dialog', { hidden: true }).className).toContain('z-[100]');
  expect(option.closest('[data-slot="select-content"]')?.className).toContain('z-[110]');
});
