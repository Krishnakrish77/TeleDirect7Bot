import { useEffect, useRef } from 'react';

import { XIcon } from '../icons';
import { YOUTUBE_TRAILER_ALLOW, youtubeTrailerEmbedSrc } from '../utils/youtubeTrailer';

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
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

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
    window.setTimeout(() => closeButtonRef.current?.focus(), 0);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
      (returnFocusTo.current || previousActive)?.focus();
    };
  }, [onClose, returnFocusTo]);

  return (
    <div className="trailer-overlay" role="dialog" aria-modal="true" aria-label={`${title} trailer`} onClick={onClose}>
      <div className="trailer-shell" ref={shellRef} onClick={(event) => event.stopPropagation()}>
        <div className="trailer-toolbar">
          <strong className="trailer-title" dir="auto">{title}</strong>
          <div className="trailer-actions">
            <button ref={closeButtonRef} type="button" className="icon-button trailer-close" onClick={onClose} aria-label="Close trailer">
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
