import { lazy, Suspense, useState } from 'react';
import { SparkleIcon } from '../icons';
import type { HubCard } from '../types';

const AiRecPanel = lazy(() => import('./aiRecPanel').then((m) => ({ default: m.AiRecPanel })));

// Floating action button that summons the personal AI recommendation agent.
// Rendered only for signed-in users when Gemini is configured (see App.tsx).
export function AiRecFab({ saved, onToggleSaved }: { saved: Set<string>; onToggleSaved: (card: HubCard) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="ai-fab"
        aria-label="AI picks for you"
        title="AI picks for you"
        onClick={() => setOpen(true)}
      >
        <SparkleIcon />
      </button>
      {open && (
        <Suspense fallback={null}>
          <AiRecPanel open={open} onClose={() => setOpen(false)} saved={saved} onToggleSaved={onToggleSaved} />
        </Suspense>
      )}
    </>
  );
}
