import { afterEach, describe, expect, it, vi } from 'vitest';
import { canPlayNativeHls, hlsUrl, resetHlsLibrary } from './hls';

// canPlayNativeHls gates the native-HLS path. Some Chromium builds
// (Edge with media packs, WebViews) report 'maybe' for mpegurl yet
// cannot play it — playback stalls at zero decoded frames. Native is
// therefore only valid when hls.js is unavailable.

function fakeVideo(canPlay: string): HTMLVideoElement {
  const el = document.createElement('video');
  vi.spyOn(el, 'canPlayType').mockReturnValue(canPlay as never);
  return el;
}

describe('canPlayNativeHls', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    Reflect.deleteProperty(window, 'Hls');
    resetHlsLibrary();
  });

  it('is false when hls.js is available, even with confident support', () => {
    window.Hls = class {} as never;
    expect(canPlayNativeHls(fakeVideo('probably'))).toBe(false);
  });

  it('is false for an untrusted maybe without hls.js', () => {
    Reflect.deleteProperty(window, 'Hls');
    expect(canPlayNativeHls(fakeVideo('maybe'))).toBe(false);
  });

  it('is true when hls.js is gone and support is confident', () => {
    Reflect.deleteProperty(window, 'Hls');
    expect(canPlayNativeHls(fakeVideo('probably'))).toBe(true);
  });

  it('is false when the browser reports no support at all', () => {
    Reflect.deleteProperty(window, 'Hls');
    expect(canPlayNativeHls(fakeVideo(''))).toBe(false);
  });
});

describe('hlsUrl', () => {
  it('appends the audio track selector', () => {
    expect(hlsUrl('/hls/k1/playlist.m3u8', 2)).toBe('/hls/k1/playlist.m3u8?a=2');
  });

  it('leaves the default track URL untouched', () => {
    expect(hlsUrl('/hls/k1/playlist.m3u8', 0)).toBe('/hls/k1/playlist.m3u8');
  });
});
