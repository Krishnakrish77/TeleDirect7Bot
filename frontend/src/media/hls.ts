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
  return Boolean(video.canPlayType('application/vnd.apple.mpegurl'));
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

export function attachHls(
  video: HTMLVideoElement,
  source: string,
  fallbackSource: string,
  onFatalError: () => void,
): Promise<HlsInstance | null> {
  video.src = source;
  if (canPlayNativeHls(video)) return Promise.resolve(null);

  return loadHlsLibrary().then((Hls) => {
    if (!Hls?.isSupported()) {
      if (fallbackSource) video.src = fallbackSource;
      onFatalError();
      return null;
    }
    const hls = new Hls({
      enableWorker: true,
      lowLatencyMode: false,
      maxBufferLength: 30,
      maxMaxBufferLength: 120,
      maxBufferSize: 60 * 1024 * 1024,
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
      } catch (_) {
        // Best-effort cleanup before falling back to the direct stream.
      }
      if (fallbackSource) video.src = fallbackSource;
      onFatalError();
    });
    hls.loadSource(source);
    hls.attachMedia(video);
    return hls;
  });
}
