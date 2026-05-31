import { ReactNode } from 'react';
import { BookmarkIcon, CheckIcon, ChevronRightIcon, ListIcon, MusicIcon, PauseIcon, PlayIcon } from '../icons';
import type { PlayerState } from '../hooks/audio';
import type { AlbumDetailResponse, ArtistDetailResponse, DetailResponse, HubCard, MovieDetailResponse, PersonDetailResponse, SeriesDetailResponse, WatchTrack } from '../types';
import type { AppRoute } from '../navigation';
import { LoadingRows, ErrorPanel } from './common';
import { MediaCard } from './mediaCard';

export function DetailPage({
  route,
  data,
  loading,
  error,
  saved,
  onToggleSaved,
  navigate,
  playTrack,
  togglePlayback,
  addToQueue,
  player,
}: {
  route: Extract<AppRoute, { kind: 'detail' }>;
  data: DetailResponse | null;
  loading: boolean;
  error: string;
  saved: Set<string>;
  onToggleSaved: (itemId: string) => void;
  navigate: (href: string, replace?: boolean) => void;
  playTrack: (track: WatchTrack, queue?: WatchTrack[]) => void;
  togglePlayback: (track?: WatchTrack, queue?: WatchTrack[]) => void;
  addToQueue: (track: WatchTrack, playNext?: boolean) => void;
  player: PlayerState;
}) {
  if (loading) {
    return <main className="detail-main"><LoadingRows /></main>;
  }
  if (error || !data) {
    return <main className="detail-main"><ErrorPanel message={error || 'Unable to load this page'} /></main>;
  }
  if (route.detailKind !== data.kind) {
    return <main className="detail-main"><ErrorPanel message="This page changed while loading." /></main>;
  }
  switch (data.kind) {
    case 'movie':
      return <MovieDetail data={data} saved={saved} onToggleSaved={onToggleSaved} />;
    case 'series':
      return <SeriesDetail data={data} saved={saved} onToggleSaved={onToggleSaved} navigate={navigate} />;
    case 'album':
      return (
        <AlbumDetail
          data={data}
          saved={saved}
          onToggleSaved={onToggleSaved}
          playTrack={playTrack}
          togglePlayback={togglePlayback}
          addToQueue={addToQueue}
          player={player}
        />
      );
    case 'artist':
      return (
        <ArtistDetail
          data={data}
          playTrack={playTrack}
          togglePlayback={togglePlayback}
          addToQueue={addToQueue}
          player={player}
          saved={saved}
          onToggleSaved={onToggleSaved}
        />
      );
    case 'person':
      return <PersonDetail data={data} saved={saved} onToggleSaved={onToggleSaved} />;
    default:
      return <main className="detail-main"><ErrorPanel message="Unsupported detail page" /></main>;
  }
}

function DetailHero({
  title,
  subtitle,
  overview,
  posterUrl,
  backdropUrl,
  genres = [],
  playHref,
  classicHref,
  saved,
  onToggleSaved,
  children,
}: {
  title: string;
  subtitle: string;
  overview: string;
  posterUrl: string;
  backdropUrl: string;
  genres?: string[];
  playHref?: string;
  classicHref?: string;
  saved?: boolean;
  onToggleSaved?: () => void;
  children?: ReactNode;
}) {
  return (
    <section className="detail-hero">
      {(backdropUrl || posterUrl) && <img className="detail-backdrop" src={backdropUrl || posterUrl} alt="" decoding="async" fetchPriority="high" />}
      <div className="detail-gradient" />
      <div className="detail-poster">
        <img src={posterUrl || backdropUrl} alt="" decoding="async" fetchPriority="high" />
      </div>
      <div className="detail-copy">
        <p className="eyebrow">{subtitle}</p>
        <h1 dir="auto">{title}</h1>
        {overview && <p className="detail-overview">{overview}</p>}
        {genres.length > 0 && (
          <div className="hero-meta">
            {genres.slice(0, 5).map((genre) => <span key={genre}>{genre}</span>)}
          </div>
        )}
        <div className="hero-actions">
          {playHref && (
            <a className="primary-action" href={playHref}>
              <PlayIcon />
              <span>Play</span>
            </a>
          )}
          {onToggleSaved && (
            <button type="button" className={saved ? 'secondary-action saved-action' : 'secondary-action'} onClick={onToggleSaved}>
              {saved ? <CheckIcon /> : <BookmarkIcon />}
              <span>{saved ? 'Saved' : 'Save'}</span>
            </button>
          )}
          {classicHref && (
            <a className="secondary-action" href={classicHref} title="Open in the original player">
              <span>Classic player</span>
            </a>
          )}
        </div>
        {children}
      </div>
    </section>
  );
}

function MovieDetail({
  data,
  saved,
  onToggleSaved,
}: {
  data: MovieDetailResponse;
  saved: Set<string>;
  onToggleSaved: (itemId: string) => void;
}) {
  return (
    <main className="detail-main">
      <DetailHero
        title={`${data.title}${data.year ? ` (${data.year})` : ''}`}
        subtitle={`${data.variants.length} version${data.variants.length === 1 ? '' : 's'}`}
        overview={data.overview}
        posterUrl={data.posterUrl}
        backdropUrl={data.backdropUrl}
        genres={data.genres}
        playHref={data.playHref}
        classicHref={data.classicHref}
        saved={saved.has(data.savedId)}
        onToggleSaved={() => onToggleSaved(data.savedId)}
      >
        <CreditLinks label="Director" items={data.directors} />
        <CreditLinks label="Cast" items={data.cast} />
      </DetailHero>

      <section className="detail-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Versions</p>
            <h2>Choose playback</h2>
          </div>
        </div>
        <div className="variant-list">
          {data.variants.map((variant) => (
            <a key={variant.key} className="variant-row" href={variant.playHref}>
              <span>{variant.quality || 'Version'}</span>
              <strong>{variant.label || variant.title}</strong>
              <ChevronRightIcon />
            </a>
          ))}
        </div>
      </section>
      <RelatedRows rows={data.related} saved={saved} onToggleSaved={(card) => onToggleSaved(card.itemId)} />
    </main>
  );
}

function SeriesDetail({
  data,
  saved,
  onToggleSaved,
  navigate,
}: {
  data: SeriesDetailResponse;
  saved: Set<string>;
  onToggleSaved: (itemId: string) => void;
  navigate: (href: string, replace?: boolean) => void;
}) {
  return (
    <main className="detail-main">
      <DetailHero
        title={data.title}
        subtitle={`${data.seasonCount} season${data.seasonCount === 1 ? '' : 's'} - ${data.totalEpisodeCount} episodes`}
        overview={data.overview}
        posterUrl={data.posterUrl}
        backdropUrl={data.backdropUrl}
        genres={data.genres}
        playHref={data.playHref}
        classicHref={data.classicHref}
        saved={saved.has(data.savedId)}
        onToggleSaved={() => onToggleSaved(data.savedId)}
      >
        <CreditLinks label="Cast" items={data.cast} />
      </DetailHero>

      <section className="detail-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{data.episodeCount} shown</p>
            <h2>Episodes</h2>
          </div>
          {data.showSelector && (
            <select
              className="season-select"
              value={data.selectedSeason}
              onChange={(event) => navigate(`/app/series/${data.key}?season=${event.currentTarget.value}`)}
              aria-label="Season"
            >
              {data.seasonOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          )}
        </div>
        <div className="episode-stack">
          {data.seasonBlocks.map((block) => (
            <section key={block.season ?? 'misc'} className="episode-block">
              <h3>{block.season !== null ? `Season ${block.season}` : 'Episodes'}</h3>
              <div className="episode-grid">
                {block.entries.map((entry) => (
                  <article key={entry.rep.key} className="episode-card">
                    <a className="episode-thumb" href={entry.rep.playHref}>
                      <img src={entry.rep.episodeStillUrl || entry.rep.thumbUrl} alt="" loading="lazy" decoding="async" />
                      {entry.rep.durationLabel && <span className="card-badge">{entry.rep.durationLabel}</span>}
                    </a>
                    <div>
                      <p className="eyebrow">{entry.rep.episodeLabel || 'Episode'}</p>
                      <h4><a href={entry.rep.playHref}>{entry.rep.title}</a></h4>
                      {entry.rep.episodeOverview && <p>{entry.rep.episodeOverview}</p>}
                      {entry.variants.length > 1 && (
                        <div className="variant-chips">
                          {entry.variants.map((variant) => (
                            <a key={variant.key} href={variant.playHref}>{variant.quality || variant.durationLabel || 'Version'}</a>
                          ))}
                        </div>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            </section>
          ))}
        </div>
      </section>
      <RelatedRows rows={data.related} saved={saved} onToggleSaved={(card) => onToggleSaved(card.itemId)} />
    </main>
  );
}

function AlbumDetail({
  data,
  saved,
  onToggleSaved,
  playTrack,
  togglePlayback,
  addToQueue,
  player,
}: {
  data: AlbumDetailResponse;
  saved: Set<string>;
  onToggleSaved: (itemId: string) => void;
  playTrack: (track: WatchTrack, queue?: WatchTrack[]) => void;
  togglePlayback: (track?: WatchTrack, queue?: WatchTrack[]) => void;
  addToQueue: (track: WatchTrack, playNext?: boolean) => void;
  player: PlayerState;
}) {
  const first = data.tracks[0];
  return (
    <main className="detail-main">
      <DetailHero
        title={data.title}
        subtitle={[data.artist, `${data.trackCount} track${data.trackCount === 1 ? '' : 's'}`].filter(Boolean).join(' - ')}
        overview={data.overview}
        posterUrl={data.posterUrl}
        backdropUrl={data.backdropUrl}
        playHref={first?.appHref}
        saved={saved.has(data.savedId)}
        onToggleSaved={() => onToggleSaved(data.savedId)}
      >
        {data.artistHref && <a className="section-link" href={data.artistHref}>{data.artist}</a>}
      </DetailHero>
      <section className="detail-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Album</p>
            <h2>Tracks</h2>
          </div>
          {first && (
            <button type="button" className="primary-action" onClick={() => playTrack(first, data.tracks)}>
              <PlayIcon />
              <span>Play all</span>
            </button>
          )}
        </div>
        <TrackList tracks={data.tracks} queue={data.tracks} player={player} togglePlayback={togglePlayback} addToQueue={addToQueue} />
      </section>
      <RelatedRows rows={data.related} saved={saved} onToggleSaved={(card) => onToggleSaved(card.itemId)} />
    </main>
  );
}

function ArtistDetail({
  data,
  playTrack,
  togglePlayback,
  addToQueue,
  player,
  saved,
  onToggleSaved,
}: {
  data: ArtistDetailResponse;
  playTrack: (track: WatchTrack, queue?: WatchTrack[]) => void;
  togglePlayback: (track?: WatchTrack, queue?: WatchTrack[]) => void;
  addToQueue: (track: WatchTrack, playNext?: boolean) => void;
  player: PlayerState;
  saved: Set<string>;
  onToggleSaved: (itemId: string) => void;
}) {
  const first = data.tracks[0];
  return (
    <main className="detail-main">
      <DetailHero
        title={data.title}
        subtitle={data.subtitle}
        overview=""
        posterUrl={data.posterUrl}
        backdropUrl={data.backdropUrl}
        playHref={first?.appHref}
      />
      {data.albums.length > 0 && (
        <section className="detail-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Discography</p>
              <h2>Albums</h2>
            </div>
          </div>
          <div className="card-row">
            {data.albums.map((card) => (
              <MediaCard key={card.itemId} card={card} saved={saved.has(card.itemId)} onToggleSaved={(item) => onToggleSaved(item.itemId)} />
            ))}
          </div>
        </section>
      )}
      <section className="detail-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Tracks</p>
            <h2>All songs</h2>
          </div>
          {first && (
            <button type="button" className="primary-action" onClick={() => playTrack(first, data.tracks)}>
              <PlayIcon />
              <span>Play</span>
            </button>
          )}
        </div>
        <TrackList tracks={data.tracks} queue={data.tracks} player={player} togglePlayback={togglePlayback} addToQueue={addToQueue} />
      </section>
    </main>
  );
}

function PersonDetail({
  data,
  saved,
  onToggleSaved,
}: {
  data: PersonDetailResponse;
  saved: Set<string>;
  onToggleSaved: (itemId: string) => void;
}) {
  return (
    <main className="detail-main">
      <DetailHero
        title={data.title}
        subtitle={`${data.roleLabel} - ${data.totalUnique} title${data.totalUnique === 1 ? '' : 's'}`}
        overview=""
        posterUrl={data.posterUrl}
        backdropUrl={data.backdropUrl}
      />
      {data.castItems.length > 0 && (
        <CardGridSection title="As Actor" items={data.castItems} saved={saved} onToggleSaved={onToggleSaved} />
      )}
      {data.directedItems.length > 0 && (
        <CardGridSection title="As Director" items={data.directedItems} saved={saved} onToggleSaved={onToggleSaved} />
      )}
    </main>
  );
}

function CreditLinks({ label, items }: { label: string; items: Array<{ name: string; href: string }> }) {
  if (!items.length) return null;
  return (
    <p className="credit-links">
      <span>{label}</span>
      {items.slice(0, 8).map((item) => (
        <a key={item.href} href={item.href}>{item.name}</a>
      ))}
    </p>
  );
}

function TrackList({
  tracks,
  queue,
  player,
  togglePlayback,
  addToQueue,
}: {
  tracks: WatchTrack[];
  queue: WatchTrack[];
  player: PlayerState;
  togglePlayback: (track?: WatchTrack, queue?: WatchTrack[]) => void;
  addToQueue: (track: WatchTrack, playNext?: boolean) => void;
}) {
  return (
    <div className="track-list">
      {tracks.map((track, index) => {
        const active = player.track?.key === track.key;
        return (
          <a key={track.key} className={active ? 'track-row active' : 'track-row'} href={track.appHref}>
            <span className="track-number">{track.trackNumber || index + 1}</span>
            <span className="track-title">
              <strong>{track.title}</strong>
              <span>{[track.artist, track.albumTitle, track.qualityLabel].filter(Boolean).join(' - ')}</span>
            </span>
            <span className="track-duration">{track.durationLabel}</span>
            <button
              type="button"
              className="icon-button"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                togglePlayback(track, queue);
              }}
              aria-label={active && player.playing ? 'Pause' : 'Play'}
            >
              {active && player.playing ? <PauseIcon /> : <PlayIcon />}
            </button>
            <button
              type="button"
              className="icon-button"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                addToQueue(track, true);
              }}
              aria-label="Play next"
            >
              <ListIcon />
            </button>
          </a>
        );
      })}
    </div>
  );
}

function RelatedRows({
  rows,
  saved,
  onToggleSaved,
}: {
  rows: Array<{ name: string; items: HubCard[] }>;
  saved: Set<string>;
  onToggleSaved: (card: HubCard) => void;
}) {
  if (!rows.length) return null;
  return (
    <div className="shelf-stack">
      {rows.map((row) => (
        <section key={row.name} className="shelf-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Related</p>
              <h2>{row.name}</h2>
            </div>
          </div>
          <div className="card-row">
            {row.items.map((card) => (
              <MediaCard key={`${card.type}:${card.itemId}`} card={card} saved={saved.has(card.itemId)} onToggleSaved={onToggleSaved} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function CardGridSection({
  title,
  items,
  saved,
  onToggleSaved,
}: {
  title: string;
  items: HubCard[];
  saved: Set<string>;
  onToggleSaved: (itemId: string) => void;
}) {
  return (
    <section className="detail-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Filmography</p>
          <h2>{title}</h2>
        </div>
      </div>
      <div className="media-grid">
        {items.map((card) => (
          <MediaCard key={`${card.type}:${card.itemId}`} card={card} saved={saved.has(card.itemId)} onToggleSaved={(item) => onToggleSaved(item.itemId)} />
        ))}
      </div>
    </section>
  );
}
