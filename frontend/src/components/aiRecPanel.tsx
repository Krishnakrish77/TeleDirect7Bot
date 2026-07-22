import { useEffect, useRef, useState } from 'react';
import { askAiRecommendations, dismissRecommendation, fetchAiRecommendations, trackRecommendationEvents } from '../api';
import type { AiRecItem, HubCard } from '../types';
import { SparkleIcon, XIcon } from '../icons';
import { MediaCard } from './mediaCard';
import { LoadingRows } from './common';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Dialog, DialogClose, DialogContent, DialogTitle } from './ui/dialog';

export function AiRecPanel({
  open,
  onClose,
  saved,
  onToggleSaved,
}: {
  open: boolean;
  onClose: () => void;
  saved: Set<string>;
  onToggleSaved: (card: HubCard) => void;
}) {
  const [items, setItems] = useState<AiRecItem[]>([]);
  const [message, setMessage] = useState('');
  const [coldStart, setColdStart] = useState(false);
  const [loading, setLoading] = useState(true);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const ctrl = useRef<AbortController | null>(null);
  const trackedImpressions = useRef<Set<string>>(new Set());

  const isAbort = (err: unknown) => err instanceof DOMException && err.name === 'AbortError';

  const load = (refresh = false) => {
    ctrl.current?.abort();
    const controller = new AbortController();
    ctrl.current = controller;
    setLoading(true);
    setError('');
    fetchAiRecommendations(refresh, controller.signal)
      .then((res) => {
        setItems(res.items || []);
        setMessage(res.message || '');
        setColdStart(Boolean(res.coldStart));
      })
      .catch((err) => { if (!isAbort(err)) setError('Could not load recommendations right now.'); })
      .finally(() => { if (ctrl.current === controller) setLoading(false); });
  };

  useEffect(() => {
    load(false);
    return () => ctrl.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const q = query.trim();
    if (!q || asking) return;
    ctrl.current?.abort();
    const controller = new AbortController();
    ctrl.current = controller;
    setAsking(true);
    setError('');
    askAiRecommendations(q, controller.signal)
      .then((res) => {
        setItems(res.items || []);
        setMessage(res.message || '');
        setColdStart(false);
      })
      .catch((err) => { if (!isAbort(err)) setError('Could not process that request.'); })
      .finally(() => { if (ctrl.current === controller) setAsking(false); });
  };

  const busy = loading || asking;
  const comfort = items.filter((item) => item.bucket !== 'discovery');
  const discovery = items.filter((item) => item.bucket === 'discovery');
  const split = discovery.length > 0 && comfort.length > 0;
  useEffect(() => {
    if (busy || !items.length) return;
    const unseen = items.flatMap((item, position) => {
      const key = item.href;
      if (trackedImpressions.current.has(key)) return [];
      trackedImpressions.current.add(key);
      return [{
        action: 'impression' as const,
        source: 'ai' as const,
        itemId: item.itemId,
        tmdbId: item.tmdbId,
        tmdbKind: item.tmdbKind,
        shelf: 'AI picks',
        position,
      }];
    });
    trackRecommendationEvents(unseen);
  }, [busy, items]);

  const dismiss = (item: AiRecItem) => {
    if (!item.tmdbId || !item.tmdbKind) return;
    setItems((current) => current.filter((candidate) => candidate.href !== item.href));
    void dismissRecommendation(item.tmdbId, item.tmdbKind);
  };
  const renderCard = (item: AiRecItem, position: number) => (
    <MediaCard
      key={item.href}
      card={item}
      saved={saved.has(item.itemId)}
      onToggleSaved={onToggleSaved}
      dismissMeta={item.tmdbId && item.tmdbKind ? { tmdbId: item.tmdbId, kind: item.tmdbKind } : null}
      onDismiss={(_meta, dismissedItem) => dismiss(dismissedItem as AiRecItem)}
      recommendation={{ source: 'ai', shelf: 'AI picks', position }}
    />
  );

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="ai-rec-panel" aria-describedby={undefined}>
        <div className="ai-rec-head">
          <div className="ai-rec-heading">
            <p className="eyebrow"><SparkleIcon /> For you</p>
            <DialogTitle asChild><h2>AI picks</h2></DialogTitle>
          </div>
          <div className="ai-rec-head-actions">
            <Button type="button" variant="ghost" size="sm" className="text-button" onClick={() => load(true)} disabled={busy}>Refresh</Button>
            <DialogClose asChild><Button type="button" variant="ghost" size="icon-sm" className="icon-button" aria-label="Close"><XIcon /></Button></DialogClose>
          </div>
        </div>

        {message && !busy && <p className="ai-rec-message">{message}</p>}

        {/* Any card click navigates via its link — close the panel so it doesn't cover the new page. */}
        <div className="ai-rec-body" onClickCapture={(event) => { if ((event.target as HTMLElement).closest('a')) onClose(); }}>
          {busy ? (
            <LoadingRows variant="grid" />
          ) : error ? (
            <p className="ai-rec-empty">{error}</p>
          ) : items.length === 0 ? (
            <p className="ai-rec-empty">No recommendations yet — keep watching and listening.</p>
          ) : (
            <>
              {coldStart && (
                <p className="ai-rec-note">Still learning your taste — here's what's fresh. The more you watch and listen, the sharper these get.</p>
              )}
              {split ? (
                <>
                  <section className="ai-rec-group">
                    <h3 className="ai-rec-section">Comfort picks</h3>
                    <div className="ai-rec-grid">{comfort.map((item, index) => renderCard(item, index))}</div>
                  </section>
                  <section className="ai-rec-group">
                    <h3 className="ai-rec-section">Discover something new</h3>
                    <div className="ai-rec-grid">{discovery.map((item, index) => renderCard(item, comfort.length + index))}</div>
                  </section>
                </>
              ) : (
                <div className="ai-rec-grid">{items.map((item, index) => renderCard(item, index))}</div>
              )}
            </>
          )}
        </div>

        <form className="ai-rec-ask" onSubmit={submit}>
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Ask for something — 'upbeat', 'like Inception'…"
            disabled={asking}
            aria-label="Ask the recommender"
          />
          <Button type="submit" disabled={asking || !query.trim()}>Ask</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
