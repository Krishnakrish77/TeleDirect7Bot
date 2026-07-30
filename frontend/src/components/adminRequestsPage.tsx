import { useEffect, useState } from 'react';
import { fetchAdminRequests, updateAdminRequest } from '../api';
import { CheckIcon, FilmIcon, TvIcon } from '../icons';
import type { AdminMediaRequest, RequestStatus, User } from '../types';
import { tmdbImageUrl } from '../utils/tmdb';
import { ErrorPanel, LoadingRows } from './common';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';

const labels: Record<RequestStatus, string> = { pending: 'Pending', planned: 'Planned', partial: 'Partially available', available: 'Available', declined: 'Not planned', cancelled: 'Cancelled' };
const variant = (state: RequestStatus) => state === 'available' ? 'success' : state === 'declined' ? 'destructive' : state === 'pending' ? 'muted' : 'default';

export function AdminRequestsPage({ user, onSignIn }: { user: User | null | undefined; onSignIn: () => void }) {
  const [items, setItems] = useState<AdminMediaRequest[]>([]);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState('');
  const reload = () => {
    if (!user?.is_admin) { setLoading(false); return; }
    setLoading(true); setError('');
    void fetchAdminRequests(status).then(setItems).catch((err) => setError(err instanceof Error ? err.message : 'Unable to load requests.')).finally(() => setLoading(false));
  };
  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [user?.is_admin, status]);
  const update = async (item: AdminMediaRequest, state: 'planned' | 'declined' | 'available') => {
    const key = `${item.kind}:${item.tmdbId}:${state}`;
    setBusy(key);
    try { await updateAdminRequest(item.tmdbId, item.kind, state, notes[`${item.kind}:${item.tmdbId}`] || ''); setNotes((current) => ({ ...current, [`${item.kind}:${item.tmdbId}`]: '' })); reload(); } catch (err) { setError(err instanceof Error ? err.message : 'Could not update this request.'); } finally { setBusy(''); }
  };
  if (!user?.is_admin) return <main className="admin-page"><section className="admin-empty"><h1>Requests</h1><p>Sign in with an admin account to manage library requests.</p><Button onClick={onSignIn}>Sign in</Button></section></main>;
  return <main className="admin-page admin-requests-page"><section className="admin-page-hero"><div><p className="eyebrow">Library demand</p><h1>Requests</h1><p>Review exactly what people want, plan it, and let enrichment mark it available when the matching title arrives.</p></div><div className="admin-request-controls"><Select value={status || 'all'} onValueChange={(value) => setStatus(value === 'all' ? '' : value)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All statuses</SelectItem>{(['pending', 'planned', 'partial', 'available', 'declined'] as const).map((value) => <SelectItem key={value} value={value}>{labels[value]}</SelectItem>)}</SelectContent></Select></div></section>
    {loading ? <LoadingRows variant="grid" /> : error ? <ErrorPanel message={error} /> : items.length === 0 ? <section className="requests-empty"><CheckIcon /><h2>No requests here</h2><p>New title requests will appear in this queue.</p></section> : <div className="admin-request-list">{items.map((item) => <article className="admin-request-card" key={`${item.kind}:${item.tmdbId}`}>
      {item.posterPath ? <img src={tmdbImageUrl(item.posterPath, 'w342')} alt="" /> : <span className="request-card-art">{item.kind === 'tv' ? <TvIcon /> : <FilmIcon />}</span>}
      <div className="admin-request-card-main"><div className="admin-request-meta"><Badge variant={variant(item.status)}>{labels[item.status]}</Badge><span>{item.requestCount} request{item.requestCount === 1 ? '' : 's'}</span></div><h2>{item.title}{item.year ? ` (${item.year})` : ''}</h2><p>{item.kind === 'tv' ? `Seasons requested: ${item.requestedSeasons.map((value) => `S${value}`).join(', ')}` : 'Movie request'}{item.inLibrary ? ' · Matching title is in the library' : ''}</p>{item.note && <p className="request-note">{item.note}</p>}</div>
      {['pending', 'planned', 'partial'].includes(item.status) && <div className="admin-request-actions"><Input value={notes[`${item.kind}:${item.tmdbId}`] || ''} onChange={(event) => setNotes((current) => ({ ...current, [`${item.kind}:${item.tmdbId}`]: event.target.value }))} placeholder="Optional note" aria-label={`Note for ${item.title}`} /><div><Button size="sm" variant="secondary" disabled={Boolean(busy)} onClick={() => void update(item, 'planned')}>Plan</Button><Button size="sm" disabled={Boolean(busy)} onClick={() => void update(item, 'available')}>Available</Button><Button size="sm" variant="destructive" disabled={Boolean(busy)} onClick={() => void update(item, 'declined')}>Decline</Button></div></div>}
    </article>)}</div>}
  </main>;
}
