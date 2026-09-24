import type { SubtitleTrack } from '../types';

export function looksLikeVtt(text: string): boolean {
  return /^\s*WEBVTT\b/i.test(text);
}

export function srtToVtt(text: string): string {
  let body = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  body = body
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, '$1.$2');
  return looksLikeVtt(body) ? body : `WEBVTT\n\n${body}`;
}

function subtitleUrl(vtt: string): string {
  return URL.createObjectURL(new Blob([vtt], { type: 'text/vtt' }));
}

/** Create a temporary track without writing it to the shared catalogue or local cache. */
export function subtitleTextToTrack(text: string, label: string, language = 'und'): SubtitleTrack {
  const vtt = looksLikeVtt(text) ? text : srtToVtt(text);
  return {
    id: `temporary:${Date.now()}`,
    url: subtitleUrl(vtt),
    language: language || 'und',
    label: label || 'Subtitles',
    codec: 'webvtt',
    kind: 'temporary',
  };
}

export async function subtitleFileToTrack(file: File, watchKey: string): Promise<SubtitleTrack> {
  const name = (file.name || '').toLowerCase();
  if (!name.endsWith('.srt') && !name.endsWith('.vtt')) {
    throw new Error('Only .srt and .vtt files are supported.');
  }
  const raw = await file.text();
  const vtt = name.endsWith('.vtt') && looksLikeVtt(raw) ? raw : srtToVtt(raw);
  const label = file.name.replace(/\.[^.]+$/, '') || 'Custom';
  // Deliberately keep viewer-supplied files local to the current player.
  // Durable shared sidecars are an admin-only catalogue operation.
  void watchKey;
  return {
    id: `custom:${Date.now()}`,
    url: subtitleUrl(vtt),
    language: 'und',
    label,
    codec: 'webvtt',
    kind: 'custom',
  };
}

export function revokeSubtitleTrack(track: SubtitleTrack): void {
  if (!['custom', 'temporary'].includes(track.kind) || !track.url.startsWith('blob:')) return;
  URL.revokeObjectURL(track.url);
}
