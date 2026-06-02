import { useEffect, useState } from 'react';
import { fetchAdminTrendingGaps, refreshAdminTrendingGaps } from '../api';
import { ChevronRightIcon, FilmIcon } from '../icons';
import type { User } from '../types';
import { ErrorPanel, LoadingRows } from './common';

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
          <button type="button" className="primary-action" onClick={onSignIn}>Sign in</button>
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
          <button type="button" className="secondary-action" onClick={handleRefresh} disabled={refreshing}>
            <span>{refreshing ? 'Refreshing…' : '↺ Refresh cache'}</span>
          </button>
          <a className="secondary-action" href="/app/admin">
            <ChevronRightIcon />
            <span>Admin console</span>
          </a>
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
              <a
                key={g.tmdb_url}
                href={g.tmdb_url}
                target="_blank"
                rel="noopener noreferrer"
                className="trending-card"
              >
                <div className="trending-card-art">
                  {g.poster ? (
                    <img src={g.poster} alt="" loading="lazy" />
                  ) : (
                    <div className="trending-card-placeholder"><FilmIcon /></div>
                  )}
                  <span className={`trending-kind-badge ${g.kind === 'tv' ? 'tv' : 'movie'}`}>{g.kind}</span>
                  {g.vote && <span className="trending-vote">★ {g.vote}</span>}
                </div>
                <div className="trending-card-copy">
                  <p className="trending-card-title">
                    {g.title}
                    {g.year && <span className="trending-card-year"> ({g.year})</span>}
                  </p>
                  <p className="trending-card-link">View on TMDB ↗</p>
                </div>
              </a>
            ))}
          </div>
        </>
      )}
    </main>
  );
}
