import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
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
  beforeEach(() => {
    localStorage.clear();
  });

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

  it('highlights new episode updates on series cards', () => {
    render(
      <MediaCard
        card={card({
          type: 'series',
          itemId: 'series:castle',
          title: 'Castle',
          subtitle: '12 episodes - 1 season',
          href: '/app/series/castle',
          detailsHref: '/app/series/castle',
          playHref: '/app/watch/latest704',
          watchKey: 'latest704',
          episodeCount: 12,
          seasonCount: 1,
          newEpisode: {
            label: 'S01E02',
            title: 'Nanny McDead',
            playHref: '/app/watch/latest704',
            watchKey: 'latest704',
          },
        })}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.getByText('Castle')).toBeTruthy();
    expect(screen.getByLabelText('Castle has new episode S01E02 · Nanny McDead')).toBeTruthy();
    expect(screen.getByText('12 episodes - 1 season')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Castle from poster' }).getAttribute('href')).toBe('/app/series/castle');
  });

  it('does not duplicate the new episode fallback label', () => {
    render(
      <MediaCard
        card={card({
          type: 'series',
          itemId: 'series:castle',
          title: 'Castle',
          subtitle: '12 episodes - 1 season',
          href: '/app/series/castle',
          detailsHref: '/app/series/castle',
          playHref: '/app/watch/latest704',
          watchKey: 'latest704',
          episodeCount: 12,
          seasonCount: 1,
          newEpisode: {
            label: '',
            title: 'Castle Latest Upload',
            playHref: '/app/watch/latest704',
            watchKey: 'latest704',
          },
        })}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('Castle has new episode Castle Latest Upload')).toBeTruthy();
    expect(screen.queryByText('New episode')).toBeNull();
  });

  it('renders only the episode label when no clean episode title is available', () => {
    render(
      <MediaCard
        card={card({
          type: 'series',
          itemId: 'series:castle',
          title: 'Castle',
          subtitle: '12 episodes - 1 season',
          href: '/app/series/castle',
          detailsHref: '/app/series/castle',
          playHref: '/app/watch/latest704',
          watchKey: 'latest704',
          episodeCount: 12,
          seasonCount: 1,
          newEpisode: {
            label: 'S01E02',
            title: '',
            playHref: '/app/watch/latest704',
            watchKey: 'latest704',
          },
        })}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('Castle has new episode S01E02')).toBeTruthy();
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

    expect(screen.getByLabelText('Not for me').getAttribute('title')).toBe('Not for me');
    fireEvent.click(screen.getByLabelText('Not for me'));
    expect(onDismiss).toHaveBeenCalledWith({ tmdbId: 123, kind: 'movie' }, media);
  });

  it('opens inline trailer previews only when a trailer key is present', () => {
    render(<MediaCard card={card({ trailerKey: 'abc123' })} saved={false} onToggleSaved={vi.fn()} />);

    const posterLink = screen.getByRole('link', { name: 'Open Kalki 2898-AD from poster' });
    expect(posterLink.getAttribute('aria-hidden')).toBeNull();

    const previewButton = screen.getByRole('button', { name: /Preview Kalki/i });
    expect(previewButton.closest('.poster-frame')).toBeTruthy();
    expect(previewButton.closest('a')).toBeNull();

    fireEvent.click(previewButton);

    const preview = screen.getByTitle('Kalki 2898-AD trailer preview') as HTMLIFrameElement;
    const previewSrc = preview.getAttribute('src') || '';
    expect(previewSrc).toContain('youtube.com/embed/abc123');
    expect(previewSrc).toContain('controls=1');
    expect(previewSrc).not.toContain('autoplay=1');
    expect(previewSrc).not.toContain('mute=1');
    expect(preview.getAttribute('allow')).toContain('fullscreen');
    expect(preview.getAttribute('allow')).toContain('encrypted-media');
    expect(preview.hasAttribute('allowfullscreen')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Close preview' }));
    expect(screen.queryByTitle('Kalki 2898-AD trailer preview')).toBeNull();
  });

  it('removes card interactions from keyboard flow while disabled', () => {
    const onToggleSaved = vi.fn();
    render(
      <MediaCard
        card={card({ trailerKey: 'abc123' })}
        saved={false}
        onToggleSaved={onToggleSaved}
        interactionDisabled
      />,
    );

    const posterLink = screen.getByRole('link', { name: 'Open Kalki 2898-AD from poster' });
    const textLink = Array.from(document.querySelectorAll<HTMLAnchorElement>('.media-card-link'))[0];
    expect(posterLink.getAttribute('aria-disabled')).toBe('true');
    expect(posterLink.getAttribute('tabindex')).toBe('-1');
    expect(textLink.getAttribute('aria-disabled')).toBe('true');
    expect(textLink.getAttribute('tabindex')).toBe('-1');
    expect(fireEvent.click(textLink)).toBe(false);

    expect((screen.getByRole('button', { name: /Preview Kalki/i }) as HTMLButtonElement).disabled).toBe(true);
    const saveButton = screen.getByRole('button', { name: 'Add to watchlist' }) as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);
    expect(saveButton.getAttribute('title')).toBe('Add to watchlist');
    fireEvent.click(saveButton);
    expect(onToggleSaved).not.toHaveBeenCalled();
  });

  it('shows local progress without exposing a card-level mark watched action', () => {
    const media = card({ watchKey: 'hash42' });
    localStorage.setItem('td:cw', JSON.stringify({ hash42: { pos: 120, dur: 240 } }));

    const { container } = render(
      <MediaCard
        card={media}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText('Mark Kalki 2898-AD as watched')).toBeNull();
    expect(container.querySelector('.card-progress span')?.getAttribute('style')).toContain('width: 50%');
  });

  it('shows watched status from API without showing the action', () => {
    render(
      <MediaCard
        card={card({ watched: true, watchKey: 'hash42' })}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('Kalki 2898-AD watched')).toBeTruthy();
    expect(screen.queryByLabelText('Mark Kalki 2898-AD as watched')).toBeNull();
  });

  it('shows watched status from local completion storage', () => {
    localStorage.setItem('td:watched:v1', JSON.stringify({ hash42: Date.now() }));

    render(
      <MediaCard
        card={card({ watchKey: 'hash42' })}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('Kalki 2898-AD watched')).toBeTruthy();
    expect(screen.queryByLabelText('Mark Kalki 2898-AD as watched')).toBeNull();
  });

  it('does not show mark watched from scratch on cards', () => {
    render(
      <MediaCard
        card={card({ watchKey: 'hash42' })}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText('Mark Kalki 2898-AD as watched')).toBeNull();
  });

  it('does not show mark watched from scratch for grouped series cards', () => {
    render(
      <MediaCard
        card={card({
          type: 'series',
          itemId: 'series:kalki',
          href: '/app/series/kalki',
          playHref: '',
          streamHref: '',
          watchKey: 'hash42',
          episodeCount: 6,
        })}
        saved={false}
        onToggleSaved={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText('Mark Kalki 2898-AD as watched')).toBeNull();
  });
});
