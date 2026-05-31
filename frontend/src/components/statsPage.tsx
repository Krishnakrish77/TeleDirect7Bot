import { ChartIcon, FilmIcon, MusicIcon, PlayIcon, UserIcon } from '../icons';
import { localAppHref } from '../navigation';
import type { StatsResponse, StatsTitle, User } from '../types';
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
      <small>{[title.year, title.media_kind].filter(Boolean).join(' - ')}</small>
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

  const maxHeat = Math.max(1, ...data.heatmap.map((cell) => cell.count));
  const audioPct = data.n_audio + data.n_video ? Math.round((data.n_audio / (data.n_audio + data.n_video)) * 100) : 0;
  const videoPct = data.n_audio + data.n_video ? 100 - audioPct : 0;

  return (
    <main className="stats-main">
      <section className="stats-hero">
        <div className="stats-hero-copy">
          <p className="eyebrow">{data.personality || 'Stats'}</p>
          <h1>{durationLabel(data.total_hours, data.total_mins)}</h1>
          <p>{data.total_plays.toLocaleString()} plays across {data.total_titles.toLocaleString()} titles</p>
          <div className="stats-hero-actions">
            <span><PlayIcon /> {data.completion}% completion</span>
            <span><ChartIcon /> {data.active_days} active days</span>
          </div>
        </div>
        {titlePoster(data.top_title, 'Most played')}
      </section>

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
          <strong>{data.current_streak}</strong>
          <small>Longest {data.longest_streak}</small>
        </article>
        <article className="stat-card">
          <ChartIcon />
          <span>Best time</span>
          <strong>{data.tod_emoji} {data.tod_label}</strong>
          <small>{data.best_day}</small>
        </article>
      </section>

      <section className="stats-panels">
        <article className="stats-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Rhythm</p>
              <h2>Weekly activity</h2>
            </div>
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

        <article className="stats-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Last 12 weeks</p>
              <h2>Activity map</h2>
            </div>
          </div>
          <div className="heatmap" aria-label="Activity heatmap">
            {data.heatmap.map((cell) => (
              <span
                key={cell.date}
                className={`heat-${heatLevel(cell.count, maxHeat)}`}
                title={`${cell.date}: ${cell.count}`}
              />
            ))}
          </div>
        </article>

        <article className="stats-panel stats-list-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Replay</p>
              <h2>Most replayed</h2>
            </div>
          </div>
          <div className="stats-title-list">
            {data.most_replayed.length ? data.most_replayed.map((title) => (
              <a key={`${title.url}:${title.count}`} href={appHref(title.url)}>
                <img src={title.poster} alt="" loading="lazy" decoding="async" />
                <span>
                  <strong>{title.title}</strong>
                  <small>{title.count || 0} plays</small>
                </span>
              </a>
            )) : <small>No replay data yet</small>}
          </div>
        </article>

        <article className="stats-panel stats-list-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Taste</p>
              <h2>Top picks</h2>
            </div>
          </div>
          <div className="stats-tags">
            {data.top_genres.map(([name, count]) => <span key={name}>{name}<small>{count}</small></span>)}
            {data.top_artists.map(([name, count]) => <span key={name}>{name}<small>{count}</small></span>)}
            {data.top_director && <span>{data.top_director[0]}<small>{data.top_director[1]}</small></span>}
            {data.best_month && <span>{data.best_month[0]}<small>{data.best_month[1]}</small></span>}
          </div>
        </article>
      </section>
    </main>
  );
}
