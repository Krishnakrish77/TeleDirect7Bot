import { useEffect, useRef, useState } from 'react';
import { dismissRecommendation, streamAiRecommendations, trackRecommendationEvents } from '../api';
import type { AiRecItem, HubCard, RequestTitle } from '../types';
import { FilmIcon, ListPlusIcon, SparkleIcon, TvIcon, XIcon } from '../icons';
import type { WatchTrack } from '../types';
import { AiMixPanel } from './aiMixPanel';
import { MediaCard } from './mediaCard';
import { LoadingRows } from './common';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Dialog, DialogClose, DialogContent, DialogTitle } from './ui/dialog';
import { tmdbImageUrl } from '../utils/tmdb';

const PROGRESS_STEPS = ['Search library', 'Explore matches', 'Curate picks'];

function progressStep(status: string) {
  const text = status.toLowerCase();
  if (text.includes('curating')) return 2;
  if (text.includes('exploring')) return 1;
  return 0;
}

export function AiRecPanel({
  open,
  onClose,
  saved,
  onToggleSaved,
  onPlayMix,
  onShuffleMix,
  onRequestTitle,
}: {
  open: boolean;
  onClose: () => void;
  saved: Set<string>;
  onToggleSaved: (card: HubCard) => void;
  onPlayMix: (tracks: WatchTrack[]) => void;
  onShuffleMix: (tracks: WatchTrack[]) => void;
  onRequestTitle: (title: RequestTitle) => void;
}) {
  const [items, setItems] = useState<AiRecItem[]>([]);
  const [externalItems, setExternalItems] = useState<RequestTitle[]>([]);
  const [message, setMessage] = useState('');
  const [assessment, setAssessment] = useState<{ title: string; verdict: 'likely' | 'maybe' | 'unlikely'; reason: string } | null>(null);
  const [coldStart, setColdStart] = useState(false);
  const [loading, setLoading] = useState(true);
  const [asking, setAsking] = useState(false);
  const [agentStatus, setAgentStatus] = useState('');
  const [agentAction, setAgentAction] = useState<'ask' | 'refresh' | null>(null);
  const [lastAgentInput, setLastAgentInput] = useState<{ query?: string; refresh?: boolean } | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'picks' | 'mix'>('picks');
  const ctrl = useRef<AbortController | null>(null);
  const trackedImpressions = useRef<Set<string>>(new Set());

  const isAbort = (err: unknown) => err instanceof DOMException && err.name === 'AbortError';

  const load = () => {
    ctrl.current?.abort();
    // ``ctrl`` is intentionally shared so an explicit ask cancels a stale
    // initial load (and vice versa). Clear the opposite busy flag at the
    // handoff; otherwise its aborted request cannot safely clear state after
    // the controller has been replaced, leaving the panel stuck loading.
    setAsking(false);
    const controller = new AbortController();
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 30_000);
    ctrl.current = controller;
    setLoading(true);
    setError('');
    setAssessment(null);
    setAgentStatus('Searching your library');
    streamAiRecommendations({ initial: true }, setAgentStatus, controller.signal)
      .then((res) => {
        setItems(res.items || []);
        setExternalItems(res.externalItems || []);
        setMessage(res.message || '');
        setAssessment(res.assessment || null);
        setColdStart(Boolean(res.coldStart));
      })
      .catch((err) => {
        if (timedOut) setError('Recommendations took too long. Please try again.');
        else if (!isAbort(err)) setError('Could not load recommendations right now.');
      })
      .finally(() => {
        window.clearTimeout(timeout);
        if (ctrl.current === controller) {
          setLoading(false);
          setAgentStatus('');
        }
      });
  };

  useEffect(() => {
    load();
    return () => ctrl.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runAgent = (input: { query?: string; refresh?: boolean }) => {
    const isRefresh = Boolean(input.refresh);
    ctrl.current?.abort();
    setLoading(false);
    setAsking(!isRefresh);
    setAgentAction(isRefresh ? 'refresh' : 'ask');
    setAgentStatus('Searching your library');
    setLastAgentInput(input);
    setError('');
    setAssessment(null);
    const controller = new AbortController();
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, 30_000);
    ctrl.current = controller;
    streamAiRecommendations(input, setAgentStatus, controller.signal)
      .then((res) => {
        setItems(res.items || []);
        setExternalItems(res.externalItems || []);
        setMessage(res.message || '');
        setAssessment(res.assessment || null);
        setColdStart(Boolean(res.coldStart));
      })
      .catch((err) => {
        if (timedOut) setError('Recommendations took too long. Please try again.');
        else if (!isAbort(err)) setError(err instanceof Error ? err.message : 'Could not process that request.');
      })
      .finally(() => {
        window.clearTimeout(timeout);
        if (ctrl.current === controller) {
          setAsking(false);
          setAgentAction(null);
          setAgentStatus('');
        }
      });
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const q = query.trim();
    if (!q || asking) return;
    runAgent({ query: q });
  };

  const stop = () => {
    ctrl.current?.abort();
    setLoading(false);
    setAsking(false);
    setAgentAction(null);
    setAgentStatus('');
    setError('Stopped looking for picks.');
  };

  const busy = loading || asking || agentAction === 'refresh';
  const activeProgressStep = progressStep(agentStatus);
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
    <Dialog modal={false} open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className={`ai-rec-panel${mode === 'mix' ? ' ai-rec-panel--mix' : ''}`} showOverlay={false} aria-describedby={undefined}>
        <div className="ai-rec-head">
          <div className="ai-rec-heading">
            <p className="eyebrow"><SparkleIcon /> For you</p>
            <DialogTitle asChild><h2>AI picks</h2></DialogTitle>
          </div>
          <div className="ai-rec-head-actions">
            {mode === 'picks' && <Button type="button" variant="outline" size="sm" className="ai-rec-mix-launch" onClick={() => setMode('mix')}><SparkleIcon /> Mix</Button>}
            {mode === 'picks' && <Button type="button" variant="ghost" size="sm" className="text-button" onClick={() => runAgent({ refresh: true })} disabled={busy}>Refresh</Button>}
            <DialogClose asChild><Button type="button" variant="ghost" size="icon-sm" className="icon-button" aria-label="Close"><XIcon /></Button></DialogClose>
          </div>
        </div>

        {mode === 'mix' ? <AiMixPanel onBack={() => setMode('picks')} onPlay={onPlayMix} onShuffle={onShuffleMix} /> : <>
          {agentStatus ? <section className="ai-rec-progress" role="status" aria-live="polite">
            <div className="ai-rec-progress-top">
              <SparkleIcon className="ai-rec-progress-icon" aria-hidden="true" />
              <div><strong>Finding your next watch</strong><p>{agentStatus}</p></div>
              <Button type="button" variant="ghost" size="sm" className="ai-rec-stop" onClick={stop}>Stop</Button>
            </div>
            <ol className="ai-rec-progress-steps" aria-label="Recommendation progress">
              {PROGRESS_STEPS.map((step, index) => <li key={step} className={index < activeProgressStep ? 'complete' : index === activeProgressStep ? 'active' : ''}>{step}</li>)}
            </ol>
          </section> : message && !busy && <p className="ai-rec-message">{message}</p>}

          {/* Any card click navigates via its link — close the panel so it doesn't cover the new page. */}
          <div className="ai-rec-body" onClickCapture={(event) => { if ((event.target as HTMLElement).closest('a')) onClose(); }}>
            {loading || (asking && !items.length) ? (
              <LoadingRows variant="grid" />
            ) : error && !items.length ? (
              <div className="ai-rec-empty">
                <p>{error}</p>
                <Button type="button" variant="outline" size="sm" onClick={() => lastAgentInput ? runAgent(lastAgentInput) : load()}>Try again</Button>
              </div>
            ) : items.length === 0 ? (
              <p className="ai-rec-empty">No recommendations yet — keep watching and listening.</p>
            ) : (
              <>
                {error && <div className="ai-rec-message" role="alert">
                  {error} <Button type="button" variant="outline" size="sm" onClick={() => lastAgentInput ? runAgent(lastAgentInput) : load()}>Try again</Button>
                </div>}
                {coldStart && (
                  <p className="ai-rec-note">Still learning your taste — here's what's fresh. The more you watch and listen, the sharper these get.</p>
                )}
                {assessment && <section className={`ai-rec-assessment ai-rec-assessment--${assessment.verdict}`} aria-label={`Taste match for ${assessment.title}`}>
                  <span>{assessment.verdict === 'likely' ? 'Likely a fit' : assessment.verdict === 'maybe' ? 'Could be a fit' : 'Probably not your usual pick'}</span>
                  <strong>{assessment.title}</strong>
                  <p>{assessment.reason}</p>
                </section>}
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
                {externalItems.length > 0 && <section className="ai-rec-group ai-rec-external">
                  <div className="ai-rec-external-head"><h3 className="ai-rec-section">Beyond your library</h3><p>Good fits we don’t have yet. Request one if it belongs here.</p></div>
                  <div className="ai-rec-external-grid">{externalItems.map((item) => <article key={`${item.kind}:${item.tmdbId}`} className="ai-request-card">
                    {item.posterPath ? <img src={tmdbImageUrl(item.posterPath, 'w342')} alt="" /> : <span className="ai-request-card-art">{item.kind === 'tv' ? <TvIcon /> : <FilmIcon />}</span>}
                    <div><span>{item.kind === 'tv' ? 'Series' : 'Movie'}</span><h4>{item.title}</h4>{item.year && <small>{item.year}</small>}<p className="ai-request-card-meta">{item.genres?.join(' · ')}{item.runtimeMinutes ? `${item.genres?.length ? ' · ' : ''}${item.runtimeMinutes}m` : ''}{item.tmdbRating ? `${item.genres?.length || item.runtimeMinutes ? ' · ' : ''}TMDB ${item.tmdbRating.toFixed(1)}` : ''}</p>{item.overview && <p className="ai-request-card-overview">{item.overview}</p>}<div className="ai-request-card-actions">{item.tmdbUrl && <a href={item.tmdbUrl} target="_blank" rel="noreferrer">View on TMDB <span aria-hidden="true">↗</span></a>}<Button size="sm" onClick={() => onRequestTitle(item)}><ListPlusIcon /> Request</Button></div></div>
                  </article>)}</div>
                </section>}
              </>
            )}
          </div>

          <form className="ai-rec-ask" onSubmit={submit}>
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Ask for something — 'upbeat', 'like Inception'…"
              disabled={asking || agentAction === 'refresh'}
              aria-label="Ask the recommender"
            />
            <Button type="submit" disabled={busy || !query.trim()}>Ask</Button>
          </form>
        </>}
      </DialogContent>
    </Dialog>
  );
}
