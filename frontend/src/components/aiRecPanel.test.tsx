import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { streamAiRecommendations } from '../api';
import { AiRecPanel } from './aiRecPanel';

vi.mock('../api', () => ({
  dismissRecommendation: vi.fn(),
  streamAiRecommendations: vi.fn(),
  trackRecommendationEvents: vi.fn(),
}));

function renderPanel() {
  return render(
    <AiRecPanel
      open
      onClose={vi.fn()}
      saved={new Set()}
      onToggleSaved={vi.fn()}
      onPlayMix={vi.fn()}
      onShuffleMix={vi.fn()}
      onRequestTitle={vi.fn()}
    />,
  );
}

describe('AiRecPanel reliability status', () => {
  it('makes a saved AI-curated shelf and its freshness visible', async () => {
    vi.mocked(streamAiRecommendations).mockResolvedValue({
      items: [], externalItems: [], message: '', coldStart: false,
      recommendationMeta: { origin: 'agent', cached: true, fallback: false, generatedAt: Math.floor(Date.now() / 1000) },
    });
    renderPanel();

    await waitFor(() => expect(screen.getByText(/Saved just now · AI-curated from your library/)).toBeTruthy());
  });

  it('explains when resilient library picks replace personalized curation', async () => {
    vi.mocked(streamAiRecommendations).mockResolvedValue({
      items: [], externalItems: [], message: '', coldStart: false,
      recommendationMeta: { origin: 'library', cached: false, fallback: true, generatedAt: Math.floor(Date.now() / 1000) },
    });
    renderPanel();

    await waitFor(() => expect(screen.getByText(/Personalized curation was unavailable/)).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(streamAiRecommendations).toHaveBeenLastCalledWith({ refresh: true }, expect.any(Function), expect.any(AbortSignal)));
  });
});
