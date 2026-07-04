import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { HubCard } from '../types';
import { getMediaCardDisplay, getMediaCardMetaItems, MediaCard } from './mediaCard';

function card(overrides: Partial<HubCard> = {}): HubCard {
  return {
    type: 'item',
    itemId: '42',
    messageId: 42,
    secureHash: 'hash',
    title: 'Kalki 2898-AD',
    subtitle: '2024 - 2h 55m - 720p',
    year: 2024,
    mediaKind: 'video',
    posterUrl: '/poster.jpg',
    thumbUrl: '/thumb.jpg',
    backdropUrl: '/backdrop.jpg',
    duration: 10545,
    durationLabel: '2h 55m',
    fileSize: 1000,
    fileSizeLabel: '1 KB',
    quality: '720p',
    genres: ['Action'],
    tags: [],
    overview: '',
    artist: '',
    albumTitle: '',
    trailerKey: '',
    href: '/app/watch/hash42',
    playHref: '/app/watch/hash42',
    detailsHref: '/app/watch/hash42',
    streamHref: '/hash42',
    watchKey: 'hash42',
    eyebrow: '720p',
    badge: '720p',
    aspect: 'poster',
    ...overrides,
  };
}

describe('MediaCard', () => {
  it('shows content context and a compact video metadata strip', () => {
    render(<MediaCard card={card()} saved={false} onToggleSaved={vi.fn()} />);

    expect(screen.getByText('Video')).toBeTruthy();
    expect(screen.getByText('Kalki 2898-AD')).toBeTruthy();
    expect(screen.getByText('Action')).toBeTruthy();
    expect(screen.getByLabelText('Kalki 2898-AD metadata')).toBeTruthy();
    expect(screen.getAllByText(/2024/)).toHaveLength(1);
    expect(screen.getAllByText(/720p/)).toHaveLength(1);
    expect(screen.getByText('2h 55m')).toBeTruthy();
  });

  it('shows album artist and track count once in the subtitle', () => {
    const album = card({
      type: 'album',
      itemId: 'album:navarasa',
      title: 'Navarasa',
      subtitle: 'Karthik - 3 tracks',
      year: 2021,
      mediaKind: 'audio',
      artist: 'Karthik',
      badge: '3 tracks',
      aspect: 'square',
      trackCount: 3,
    });

    render(<MediaCard card={album} saved={false} onToggleSaved={vi.fn()} />);

    expect(screen.getByText('Album')).toBeTruthy();
    expect(screen.getByText('Navarasa')).toBeTruthy();
    expect(screen.getByText('Karthik - 3 tracks')).toBeTruthy();
    expect(screen.getAllByText(/3 tracks/)).toHaveLength(1);
    expect(screen.queryByText(/2021/)).toBeNull();
  });

  it('normalizes card display data independently from API metadata', () => {
    expect(getMediaCardDisplay(card()).eyebrow).toBe('Video');
    expect(getMediaCardMetaItems(card())).toEqual(['2024', '2h 55m', '720p']);
    expect(getMediaCardDisplay(card({ type: 'track', mediaKind: 'audio', artist: 'Anirudh' }))).toMatchObject({
      eyebrow: 'Song',
      subtitle: 'Anirudh',
    });
  });

  it('shows recommendation reason text when present', () => {
    render(<MediaCard card={card({ recReason: 'Because you like Action' })} saved={false} onToggleSaved={vi.fn()} />);

    expect(screen.getByText('Because you like Action')).toBeTruthy();
  });

  it('shows external ratings for video cards', () => {
    render(
      <MediaCard
        card={card({ externalRating: { provider: 'TMDB', value: 7.8, label: '7.8', count: 1200 } })}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('External rating TMDB 7.8')).toBeTruthy();
    expect(screen.getByLabelText('Kalki 2898-AD metadata').textContent).not.toContain('TMDB 7.8');
    expect(screen.getAllByText('TMDB 7.8')).toHaveLength(1);
  });

  it('does not show external ratings on music cards', () => {
    render(
      <MediaCard
        card={card({
          type: 'album',
          mediaKind: 'audio',
          aspect: 'square',
          externalRating: { provider: 'TMDB', value: 7.8, label: '7.8', count: 1200 },
        })}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText(/External rating/i)).toBeNull();
  });

  it('shows aggregate community ratings for video cards', () => {
    render(
      <MediaCard
        card={card({ ratingCounts: { up: 4, down: 1 } })}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('Community rating: 4 up, 1 down')).toBeTruthy();
    expect(screen.getByText('4')).toBeTruthy();
    expect(screen.getByText('1')).toBeTruthy();
  });

  it('does not show aggregate community ratings on music cards', () => {
    render(
      <MediaCard
        card={card({
          type: 'track',
          mediaKind: 'audio',
          aspect: 'square',
          ratingCounts: { up: 4, down: 1 },
        })}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText(/Community rating/i)).toBeNull();
  });

  it('surfaces dismiss controls for recommendation cards', () => {
    const onDismiss = vi.fn();
    const media = card();

    render(
      <MediaCard
        card={media}
        saved={false}
        onToggleSaved={vi.fn()}
        dismissMeta={{ tmdbId: 123, kind: 'movie' }}
        onDismiss={onDismiss}
      />,
    );

    fireEvent.click(screen.getByLabelText('Not for me'));
    expect(onDismiss).toHaveBeenCalledWith({ tmdbId: 123, kind: 'movie' }, media);
  });

  it('opens inline trailer previews only when a trailer key is present', () => {
    render(<MediaCard card={card({ trailerKey: 'abc123' })} saved={false} onToggleSaved={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /Preview Kalki/i }));

    const preview = screen.getByTitle('Kalki 2898-AD trailer preview') as HTMLIFrameElement;
    expect(preview.getAttribute('src')).toContain('youtube.com/embed/abc123');
    fireEvent.click(screen.getByRole('button', { name: 'Close preview' }));
    expect(screen.queryByTitle('Kalki 2898-AD trailer preview')).toBeNull();
  });
});
