import { describe, expect, it } from 'vitest';
import { playbackOptionInfo } from './playbackOptions';

describe('playbackOptionInfo', () => {
  it('labels an unlabelled source without calling it a version', () => {
    expect(playbackOptionInfo({ title: 'The Adventures of Tintin' })).toEqual({
      label: 'Original',
    });
  });

  it('keeps a tagged quality concise', () => {
    expect(playbackOptionInfo({ quality: '480p', title: 'The Adventures of Tintin' })).toEqual({
      label: '480p',
    });
  });
});
