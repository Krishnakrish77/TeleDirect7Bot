import { describe, expect, it } from 'vitest';
import { looksLikeVtt, srtToVtt } from './subtitles';

describe('subtitle helpers', () => {
  it('converts SRT timestamps to WebVTT', () => {
    const vtt = srtToVtt('1\n00:00:01,200 --> 00:00:03,000\nHello');

    expect(vtt.startsWith('WEBVTT')).toBe(true);
    expect(vtt).toContain('00:00:01.200 --> 00:00:03.000');
  });

  it('detects existing WebVTT files', () => {
    expect(looksLikeVtt('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi')).toBe(true);
  });
});
