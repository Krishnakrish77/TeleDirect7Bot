import { describe, expect, it } from 'vitest';
import { playbackOptionInfo } from './playbackOptions';

describe('playbackOptionInfo', () => {
  it('labels an unlabelled source without calling it a version', () => {
    expect(playbackOptionInfo({ title: 'The Adventures of Tintin' })).toEqual({
      label: 'Unknown',
    });
  });

  it('keeps a tagged quality concise', () => {
    expect(playbackOptionInfo({ quality: '480p', title: 'The Adventures of Tintin' })).toEqual({
      label: '480p',
    });
  });

  it('falls back to the theatre-print source tag when quality is unparseable', () => {
    expect(playbackOptionInfo({ sourceType: 'PreDVD', title: 'The Adventures of Tintin' })).toEqual({
      label: 'PreDVD',
    });
  });

  it('shows the source tag alongside the resolution when both parse', () => {
    expect(playbackOptionInfo({ quality: '720p', sourceType: 'PreDVD' })).toEqual({
      label: 'PreDVD · 720p',
    });
  });
});
