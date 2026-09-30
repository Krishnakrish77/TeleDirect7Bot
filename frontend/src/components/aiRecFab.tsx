import { lazy, Suspense, useState } from 'react';
import { CouchMateIcon } from '../icons';
import type { BuddyChatContext, HubCard, RequestTitle, WatchTrack } from '../types';

const AiRecPanel = lazy(() => import('./aiRecPanel').then((m) => ({ default: m.AiRecPanel })));

// Floating action button that summons CouchMate, the personal movie
// assistant. Rendered only for signed-in users when Gemini is configured
// (see App.tsx).
export function AiRecFab({
  buddyEnabled = false,
  buddyContext,
  saved,
  onToggleSaved,
  onPlayMix,
  onShuffleMix,
  onRequestTitle,
}: {
  /** When the user opted into CouchMate, the panel opens on the chat tab. */
  buddyEnabled?: boolean;
  /** Title context from the page the user is on, if any. */
  buddyContext?: BuddyChatContext;
  saved: Set<string>;
  onToggleSaved: (card: HubCard) => void;
  onPlayMix: (tracks: WatchTrack[]) => void;
  onShuffleMix: (tracks: WatchTrack[]) => void;
  onRequestTitle: (title: RequestTitle) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="ai-fab"
        aria-label="CouchMate — your movie assistant"
        title="CouchMate — your movie assistant"
        onClick={() => setOpen(true)}
      >
        <CouchMateIcon />
      </button>
      {open && (
        <Suspense fallback={null}>
          <AiRecPanel open={open} onClose={() => setOpen(false)} buddyEnabled={buddyEnabled} buddyContext={buddyContext} saved={saved} onToggleSaved={onToggleSaved} onRequestTitle={onRequestTitle} onPlayMix={onPlayMix} onShuffleMix={onShuffleMix} />
        </Suspense>
      )}
    </>
  );
}
