import { describe, expect, it } from 'vitest';
import { playbackOptionInfo } from './playbackOptions';

describe('playbackOptionInfo', () => {
  it('explains an unlabelled source without calling it a version', () => {
    expect(playbackOptionInfo({ title: 'The Adventures of Tintin' }, 'The Adventures of Tintin')).toEqual({
      label: 'Original upload',
      description: 'Resolution not labelled',
    });
  });

  it('explains the data tradeoff of a lower-resolution copy', () => {
    expect(playbackOptionInfo({ quality: '480p', title: 'The Adventures of Tintin' }, 'The Adventures of Tintin')).toEqual({
      label: '480p',
      description: 'Standard definition · uses less data',
    });
  });
});
