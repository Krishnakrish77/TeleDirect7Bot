import { useEffect, useState } from 'react';
import { cancelMediaRequest, fetchMyRequests } from '../api';
import { CheckIcon, FilmIcon, ListPlusIcon, TvIcon } from '../icons';
import type { MediaRequest, User } from '../types';
import { tmdbImageUrl } from '../utils/tmdb';
import { ErrorPanel, LoadingRows } from './common';
import { RequestTitleDialog } from './requestTitleDialog';
import { Badge } from './ui/badge';
import { Button } from './ui/button';

const statusVariant = (status: MediaRequest['status']) => status === 'available' ? 'success' : status === 'declined' ? 'destructive' : status === 'pending' ? 'muted' : 'default';
const statusLabel = (status: MediaRequest['status']) => ({ pending: 'Pending review', planned: 'Planned', partial: 'Partially available', available: 'Available', declined: 'Not planned', cancelled: 'Cancelled' }[status]);

export function RequestsPage({ user, onSignIn }: { user: User | null | undefined; onSignIn: () => void }) {
  const [items, setItems] = useState<MediaRequest[]>([]);
  const [loading, setLoading] = useState(Boolean(user));
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);

  const reload = () => {
    if (!user) { setItems([]); setLoading(false); return; }
    setLoading(true); setError('');
    void fetchMyRequests().then(setItems).catch((err) => setError(err instanceof Error ? err.message : 'Unable to load requests.')).finally(() => setLoading(false));
  };
  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [user]);

  const cancel = async (item: MediaRequest) => {
    try { await cancelMediaRequest(item.id); reload(); } catch (err) { setError(err instanceof Error ? err.message : 'Could not cancel this request.'); }
  };

  if (!user) return <main className="hub-main requests-page"><section className="requests-hero"><p className="eyebrow">Library requests</p><h1>Want something new?</h1><p>Sign in to request movies and series, then follow their progress here.</p><Button onClick={onSignIn}>Sign in to request</Button></section></main>;
  return <main className="hub-main requests-page">
    <section className="requests-hero"><div><p className="eyebrow">Library requests</p><h1>My requests</h1><p>Tell us what belongs in your library. We’ll keep the status clear from review to availability.</p></div><Button onClick={() => setOpen(true)}><ListPlusIcon /> Request a title</Button></section>
    {loading ? <LoadingRows variant="grid" /> : error ? <ErrorPanel message={error} /> : items.length === 0 ? <section className="requests-empty"><FilmIcon /><h2>Nothing requested yet</h2><p>Search for a movie or series you would like to watch.</p><Button onClick={() => setOpen(true)}>Request a title</Button></section> : <div className="requests-list">{items.map((item) => <article className="request-card" key={item.id}>
      {item.posterPath ? <img src={tmdbImageUrl(item.posterPath, 'w342')} alt="" /> : <span className="request-card-art">{item.kind === 'tv' ? <TvIcon /> : <FilmIcon />}</span>}
      <div><div className="request-card-title"><Badge variant={statusVariant(item.status)}>{statusLabel(item.status)}</Badge><span>{item.kind === 'tv' ? 'Series' : 'Movie'}</span></div><h2>{item.title}{item.year ? ` (${item.year})` : ''}</h2>{item.kind === 'tv' && <p>Requested: {item.requestedSeasons.map((season) => `S${season}`).join(', ')}</p>}{item.status === 'partial' && <p>Available now: {item.availableSeasons.map((season) => `S${season}`).join(', ')}</p>}{item.note && <p className="request-note">{item.note}</p>}</div>
      {['pending', 'planned', 'partial'].includes(item.status) && <Button variant="ghost" size="sm" onClick={() => void cancel(item)}>Cancel</Button>}{item.status === 'available' && <span className="request-card-available"><CheckIcon /> Available</span>}
    </article>)}</div>}
    <RequestTitleDialog open={open} onOpenChange={setOpen} onCompleted={reload} />
  </main>;
}
