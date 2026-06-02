import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearLyricsCache, parseLrc } from '../hooks/lyrics';
import type { WatchTrack } from '../types';
import { LyricsFlipCard, LyricsPanel } from './lyrics';

function makeTrack(overrides: Partial<WatchTrack> = {}): WatchTrack {
  return {
    key: 'track-key',
    itemId: 'item-track-key',
    type: 'track',
    messageId: 1,
    secureHash: 'hash',
    title: 'Naanum',
    year: 2026,
    mediaKind: 'music',
    posterUrl: '/thumb/track.jpg',
    thumbUrl: '/thumb/track.jpg',
    backdropUrl: '/thumb/track-backdrop.jpg',
    duration: 100,
    durationLabel: '1:40',
    fileSize: 1000,
    fileSizeLabel: '1 KB',
    quality: 'mp3',
    genres: [],
    tags: [],
    overview: '',
    artist: 'Karthik',
    albumTitle: 'Album',
    href: '/watch/track-key',
    streamHref: '/stream/track-key',
    watchKey: 'track-key',
    trackNumber: 1,
    format: 'MP3',
    qualityLabel: 'MP3',
    appHref: '/app/watch/track-key',
    classicHref: '/watch/track-key',
    albumHref: '/app/album/album',
    ...overrides,
  } as WatchTrack;
}

beforeEach(() => clearLyricsCache());

afterEach(() => vi.unstubAllGlobals());

describe('parseLrc', () => {
  it('parses sorted synced lyric timestamps including repeated tags', () => {
    expect(parseLrc('[00:12.50]Second\n[00:01.00][00:02.00]First')).toEqual([
      { t: 1, text: 'First' },
      { t: 2, text: 'First' },
      { t: 12.5, text: 'Second' },
    ]);
  });
});

describe('LyricsPanel', () => {
  it('loads synced lyrics and lets the user seek by line', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        syncedLyrics: '[00:01.00]First line\n[00:12.50]Second line',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const seek = vi.fn();

    render(<LyricsPanel track={makeTrack()} currentTime={12.6} seek={seek} />);

    expect(await screen.findByRole('button', { name: 'Second line' })).toBeTruthy();
    expect(fetchMock.mock.calls[0][0]).toContain('https://lrclib.net/api/get?');
    expect(fetchMock.mock.calls[0][0]).toContain('track_name=Naanum');
    expect(screen.getByRole('button', { name: 'Second line' }).className).toContain('active');

    fireEvent.click(screen.getByRole('button', { name: 'First line' }));
    expect(seek).toHaveBeenCalledWith(1);
  });

  it('falls back to plain lyrics when synced lyrics are unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ plainLyrics: 'Plain\nLyrics' }),
    }));

    render(<LyricsPanel track={makeTrack({ title: 'Plain Song' })} currentTime={0} seek={vi.fn()} />);

    expect(await screen.findByText((_, element) => element?.textContent === 'Plain\nLyrics')).toBeTruthy();
  });

  it('deduplicates concurrent lyric fetches for repeated lyrics surfaces', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        syncedLyrics: '[00:01.00]First line',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const track = makeTrack({ title: 'Shared Song' });

    render(
      <>
        <LyricsPanel track={track} currentTime={1.2} seek={vi.fn()} />
        <LyricsPanel track={track} currentTime={1.2} seek={vi.fn()} />
      </>,
    );

    expect(await screen.findAllByRole('button', { name: 'First line' })).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('LyricsFlipCard', () => {
  it('flips album art to synced lyrics and lets the user seek', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        syncedLyrics: '[00:01.00]First line\n[00:12.50]Second line',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const seek = vi.fn();

    render(<LyricsFlipCard track={makeTrack()} currentTime={12.6} seek={seek} />);

    fireEvent.click(screen.getByLabelText('Show lyrics'));

    expect(await screen.findByRole('button', { name: 'Second line' })).toBeTruthy();
    expect(screen.getByLabelText('Hide lyrics')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'First line' }));
    expect(seek).toHaveBeenCalledWith(1);

    fireEvent.click(screen.getByLabelText('Hide lyrics'));
    expect(screen.getByLabelText('Show lyrics')).toBeTruthy();
  });

  it('hides the inactive flip face from assistive technology', () => {
    const view = render(<LyricsFlipCard track={makeTrack()} currentTime={0} seek={vi.fn()} />);
    const front = view.container.querySelector('.lyrics-flip-front');
    const back = view.container.querySelector('.lyrics-flip-back');

    expect(front?.getAttribute('aria-hidden')).toBeNull();
    expect(back?.getAttribute('aria-hidden')).toBe('true');

    fireEvent.click(screen.getByLabelText('Show lyrics'));

    expect(front?.getAttribute('aria-hidden')).toBe('true');
    expect(back?.getAttribute('aria-hidden')).toBeNull();
  });
});
