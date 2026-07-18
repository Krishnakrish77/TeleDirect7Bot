import { useEffect, useState } from 'react';
import { fetchAdminTrendingGaps, refreshAdminTrendingGaps } from '../api';
import { ChevronRightIcon, FilmIcon } from '../icons';
import type { User } from '../types';
import { ErrorPanel, LoadingRows } from './common';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';

interface TrendingGap {
  title: string;
  year: string;
  kind: string;
  poster: string;
  vote: string;
  tmdb_url: string;
}

export function AdminTrendingGaps({ user, onSignIn }: { user: User | null; onSignIn: () => void }) {
  const [gaps, setGaps] = useState<TrendingGap[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const load = (signal?: AbortSignal) => {
    setLoading(true);
    setError('');
    fetchAdminTrendingGaps(signal)
      .then((res) => setGaps(res.gaps as TrendingGap[]))
      .catch((err: Error) => { if (!signal?.aborted) setError(err.message); })
      .finally(() => { if (!signal?.aborted) setLoading(false); });
  };

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, []);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refreshAdminTrendingGaps();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Refresh failed');
    } finally {
      setRefreshing(false);
    }
  };

  if (!user || !user.is_admin) {
    return (
      <main className="admin-main">
        <div className="empty-state">
          <strong>{user ? 'Admin access required' : 'Sign in required'}</strong>
          <Button type="button" onClick={onSignIn}>Sign in</Button>
        </div>
      </main>
    );
  }

  return (
    <main className="admin-main">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Operations</p>
          <h1>Trending Gaps</h1>
          <p style={{ color: 'var(--muted)', fontSize: '0.9rem' }}>
            TMDB trending &amp; popular titles not in the catalogue. Refreshes every 24h.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <Button type="button" variant="secondary" onClick={handleRefresh} disabled={refreshing}>{refreshing ? 'Refreshing…' : 'Refresh cache'}</Button>
          <Button asChild variant="secondary">
            <a href="/app/admin"><ChevronRightIcon />Admin console</a>
          </Button>
        </div>
      </div>

      {error && <ErrorPanel message={error} />}
      {loading && <LoadingRows />}

      {!loading && !error && gaps.length === 0 && (
        <div className="empty-state">
          <FilmIcon />
          <strong>No gaps found</strong>
          <span>All TMDB trending titles are already in the catalogue, or TMDB is not configured.</span>
        </div>
      )}

      {!loading && gaps.length > 0 && (
        <>
          <p style={{ color: 'var(--muted)', fontSize: '0.82rem' }}>
            {gaps.length} titles trending on TMDB but not in the library — ranked by popularity.
          </p>
          <div className="trending-grid">
            {gaps.map((g) => (
              <Card
                key={g.tmdb_url}
                className="trending-card"
              >
                <a href={g.tmdb_url} target="_blank" rel="noopener noreferrer" className="trending-card-link-wrap">
                  <div className="trending-card-art">
                    {g.poster ? (
                      <img src={g.poster} alt="" loading="lazy" />
                    ) : (
                      <div className="trending-card-placeholder"><FilmIcon /></div>
                    )}
                    <Badge className={`trending-kind-badge ${g.kind === 'tv' ? 'tv' : 'movie'}`} variant="muted">{g.kind}</Badge>
                    {g.vote && <Badge className="trending-vote">★ {g.vote}</Badge>}
                  </div>
                  <CardContent className="trending-card-copy">
                    <p className="trending-card-title">
                      {g.title}
                      {g.year && <span className="trending-card-year"> ({g.year})</span>}
                    </p>
                    <p className="trending-card-link">View on TMDB ↗</p>
                  </CardContent>
                </a>
              </Card>
            ))}
          </div>
        </>
      )}
    </main>
  );
}
