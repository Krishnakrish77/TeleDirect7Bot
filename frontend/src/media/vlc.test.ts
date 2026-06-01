import { describe, expect, it } from 'vitest';
import { buildVlcHref } from './vlc';

describe('buildVlcHref', () => {
  it('adds VLC tracking tokens and desktop scheme', () => {
    expect(buildVlcHref('https://media.test/file', '7:abc', 'Macintosh')).toBe('vlc://https://media.test/file?vt=7%3Aabc');
  });

  it('uses Android intents', () => {
    expect(buildVlcHref('https://media.test/file', '', 'Android')).toBe(
      'intent://media.test/file#Intent;scheme=https;package=org.videolan.vlc;type=video/*;end',
    );
  });

  it('uses iOS x-callback URLs', () => {
    expect(buildVlcHref('https://media.test/file', '', 'iPhone')).toBe(
      'vlc-x-callback://x-callback-url/stream?url=https%3A%2F%2Fmedia.test%2Ffile',
    );
  });
});
