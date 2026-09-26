import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { streamAiRecommendationJob, streamAiRecommendations, submitAiRecommendationJob } from '../api';
import { AiRecPanel } from './aiRecPanel';

vi.mock('../api', () => ({
  dismissRecommendation: vi.fn(),
  streamAiRecommendations: vi.fn(),
  submitAiRecommendationJob: vi.fn(),
  streamAiRecommendationJob: vi.fn(),
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
    // Retry goes through the background job path: submit id at once, then
    // subscribe to the job stream for the regenerated shelf.
    vi.mocked(submitAiRecommendationJob).mockResolvedValue({ jobId: 'job-1' });
    vi.mocked(streamAiRecommendationJob).mockResolvedValue({
      items: [], externalItems: [], message: '', coldStart: false,
      recommendationMeta: { origin: 'library', cached: false, fallback: true, generatedAt: Math.floor(Date.now() / 1000) },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(submitAiRecommendationJob).toHaveBeenLastCalledWith({ refresh: true }, expect.any(AbortSignal)));
    await waitFor(() => expect(streamAiRecommendationJob).toHaveBeenCalledWith('job-1', expect.any(Function), expect.any(AbortSignal)));
  });

  it('lands job results in the panel when a background ask completes', async () => {
    vi.mocked(streamAiRecommendations).mockResolvedValue({
      items: [], externalItems: [], message: '', coldStart: false,
    });
    vi.mocked(submitAiRecommendationJob).mockResolvedValue({ jobId: 'job-2' });
    vi.mocked(streamAiRecommendationJob).mockResolvedValue({
      items: [{
        itemId: 'x', href: '/x', title: 'Picked', bucket: 'comfort', type: 'item',
        subtitle: '', year: null, mediaKind: '', posterUrl: '', posterSrcSet: '', thumbUrl: '',
        backdropUrl: '', duration: 0, durationLabel: '', fileSize: 0, fileSizeLabel: '',
        quality: '', sourceType: '', genres: [], tags: [], overview: '', artist: '', albumTitle: '',
        trailerKey: '', streamHref: '', watchKey: '', eyebrow: '', badge: '', aspect: 'poster',
      }], externalItems: [], message: '', coldStart: false,
      recommendationMeta: { origin: 'agent', cached: false, fallback: false, generatedAt: Math.floor(Date.now() / 1000) },
    });
    renderPanel();

    await waitFor(() => expect(screen.getByLabelText('Ask the recommender')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Ask the recommender'), { target: { value: 'something fun' } });
    fireEvent.submit(screen.getByLabelText('Ask the recommender').closest('form')!);

    await waitFor(() => expect(screen.getByText('Picked')).toBeTruthy());
    // The status line clears and the shelf appears — the ask never blocked on the request lifetime.
    expect(submitAiRecommendationJob).toHaveBeenCalledWith({ query: 'something fun' }, expect.any(AbortSignal));
  });
});
