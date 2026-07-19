import { useCallback, useEffect, useRef, useState } from 'react';

import { MaximizeIcon, PlayIcon, XIcon } from '../icons';
import { YOUTUBE_TRAILER_ALLOW, youtubeTrailerEmbedSrc, youtubeTrailerWatchUrl } from '../utils/youtubeTrailer';

type FullscreenShell = HTMLDivElement & {
  webkitRequestFullscreen?: () => Promise<void> | void;
};

function supportsElementFullscreen(): boolean {
  if (typeof HTMLElement === 'undefined') return false;
  const prototype = HTMLElement.prototype as HTMLElement & {
    webkitRequestFullscreen?: () => Promise<void> | void;
  };
  return Boolean(prototype.requestFullscreen || prototype.webkitRequestFullscreen);
}

function focusableTrailerElements(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  ));
}

export function TrailerModal({
  trailerKey,
  title,
  returnFocusTo,
  onClose,
}: {
  trailerKey: string;
  title: string;
  returnFocusTo: { current: HTMLElement | null };
  onClose: () => void;
}) {
  const shellRef = useRef<HTMLDivElement | null>(null);
  const firstActionRef = useRef<HTMLButtonElement | HTMLAnchorElement | null>(null);
  const [fullscreenSupported, setFullscreenSupported] = useState(supportsElementFullscreen);
  const trailerUrl = youtubeTrailerWatchUrl(trailerKey);
  const setFirstActionRef = useCallback((node: HTMLButtonElement | HTMLAnchorElement | null) => {
    firstActionRef.current = node;
  }, []);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const previousActive = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = focusableTrailerElements(shellRef.current);
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (!shellRef.current?.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    window.setTimeout(() => firstActionRef.current?.focus(), 0);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
      (returnFocusTo.current || previousActive)?.focus();
    };
  }, [onClose, returnFocusTo]);

  const requestFullscreen = useCallback(() => {
    const shell = shellRef.current as FullscreenShell | null;
    const request = shell?.requestFullscreen?.bind(shell) || shell?.webkitRequestFullscreen?.bind(shell);
    if (!request) {
      setFullscreenSupported(false);
      return;
    }
    try {
      const result = request();
      if (result && typeof result.catch === 'function') result.catch(() => setFullscreenSupported(false));
    } catch {
      setFullscreenSupported(false);
    }
  }, []);

  return (
    <div className="trailer-overlay" role="dialog" aria-modal="true" aria-label={`${title} trailer`} onClick={onClose}>
      <div className="trailer-shell" ref={shellRef} onClick={(event) => event.stopPropagation()}>
        <div className="trailer-toolbar">
          <strong className="trailer-title" dir="auto">{title}</strong>
          <div className="trailer-actions">
            {fullscreenSupported ? (
              <button ref={setFirstActionRef} type="button" className="secondary-action trailer-action" onClick={requestFullscreen} title="Fullscreen" aria-label="Open trailer fullscreen">
                <MaximizeIcon />
                <span>Fullscreen</span>
              </button>
            ) : (
              <a ref={setFirstActionRef} className="secondary-action trailer-action" href={trailerUrl} target="_blank" rel="noopener noreferrer" title="Open on YouTube for fullscreen" aria-label="Open trailer on YouTube for fullscreen">
                <MaximizeIcon />
                <span>Fullscreen</span>
              </a>
            )}
            <a className="secondary-action trailer-action" href={trailerUrl} target="_blank" rel="noopener noreferrer" title="Open on YouTube">
              <PlayIcon />
              <span>YouTube</span>
            </a>
            <button type="button" className="icon-button trailer-close" onClick={onClose} aria-label="Close trailer">
              <XIcon />
            </button>
          </div>
        </div>
        <div className="trailer-frame">
          <iframe src={youtubeTrailerEmbedSrc(trailerKey)} title="Trailer" allow={YOUTUBE_TRAILER_ALLOW} allowFullScreen tabIndex={-1} />
        </div>
      </div>
    </div>
  );
}
