import { render, screen } from '@testing-library/react';
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
  it('does not repeat year or quality metadata on video cards', () => {
    render(<MediaCard card={card()} saved={false} onToggleSaved={vi.fn()} />);

    expect(screen.getByText('Video')).toBeTruthy();
    expect(screen.getByText('Kalki 2898-AD')).toBeTruthy();
    expect(screen.getByText('2h 55m')).toBeTruthy();
    expect(screen.queryByText(/2024/)).toBeNull();
    expect(screen.queryByText(/720p/)).toBeNull();
  });

  it('shows album artist without repeating track count', () => {
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
    expect(screen.getByText('Karthik')).toBeTruthy();
    expect(screen.queryByText(/3 tracks/)).toBeNull();
    expect(screen.queryByText(/2021/)).toBeNull();
  });

  it('normalizes card display data independently from API metadata', () => {
    expect(getMediaCardDisplay(card()).eyebrow).toBe('Video');
    expect(getMediaCardDisplay(card({ type: 'track', mediaKind: 'audio', artist: 'Anirudh' }))).toMatchObject({
      eyebrow: 'Song',
      subtitle: 'Anirudh',
    });
  });
});
