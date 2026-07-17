import { ChartIcon, FilmIcon, MusicIcon, PlayIcon, TvIcon, UserIcon } from '../icons';
import { localAppHref } from '../navigation';
import type { StatsHistoryItem, StatsResponse, StatsTitle, User } from '../types';
import { ErrorPanel, LoadingRows } from './common';

function appHref(url: string): string {
  if (url.startsWith('/watch/')) return url.replace(/^\/watch\//, '/app/watch/');
  return localAppHref(url) || url;
}

function durationLabel(hours: number, mins: number): string {
  if (hours && mins) return `${hours}h ${mins}m`;
  if (hours) return `${hours}h`;
  return `${mins}m`;
}

function heatLevel(count: number, max: number): number {
  if (!count || !max) return 0;
  return Math.max(1, Math.min(4, Math.ceil((count / max) * 4)));
}

function titlePoster(title: StatsTitle | null, label: string) {
  if (!title) return null;
  return (
    <a className="stats-title-card" href={appHref(title.url)}>
      <img src={title.poster} alt="" loading="lazy" decoding="async" />
      <span>{label}</span>
      <strong>{title.title}</strong>
      <small>{[title.year, title.media_kind].filter(Boolean).join(' · ')}</small>
    </a>
  );
}

export function StatsPage({
  user,
  data,
  loading,
  error,
  onSignIn,
}: {
  user: User | null;
  data: StatsResponse | null;
  loading: boolean;
  error: string;
  onSignIn: () => void;
}) {
  if (!user) {
    return (
      <main className="stats-main">
        <div className="empty-state">
          <ChartIcon />
          <strong>Sign in to view your stats</strong>
          <button type="button" className="primary-action" onClick={onSignIn}>Sign in</button>
        </div>
      </main>
    );
  }

  if (loading) {
    return (
      <main className="stats-main">
        <LoadingRows variant="detail" />
      </main>
    );
  }

  if (error) {
    return (
      <main className="stats-main">
        <ErrorPanel message={error} />
      </main>
    );
  }

  if (!data) return null;

  if (!data.has_activity) {
    return (
      <main className="stats-main">
        <div className="empty-state">
          <ChartIcon />
          <strong>No activity yet</strong>
          <span>Start watching or listening to see your personal stats.</span>
        </div>
      </main>
    );
  }

  const maxHeat = Math.max(1, ...data.heatmap.map((cell) => cell.count));
  const audioPct = data.n_audio + data.n_video ? Math.round((data.n_audio / (data.n_audio + data.n_video)) * 100) : 0;
  const videoPct = data.n_audio + data.n_video ? 100 - audioPct : 0;

  const equivParts = [
    data.equiv_movies > 0 ? `≈ ${data.equiv_movies} movies` : null,
    data.equiv_flights > 0 ? `${data.equiv_flights} flights` : null,
  ].filter(Boolean);

  return (
    <main className="stats-main">
      {/* ── Hero ── */}
      <section className="stats-hero">
        <div className="stats-hero-copy">
          <p className="eyebrow">{data.personality || 'Stats'}</p>
          <h1>{durationLabel(data.total_hours, data.total_mins)}</h1>
          <p>
            {data.total_plays.toLocaleString()} completed play{data.total_plays === 1 ? '' : 's'} across {data.total_titles.toLocaleString()} title{data.total_titles === 1 ? '' : 's'}
            {data.in_progress > 0 && ` · ${data.in_progress} in progress`}
          </p>
          {equivParts.length > 0 && (
            <p className="stats-equiv">{equivParts.join(' · ')}</p>
          )}
          <div className="stats-hero-actions">
            <span><PlayIcon /> {data.completion}% completion</span>
            <span><ChartIcon /> {data.active_days} active days</span>
          </div>
        </div>
        {titlePoster(data.top_title, data.top_title_label)}
      </section>

      {/* ── Summary cards ── */}
      <section className="stats-grid" aria-label="Summary">
        <article className="stat-card">
          <FilmIcon />
          <span>Video</span>
          <strong>{durationLabel(data.video_hours, data.video_mins)}</strong>
          <small>{videoPct}% of plays</small>
        </article>
        <article className="stat-card">
          <MusicIcon />
          <span>Music</span>
          <strong>{durationLabel(data.audio_hours, data.audio_mins)}</strong>
          <small>{audioPct}% of plays</small>
        </article>
        <article className="stat-card">
          <UserIcon />
          <span>Current streak</span>
          <strong>{data.current_streak} days</strong>
          <small>Longest {data.longest_streak}</small>
        </article>
        <article className="stat-card">
          <ChartIcon />
          <span>Finished</span>
          <strong>{data.finished}</strong>
          <small>of {data.started} started</small>
        </article>
      </section>

      {/* ── Watch history ── */}
      {(data.recent_history?.length ?? 0) > 0 && (
        <section className="stats-history-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">History</p>
              <h2>Recently played</h2>
            </div>
          </div>
          <div className="stats-history-row">
            {data.recent_history.map((item: StatsHistoryItem) => (
              <a key={`${item.url}:${item.watched_at}`} className="stats-history-card" href={appHref(item.url)}>
                <div className="stats-history-poster audio-art-wrap">
                  {item.media_kind === 'audio' ? <MusicIcon /> : <TvIcon />}
                  <img src={item.poster} alt="" loading="lazy" decoding="async" onError={(e) => { e.currentTarget.hidden = true; }} />
                </div>
                <span className="stats-history-title">{item.title}</span>
                {item.watched_at && <small>{item.watched_at}</small>}
              </a>
            ))}
          </div>
        </section>
      )}

      <section className="stats-panels">
        {/* ── Weekly rhythm ── */}
        <article className="stats-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Rhythm</p>
              <h2>Weekly activity</h2>
            </div>
            <span className="stats-panel-hint">Best day: {data.best_day}</span>
          </div>
          <div className="dow-bars">
            {data.dow_bars.map((bar) => (
              <div key={bar.label} className="dow-row">
                <span>{bar.label}</span>
                <i><b style={{ width: `${bar.pct}%` }} /></i>
                <strong>{bar.count}</strong>
              </div>
            ))}
          </div>
        </article>

        {/* ── Heatmap ── */}
        <article className="stats-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Last 12 weeks</p>
              <h2>Activity map</h2>
            </div>
            <span className="stats-panel-hint">{data.tod_emoji} {data.tod_label} person</span>
          </div>
          <div className="heatmap" role="img" aria-label="Activity heatmap" aria-description={`${data.active_days} active days in the last 12 weeks`}>
            {data.heatmap.map((cell) => (
              <span
                key={cell.date}
                className={`heat-${heatLevel(cell.count, maxHeat)}`}
                title={cell.count ? `${cell.date}: ${cell.count} play${cell.count === 1 ? '' : 's'}` : cell.date}
                aria-label={cell.count ? `${cell.date}: ${cell.count}` : undefined}
              />
            ))}
          </div>
        </article>

        {/* ── Most replayed ── */}
        {data.most_replayed.length > 0 && (
          <article className="stats-panel stats-list-panel">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Replay</p>
                <h2>Most replayed</h2>
              </div>
            </div>
            <div className="stats-title-list">
              {data.most_replayed.map((title) => (
                <a key={`${title.url}:${title.count}`} href={appHref(title.url)}>
                  <img src={title.poster} alt="" loading="lazy" decoding="async" />
                  <span>
                    <strong>{title.title}</strong>
                    <small>{title.count || 0} plays</small>
                  </span>
                </a>
              ))}
            </div>
          </article>
        )}

        {/* ── Taste ── */}
        <article className="stats-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Taste</p>
              <h2>Top picks</h2>
            </div>
          </div>

          {data.top_genres.length > 0 && (
            <div className="stats-taste-row">
              <span className="stats-taste-label">Genres</span>
              <div className="stats-tags">
                {data.top_genres.map(([name, count]) => (
                  <span key={name}>{name}<small>{count}</small></span>
                ))}
              </div>
            </div>
          )}

          {data.top_artists.length > 0 && (
            <div className="stats-taste-row">
              <span className="stats-taste-label">Artists</span>
              <div className="stats-tags">
                {data.top_artists.map(([name, count]) => (
                  <span key={name}>{name}<small>{count}</small></span>
                ))}
              </div>
            </div>
          )}

          <div className="stats-taste-facts">
            {data.top_director && (
              <span>
                <small>Director</small>
                <strong>{data.top_director[0]}</strong>
                <small>{data.top_director[1]} films</small>
              </span>
            )}
            {data.best_month && (
              <span>
                <small>Best month</small>
                <strong>{data.best_month[0]}</strong>
                <small>{data.best_month[1]} plays</small>
              </span>
            )}
          </div>
        </article>
      </section>
    </main>
  );
}
