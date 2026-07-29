import { useEffect, useRef, useState } from 'react';
import { ApiError, createMediaRequest, fetchRequestTitle, searchRequestTitles } from '../api';
import { CheckIcon, FilmIcon, SearchIcon, TvIcon, XIcon } from '../icons';
import type { RequestTitle } from '../types';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from './ui/dialog';
import { Input } from './ui/input';

function titleYear(item: RequestTitle) {
  return item.year ? `${item.title} (${item.year})` : item.title;
}

export function RequestTitleDialog({
  open,
  onOpenChange,
  seed = null,
  onCompleted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  seed?: RequestTitle | null;
  onCompleted?: () => void;
}) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<RequestTitle[]>([]);
  const [selected, setSelected] = useState<RequestTitle | null>(null);
  const [seasons, setSeasons] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const searchRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!open) return;
    setMessage('');
    setQuery(seed ? titleYear(seed) : '');
    setResults(seed ? [seed] : []);
    setSelected(null);
    setSeasons([]);
    if (seed) void choose(seed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, seed?.tmdbId, seed?.kind]);

  useEffect(() => () => searchRef.current?.abort(), []);

  useEffect(() => {
    if (!open || selected || query.trim().length < 2) return;
    const timer = window.setTimeout(() => {
      searchRef.current?.abort();
      const controller = new AbortController();
      searchRef.current = controller;
      setLoading(true);
      searchRequestTitles(query.trim(), controller.signal)
        .then(setResults)
        .catch((err) => { if (!controller.signal.aborted) setMessage(err instanceof Error ? err.message : 'Could not search titles.'); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [open, query, selected]);

  const choose = async (item: RequestTitle) => {
    setSelected(item);
    setResults([]);
    setMessage('');
    setLoading(true);
    try {
      const detail = await fetchRequestTitle(item.tmdbId, item.kind);
      setSelected(detail);
      setSeasons((detail.seasons || []).map((season) => season.number).filter((number) => !(detail.availableSeasons || []).includes(number)));
    } catch (err) {
      setSelected(null);
      setMessage(err instanceof Error ? err.message : 'Could not load this title.');
    } finally {
      setLoading(false);
    }
  };

  const submit = async () => {
    if (!selected || saving) return;
    setSaving(true);
    setMessage('');
    try {
      const result = await createMediaRequest({ tmdbId: selected.tmdbId, kind: selected.kind, seasons });
      setMessage(result.duplicate ? 'You already requested this title.' : 'Request sent. We’ll update you here when it changes.');
      onCompleted?.();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not save your request.');
    } finally {
      setSaving(false);
    }
  };

  const selectSeason = (number: number, checked: boolean) => {
    setSeasons((current) => checked ? [...new Set([...current, number])].sort((a, b) => a - b) : current.filter((value) => value !== number));
  };

  const alreadyAvailable = selected?.kind === 'movie'
    ? Boolean(selected.inLibrary)
    : Boolean(selected && selected.seasons?.length && selected.seasons.every((season) => (selected.availableSeasons || []).includes(season.number)));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showOverlay className="request-dialog" aria-describedby="request-title-help">
        <div className="request-dialog-head">
          <div>
            <p className="eyebrow">Library requests</p>
            <DialogTitle asChild><h2>Request a title</h2></DialogTitle>
            <DialogDescription id="request-title-help">Pick the exact movie or series. We’ll keep its status in your requests.</DialogDescription>
          </div>
          <DialogClose asChild><Button variant="ghost" size="icon-sm" aria-label="Close"><XIcon /></Button></DialogClose>
        </div>

        {!selected ? <>
          <div className="request-search"><SearchIcon /><Input autoFocus value={query} onChange={(event) => { setQuery(event.target.value); setMessage(''); }} placeholder="Search movies and series" /></div>
          {loading && <p className="request-dialog-state">Searching TMDB…</p>}
          {!loading && query.trim().length >= 2 && results.length === 0 && <p className="request-dialog-state">No matching titles yet.</p>}
          <div className="request-results">
            {results.map((item) => <button className="request-result" type="button" key={`${item.kind}:${item.tmdbId}`} onClick={() => void choose(item)}>
              {item.posterUrl ? <img src={item.posterUrl} alt="" /> : <span className="request-result-art">{item.kind === 'tv' ? <TvIcon /> : <FilmIcon />}</span>}
              <span><strong>{titleYear(item)}</strong><small>{item.kind === 'tv' ? 'Series' : 'Movie'}</small>{item.inLibrary && <Badge variant="success">In your library</Badge>}</span>
            </button>)}
          </div>
        </> : <>
          <div className="request-selected">
            {selected.posterUrl && <img src={selected.posterUrl} alt="" />}
            <div><Badge variant="muted">{selected.kind === 'tv' ? 'Series' : 'Movie'}</Badge><h3>{titleYear(selected)}</h3>{selected.overview && <p>{selected.overview}</p>}</div>
            <Button type="button" variant="ghost" size="sm" onClick={() => { setSelected(null); setResults([]); setQuery(''); }}>Change</Button>
          </div>
          {selected.kind === 'tv' && <fieldset className="request-seasons"><legend>Seasons to add</legend><p>Only missing seasons are selected by default.</p><div>{(selected.seasons || []).map((season) => {
            const present = (selected.availableSeasons || []).includes(season.number);
            return <label key={season.number} className={present ? 'is-present' : ''}><Checkbox checked={present || seasons.includes(season.number)} disabled={present} onCheckedChange={(checked) => selectSeason(season.number, checked === true)} /> <span>{season.name}<small>{present ? 'Already in your library' : `${season.episodeCount || '—'} episodes`}</small></span></label>;
          })}</div></fieldset>}
          {alreadyAvailable ? <p className="request-dialog-state success"><CheckIcon /> This title is already available in your library.</p> : <div className="request-dialog-actions"><Button type="button" onClick={() => void submit()} disabled={saving || (selected.kind === 'tv' && seasons.length === 0)}>{saving ? 'Sending…' : 'Send request'}</Button></div>}
        </>}
        {message && <p className={`request-dialog-state${message.startsWith('Request') || message.startsWith('You already') ? ' success' : ''}`}>{message}</p>}
      </DialogContent>
    </Dialog>
  );
}
