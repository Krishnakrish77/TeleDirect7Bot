import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { HubCard } from '../types';
import { getMediaCardDisplay, MediaCard } from './mediaCard';

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
  it('shows year and quality once in the video subtitle', () => {
    render(<MediaCard card={card()} saved={false} onToggleSaved={vi.fn()} />);

    expect(screen.getByText('Video')).toBeTruthy();
    expect(screen.getByText('Kalki 2898-AD')).toBeTruthy();
    expect(screen.getByText('2024 - 2h 55m - 720p')).toBeTruthy();
    expect(screen.getAllByText(/2024/)).toHaveLength(1);
    expect(screen.getAllByText(/720p/)).toHaveLength(1);
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
    expect(getMediaCardDisplay(card({ type: 'track', mediaKind: 'audio', artist: 'Anirudh' }))).toMatchObject({
      eyebrow: 'Song',
      subtitle: 'Anirudh',
    });
  });

  it('shows recommendation reason text when present', () => {
    render(<MediaCard card={card({ recReason: 'Because you like Action' })} saved={false} onToggleSaved={vi.fn()} />);

    expect(screen.getByText('Because you like Action')).toBeTruthy();
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
});
