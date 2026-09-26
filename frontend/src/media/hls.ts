type HlsConstructor = {
  new (config?: Record<string, unknown>): HlsInstance;
  isSupported: () => boolean;
  Events: {
    ERROR: string;
    MANIFEST_PARSED: string;
  };
};

type HlsInstance = {
  loadSource: (source: string) => void;
  attachMedia: (video: HTMLVideoElement) => void;
  destroy: () => void;
  on: (event: string, handler: (...args: unknown[]) => void) => void;
  off: (event: string, handler: (...args: unknown[]) => void) => void;
};

declare global {
  interface Window {
    Hls?: HlsConstructor;
  }
}

let hlsPromise: Promise<HlsConstructor | null> | null = null;

export function canPlayNativeHls(video: HTMLVideoElement): boolean {
  // hls.js (MSE) is supported in every browser that can also play HLS
  // natively, so this only matters when hls.js is unavailable. Some
  // Chromium builds (Edge with media packs, WebViews) return 'maybe'
  // for mpegurl without being able to play it — video stalls at zero
  // decoded frames. 'maybe' is not trusted; only 'probably' counts.
  // (Safari without hls.js is the intended consumer; Safari WITH hls.js
  // goes through the MSE pipeline via the caller's hls.js-first check.)
  return !window.Hls && video.canPlayType('application/vnd.apple.mpegurl') === 'probably';
}

export function hlsUrl(base: string, audioIndex = 0): string {
  if (!base) return '';
  if (!audioIndex) return base;
  return `${base}${base.includes('?') ? '&' : '?'}a=${audioIndex}`;
}

export function loadHlsLibrary(): Promise<HlsConstructor | null> {
  if (window.Hls) return Promise.resolve(window.Hls);
  if (hlsPromise) return hlsPromise;

  hlsPromise = new Promise((resolve) => {
    const existing = document.querySelector<HTMLScriptElement>('script[data-hls-js]');
    if (existing) {
      existing.addEventListener('load', () => resolve(window.Hls || null), { once: true });
      existing.addEventListener('error', () => resolve(null), { once: true });
      return;
    }

    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js';
    script.async = true;
    script.dataset.hlsJs = 'true';
    script.addEventListener('load', () => resolve(window.Hls || null), { once: true });
    script.addEventListener('error', () => resolve(null), { once: true });
    document.head.appendChild(script);
  });

  return hlsPromise;
}

/** Test seam: clears the memoized loader promise so the next
 * loadHlsLibrary() call re-reads window.Hls / the document. */
export function resetHlsLibrary(): void {
  hlsPromise = null;
}

export async function attachHls(
  video: HTMLVideoElement,
  source: string,
  fallbackSource: string,
  onFatalError: () => void,
  startPosition = -1,
): Promise<HlsInstance | null> {
  // Prefer hls.js: it plays through MSE in every browser that supports it
  // (including Safari), giving one consistent pipeline. Native HLS is the
  // fallback for browsers without MSE-hls.js support. Some Chromium builds
  // (Edge with media packs, WebViews) return 'maybe' for mpegurl but
  // cannot actually play it — trusting that stalled playback at zero
  // decoded frames, so native is only tried when hls.js is unavailable.
  // The CDN script may hang (blocked network); time out to native/direct
  // instead of leaving the player dead.
  const Hls = await Promise.race([
    loadHlsLibrary(),
    new Promise<null>((resolve) => setTimeout(() => resolve(null), 10000)),
  ]);
  if (Hls?.isSupported()) {
    return attachWithHlsJs(Hls, video, source, fallbackSource, onFatalError, startPosition);
  }

  // Native HLS (Safari without hls.js): play the m3u8 directly.
  if (canPlayNativeHls(video)) {
    video.src = source;
    return null;
  }

  if (fallbackSource) video.src = fallbackSource;
  onFatalError();
  return null;
}

function attachWithHlsJs(
  Hls: HlsConstructor,
  video: HTMLVideoElement,
  source: string,
  fallbackSource: string,
  onFatalError: () => void,
  startPosition: number,
): HlsInstance | null {
  const hls = new Hls({
    enableWorker: true,
    lowLatencyMode: false,
    maxBufferLength: 30,
    maxMaxBufferLength: 120,
    maxBufferSize: 60 * 1024 * 1024,
    // Starting from the saved playhead prevents hls.js from downloading
    // segments 0/1 before it discovers that the viewer resumed much later.
    // It also lets the server begin the compatibility rendition at the
    // relevant HLS boundary instead of needlessly encoding from the start.
    startPosition: startPosition > 0 ? startPosition : -1,
    manifestLoadingTimeOut: 30000,
    manifestLoadingMaxRetry: 4,
    levelLoadingTimeOut: 30000,
    fragLoadingTimeOut: 60000,
    fragLoadingMaxRetry: 4,
  });
  hls.on(Hls.Events.ERROR, (_event, data) => {
    const fatal = Boolean((data as { fatal?: boolean } | undefined)?.fatal);
    if (!fatal) return;
    try {
      hls.destroy();
    } catch {
      // Best-effort cleanup before falling back to the direct stream.
    }
    if (fallbackSource) video.src = fallbackSource;
    onFatalError();
  });
  hls.loadSource(source);
  hls.attachMedia(video);
  return hls;
}
