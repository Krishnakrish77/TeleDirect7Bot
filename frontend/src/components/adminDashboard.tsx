import { useEffect, useState } from 'react';
import { fetchAdminDashboard } from '../api';
import { ChevronRightIcon } from '../icons';
import type { AdminDashboardResponse, User } from '../types';
import { ErrorPanel, LoadingRows } from './common';

function StatCard({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="admin-panel dash-card">
      <p className="eyebrow">{label}</p>
      {children}
    </div>
  );
}

function StatRow({ label, value, warn }: { label: string; value: string | number; warn?: boolean }) {
  return (
    <div className={`dash-stat-row${warn ? ' warn' : ''}`}>
      <span>{label}</span>
      <span>{value}</span>
    </div>
  );
}

export function AdminDashboard({ user, onSignIn }: { user: User | null; onSignIn: () => void }) {
  const [data, setData] = useState<AdminDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetchAdminDashboard(controller.signal)
      .then(setData)
      .catch((err: Error) => { if (!controller.signal.aborted) setError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

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
          <h1>Dashboard</h1>
          {data && <p style={{ color: 'var(--muted)', fontSize: '0.9rem' }}>{data.total.toLocaleString()} items · {data.total_size_label}</p>}
        </div>
        <a className="secondary-action" href="/app/admin">
          <ChevronRightIcon />
          <span>Admin console</span>
        </a>
      </div>

      {loading && !data && <LoadingRows variant="detail" />}
      {error && <ErrorPanel message={error} />}

      {data && (
        <>
          <div className="dash-grid-3">
            <StatCard label="Composition">
              <StatRow label="Series episodes" value={data.kinds.series_episodes.toLocaleString()} />
              <StatRow label="Movies" value={data.kinds.movies.toLocaleString()} />
              <StatRow label="Standalone" value={data.kinds.standalone.toLocaleString()} />
              {data.audio_count > 0 && <StatRow label="Audio tracks" value={`${data.audio_count.toLocaleString()} · ${data.album_count} albums`} />}
            </StatCard>

            <StatCard label="Storage by quality">
              {data.storage_by_quality.map((q) => (
                <div key={q.quality} className="dash-stat-row">
                  <span>{q.quality} <span style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>· {(data.quality_counts[q.quality] || 0)} items</span></span>
                  <span>{q.label}</span>
                </div>
              ))}
              {!data.storage_by_quality.length && <p style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>No data yet.</p>}
            </StatCard>

            <StatCard label="Storage by codec">
              {data.storage_by_codec.map((c) => (
                <div key={c.codec} className="dash-stat-row">
                  <span style={{ color: c.codec === 'not probed' || c.codec === 'unknown' ? 'var(--muted)' : undefined }}>{c.codec}</span>
                  <span>{c.label}</span>
                </div>
              ))}
              {!data.storage_by_codec.length && <p style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>Run Probe codecs to populate.</p>}
            </StatCard>
          </div>

          <div className="dash-grid-3">
            <StatCard label="TMDB enrichment">
              <StatRow label="Enriched" value={data.enrichment.enriched} />
              <StatRow label="Attempted, no match" value={data.enrichment.attempted_no_match} warn={data.enrichment.attempted_no_match > 0} />
              <StatRow label="Never attempted" value={data.enrichment.never_attempted} warn={data.enrichment.never_attempted > 0} />
            </StatCard>

            <StatCard label="Codec compatibility">
              <StatRow label="Browser-playable" value={data.codec_health.probed_playable} />
              <StatRow label="Needs native player" value={data.codec_health.probed_unplayable} warn={data.codec_health.probed_unplayable > 0} />
              <StatRow label="Not probed yet" value={data.codec_health.never_probed} warn={data.codec_health.never_probed > 0} />
            </StatCard>

            <StatCard label="Top genres">
              <div className="dash-chips">
                {data.top_genres.map(([g, n]) => (
                  <span key={g} className="dash-chip">{g} · {n}</span>
                ))}
                {!data.top_genres.length && <p style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>Run Enrich (TMDB) to populate.</p>}
              </div>
            </StatCard>
          </div>

          <div className="admin-panel">
            <p className="eyebrow" style={{ marginBottom: '0.6rem' }}>Issues to clean up</p>
            <div className="dash-grid-4">
              {[
                { label: 'Duplicates', value: data.duplicate_extras ? `${data.duplicate_extras} extras · ${data.duplicate_groups} files` : 'none', filter: 'duplicates', warn: data.duplicate_extras > 0 },
                { label: 'Missing poster', value: data.missing_poster, filter: 'no-poster', warn: data.missing_poster > 0 },
                { label: 'Missing thumbnail', value: data.missing_thumb, filter: 'no-thumb', warn: data.missing_thumb > 0 },
                { label: 'Unenriched', value: data.enrichment.never_attempted, filter: 'unenriched', warn: data.enrichment.never_attempted > 0 },
              ].map((item) => (
                <a key={item.filter} className={`dash-issue${item.warn ? ' warn' : ''}`} href={`/app/admin?filter=${item.filter}`}>
                  <span className="dash-issue-label">{item.label}</span>
                  <span>{item.value}</span>
                </a>
              ))}
            </div>
          </div>

          <div className="dash-grid-2">
            <StatCard label="Top series">
              {data.top_series.map((s) => (
                <div key={s.key} className="dash-stat-row">
                  <a href={`/series/${s.key}`} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--brand)' }}>{s.title}</a>
                  <span style={{ color: 'var(--muted)' }}>{s.count} ep</span>
                </div>
              ))}
              {!data.top_series.length && <p style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>No series yet.</p>}
            </StatCard>

            <StatCard label="Year distribution">
              {data.year_distribution.map((y) => (
                <div key={y.decade} className="dash-year-row">
                  <span className="dash-year-label">{y.decade}s</span>
                  <div className="dash-year-bar">
                    <div style={{ width: `${data.year_distribution_max ? Math.round(y.count / data.year_distribution_max * 100) : 0}%` }} />
                  </div>
                  <span className="dash-year-count">{y.count}</span>
                </div>
              ))}
              {!data.year_distribution.length && <p style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>No year metadata yet.</p>}
            </StatCard>
          </div>

          <div className="dash-grid-2">
            <StatCard label="Recent additions">
              {data.recent_additions.map((it) => (
                <div key={it.message_id} className="dash-stat-row">
                  <a href={it.watchHref} style={{ color: 'inherit', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {it.title}{it.year ? ` (${it.year})` : ''}
                    {it.season != null && it.episode != null && (
                      <span style={{ color: 'var(--muted)', fontSize: '0.75rem', marginLeft: '0.3rem' }}>
                        S{String(it.season).padStart(2, '0')}E{String(it.episode).padStart(2, '0')}
                      </span>
                    )}
                  </a>
                  <span style={{ color: 'var(--muted)', fontSize: '0.75rem', flexShrink: 0 }}>
                    {[it.quality, it.fileSizeLabel].filter(Boolean).join(' · ')}
                  </span>
                </div>
              ))}
            </StatCard>

            <StatCard label="Largest items">
              {data.largest_items.map((it) => (
                <div key={it.message_id} className="dash-stat-row">
                  <a href={it.watchHref} style={{ color: 'inherit', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {it.title}{it.year ? ` (${it.year})` : ''}
                  </a>
                  <span style={{ color: 'var(--muted)', fontSize: '0.75rem', flexShrink: 0 }}>
                    {[it.quality, it.fileSizeLabel].filter(Boolean).join(' · ')}
                  </span>
                </div>
              ))}
            </StatCard>
          </div>
        </>
      )}
    </main>
  );
}
