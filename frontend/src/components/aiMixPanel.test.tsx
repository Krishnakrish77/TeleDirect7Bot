import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { createAiMix, saveAiMixPlaylist } from '../api';
import type { WatchTrack } from '../types';
import { AiMixPanel } from './aiMixPanel';

vi.mock('../api', () => ({
  createAiMix: vi.fn(),
  saveAiMixPlaylist: vi.fn(),
}));

const track = (id: number): WatchTrack => ({
  key: `hash${id}`,
  itemId: String(id),
  type: 'track',
  messageId: id,
  secureHash: 'hash',
  title: `Track ${id}`,
  year: 2026,
  mediaKind: 'audio',
  posterUrl: '/poster.jpg',
  thumbUrl: '/poster.jpg',
  backdropUrl: '/poster.jpg',
  duration: 180,
  durationLabel: '3m',
  fileSize: 1,
  fileSizeLabel: '1 MB',
  quality: '',
  genres: [],
  tags: [],
  overview: '',
  artist: 'Artist',
  albumTitle: 'Album',
  href: '/app/watch/hash',
  streamHref: '/stream/hash',
  watchKey: `hash${id}`,
  trackNumber: id,
  format: 'MP3',
  qualityLabel: 'MP3',
  appHref: '/app/watch/hash',
  classicHref: '/watch/hash',
  albumHref: '/app/album/album',
});

it('generates, edits, plays, and atomically saves a temporary AI mix', async () => {
  vi.mocked(createAiMix).mockResolvedValue({
    title: 'Night Drive Mix', description: 'For a late drive.', prompt: 'night drive', discovery: 'balanced', generated: true,
    tracks: [track(1), track(2)],
  });
  vi.mocked(saveAiMixPlaylist).mockResolvedValue({
    playlistId: 'abcdefabcdefabcdefabcdefabcdefab', name: 'Night Drive Mix', trackCount: 1, coverUrls: [], createdAt: '', updatedAt: '', tracks: [track(2)], available: true, maxPlaylists: 50, maxTracks: 500,
  });
  const onPlay = vi.fn();
  render(<AiMixPanel onBack={vi.fn()} onPlay={onPlay} onShuffle={vi.fn()} />);

  fireEvent.click(screen.getByRole('button', { name: 'Create mix' }));
  expect(await screen.findByText('Night Drive Mix')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Remove Track 1' }));
  fireEvent.click(screen.getByRole('button', { name: 'Play' }));
  expect(onPlay).toHaveBeenCalledWith([expect.objectContaining({ messageId: 2 })]);
  fireEvent.click(screen.getByRole('button', { name: 'Save as playlist' }));
  await vi.waitFor(() => expect(saveAiMixPlaylist).toHaveBeenCalledWith('Night Drive Mix', [expect.objectContaining({ messageId: 2 })]));
  expect((await screen.findByRole('status')).textContent).toContain('Saved as Night Drive Mix.');
});
