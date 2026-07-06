import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { BookmarkIcon, CheckIcon, ChevronRightIcon, DownloadIcon, ListIcon, ListPlusIcon, PauseIcon, PlayIcon, ShuffleIcon, XIcon } from '../icons';
import type { PlayerState } from '../hooks/audio';
import type { AlbumDetailResponse, ArtistDetailResponse, DetailResponse, HubCard, MovieDetailResponse, PersonDetailResponse, SeriesDetailResponse, VideoChoice, WatchTrack } from '../types';
import type { AppRoute } from '../navigation';
import { LoadingRows, ErrorPanel } from './common';
import { MediaCard } from './mediaCard';
import { RatingControls } from './rating';
import { formatExternalRating } from '../utils/externalRating';

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
  shuffleQueue,
  player,
  onAddToPlaylist,
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
  shuffleQueue: (queue: WatchTrack[]) => void;
  player: PlayerState;
  onAddToPlaylist?: (track: WatchTrack) => void;
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
          shuffleQueue={shuffleQueue}
          player={player}
          onAddToPlaylist={onAddToPlaylist}
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
          onAddToPlaylist={onAddToPlaylist}
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
  externalRating,
  playHref,
  classicHref,
  imdbHref,
  trailerKey,
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
  externalRating?: MovieDetailResponse['externalRating'];
  playHref?: string;
  classicHref?: string;
  imdbHref?: string;
  trailerKey?: string;
  saved?: boolean;
  onToggleSaved?: () => void;
  children?: ReactNode;
}) {
  const [trailerOpen, setTrailerOpen] = useState(false);
  const ratingLabel = formatExternalRating(externalRating);
  const metaItems = [...(ratingLabel ? [ratingLabel] : []), ...genres.slice(0, 5)];
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
        {metaItems.length > 0 && (
          <div className="hero-meta">
            {metaItems.map((item) => <span key={item}>{item}</span>)}
          </div>
        )}
        <div className="hero-actions">
          {playHref && (
            <a className="primary-action" href={playHref}>
              <PlayIcon />
              <span>Play</span>
            </a>
          )}
          {trailerKey && (
            <button type="button" className="secondary-action" onClick={() => setTrailerOpen(true)}>
              <PlayIcon />
              <span>Trailer</span>
            </button>
          )}
          {onToggleSaved && (
            <button type="button" className={saved ? 'secondary-action saved-action' : 'secondary-action'} onClick={onToggleSaved}>
              {saved ? <CheckIcon /> : <BookmarkIcon />}
              <span>{saved ? 'Saved' : 'Save'}</span>
            </button>
          )}
          {imdbHref && (
            <a className="secondary-action" href={imdbHref} target="_blank" rel="noopener noreferrer">
              <span>IMDb</span>
            </a>
          )}
          {classicHref && (
            <a className="secondary-action" href={classicHref} title="Open in the original player">
              <span>Classic player</span>
            </a>
          )}
        </div>
        {children}
      </div>
      {trailerOpen && (
        <div
          className="trailer-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Trailer"
          onClick={() => setTrailerOpen(false)}
        >
          <div className="trailer-frame" onClick={(e) => e.stopPropagation()}>
            <button type="button" className="icon-button trailer-close" onClick={() => setTrailerOpen(false)} aria-label="Close trailer">
              <XIcon />
            </button>
            <iframe
              src={`https://www.youtube.com/embed/${trailerKey}?autoplay=1&rel=0`}
              title="Trailer"
              allow="autoplay; fullscreen"
              allowFullScreen
            />
          </div>
        </div>
      )}
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
  const ratingId = data.variants[0]?.itemId || null;
  return (
    <main className="detail-main">
      <DetailHero
        title={`${data.title}${data.year ? ` (${data.year})` : ''}`}
        subtitle={`${data.variants.length} version${data.variants.length === 1 ? '' : 's'}`}
        overview={data.overview}
        posterUrl={data.posterUrl}
        backdropUrl={data.backdropUrl}
        genres={data.genres}
        externalRating={data.externalRating}
        playHref={data.playHref}
        classicHref={data.classicHref}
        imdbHref={data.imdbHref}
        trailerKey={data.trailerKey}
        saved={saved.has(data.savedId)}
        onToggleSaved={() => onToggleSaved(data.savedId)}
      >
        <CreditLinks label="Director" items={data.directors} />
        <CreditLinks label="Cast" items={data.cast} />
        <RatingControls messageId={ratingId} />
      </DetailHero>

      <section className="detail-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Versions</p>
            <h2>Choose playback</h2>
          </div>
        </div>
        <div className="playback-options">
          {data.variants.map((variant) => (
            <a
              key={variant.key}
              className="playback-option"
              href={variant.playHref}
              aria-label={`Play ${[variant.title, variant.quality].filter(Boolean).join(' ') || 'version'}`}
            >
              <strong>{variant.quality || 'Version'}</strong>
              <span>{variant.title || variant.label || 'Playback version'}</span>
              <small>{[variant.durationLabel, variant.fileSizeLabel].filter(Boolean).join(' - ')}</small>
              <em>Play</em>
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
  const ratingId = data.seasonBlocks[0]?.entries[0]?.rep.itemId || null;
  const [downloadBatch, setDownloadBatch] = useState<{ current: number; total: number } | null>(null);
  const downloadTimers = useRef<number[]>([]);
  const downloadTargets = useMemo(() => {
    const seen = new Set<string>();
    return data.seasonBlocks.flatMap((block) => (
      block.entries.flatMap((entry) => {
        const href = entry.rep.downloadHref;
        if (!href || seen.has(href)) return [];
        seen.add(href);
        return [href];
      })
    ));
  }, [data.seasonBlocks]);

  useEffect(() => () => {
    downloadTimers.current.forEach((timer) => window.clearTimeout(timer));
  }, []);

  const handleDownloadAll = () => {
    if (!downloadTargets.length || downloadBatch) return;
    downloadTimers.current.forEach((timer) => window.clearTimeout(timer));
    downloadTimers.current = [];
    const total = downloadTargets.length;
    setDownloadBatch({ current: 0, total });
    downloadTargets.forEach((href, index) => {
      const start = () => {
        triggerBrowserDownload(href);
        setDownloadBatch({ current: index + 1, total });
        if (index === total - 1) {
          const doneTimer = window.setTimeout(() => setDownloadBatch(null), 1600);
          downloadTimers.current.push(doneTimer);
        }
      };
      if (index === 0) {
        start();
      } else {
        const timer = window.setTimeout(start, index * 900);
        downloadTimers.current.push(timer);
      }
    });
  };
  const downloadButtonLabel = downloadBatch
    ? `Starting ${downloadBatch.current}/${downloadBatch.total}`
    : 'Download all';
  return (
    <main className="detail-main">
      <DetailHero
        title={data.title}
        subtitle={`${data.seasonCount} season${data.seasonCount === 1 ? '' : 's'} - ${data.totalEpisodeCount} episodes`}
        overview={data.overview}
        posterUrl={data.posterUrl}
        backdropUrl={data.backdropUrl}
        genres={data.genres}
        externalRating={data.externalRating}
        playHref={data.playHref}
        classicHref={data.classicHref}
        imdbHref={data.imdbHref}
        trailerKey={data.trailerKey}
        saved={saved.has(data.savedId)}
        onToggleSaved={() => onToggleSaved(data.savedId)}
      >
        <CreditLinks label="Cast" items={data.cast} />
        <RatingControls messageId={ratingId} />
      </DetailHero>

      <section className="detail-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{data.episodeCount} shown</p>
            <h2>Episodes</h2>
          </div>
          <div className="section-actions">
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
            {downloadTargets.length > 0 && (
              <button
                type="button"
                className="secondary-action season-download-all"
                onClick={handleDownloadAll}
                disabled={Boolean(downloadBatch)}
                aria-label="Download all shown episodes"
              >
                <DownloadIcon />
                <span>{downloadButtonLabel}</span>
              </button>
            )}
          </div>
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
                      <EpisodePlaybackState progressPct={entry.progressPct} watched={entry.watched} />
                      {entry.rep.durationLabel && <span className="card-badge">{entry.rep.durationLabel}</span>}
                    </a>
                    <div>
                      <p className="eyebrow">
                        {entry.rep.episodeLabel || 'Episode'}
                        {entry.rep.firstAired && (
                          <time className="episode-airdate" dateTime={entry.rep.firstAired}>
                            {entry.rep.firstAired.slice(0, 4)}
                          </time>
                        )}
                      </p>
                      <h4><a href={entry.rep.playHref}>{entry.rep.title}</a></h4>
                      {entry.rep.episodeOverview && <p>{entry.rep.episodeOverview}</p>}
                      {entry.variants.length > 1 && (
                        <div className="variant-chips">
                          {entry.variants.map((variant) => (
                            <a key={variant.key} href={variant.playHref}>{variant.quality || variant.durationLabel || 'Version'}</a>
                          ))}
                        </div>
                      )}
                      {entry.rep.downloadHref && (
                        <div className="episode-card-actions">
                          <a
                            className="episode-download-action"
                            href={entry.rep.downloadHref}
                            download
                            aria-label={`Download ${episodeDownloadTitle(entry.rep)}`}
                          >
                            <DownloadIcon />
                            <span>Download</span>
                          </a>
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

function episodeDownloadTitle(choice: VideoChoice): string {
  return [choice.episodeLabel, choice.title].filter(Boolean).join(' ') || 'episode';
}

function triggerBrowserDownload(href: string) {
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = '';
  anchor.rel = 'noopener';
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

function EpisodePlaybackState({ progressPct, watched }: { progressPct: number; watched: boolean }) {
  const progress = Math.max(0, Math.min(100, Math.round(progressPct || 0)));
  if (!progress && !watched) return null;
  const complete = watched && !progress;
  const label = complete ? 'Watched' : `${progress}% watched`;
  const width = complete ? 100 : progress;
  return (
    <>
      <span className={complete ? 'episode-progress-badge watched' : 'episode-progress-badge'} aria-label={label}>
        {complete && <CheckIcon />}
        <span>{complete ? 'Watched' : `${progress}%`}</span>
      </span>
      <span className="episode-progress-track" aria-hidden="true">
        <span style={{ width: `${width}%` }} />
      </span>
    </>
  );
}

function AlbumDetail({
  data,
  saved,
  onToggleSaved,
  playTrack,
  togglePlayback,
  addToQueue,
  shuffleQueue,
  player,
  onAddToPlaylist,
}: {
  data: AlbumDetailResponse;
  saved: Set<string>;
  onToggleSaved: (itemId: string) => void;
  playTrack: (track: WatchTrack, queue?: WatchTrack[]) => void;
  togglePlayback: (track?: WatchTrack, queue?: WatchTrack[]) => void;
  addToQueue: (track: WatchTrack, playNext?: boolean) => void;
  shuffleQueue: (queue: WatchTrack[]) => void;
  player: PlayerState;
  onAddToPlaylist?: (track: WatchTrack) => void;
}) {
  const first = data.tracks[0];
  return (
    <main className="detail-main music-detail-main">
      <div className="album-layout">
        <AlbumHero
          title={data.title}
          artistHref={data.artistHref}
          artist={data.artist}
          year={data.year}
          trackCount={data.trackCount}
          overview={data.overview}
          posterUrl={data.posterUrl}
          backdropUrl={data.backdropUrl}
          onPlayAll={first ? () => playTrack(first, data.tracks) : undefined}
          onShuffle={data.tracks.length > 1 ? () => shuffleQueue(data.tracks) : undefined}
          saved={saved.has(data.savedId)}
          onToggleSaved={() => onToggleSaved(data.savedId)}
        />
        <section className="detail-section album-track-section">
          <div className="section-heading album-track-heading">
            <div>
              <p className="eyebrow">Album</p>
              <h2>Tracks</h2>
            </div>
            <span>{data.trackCount} track{data.trackCount === 1 ? '' : 's'}</span>
          </div>
          <TrackList tracks={data.tracks} queue={data.tracks} player={player} togglePlayback={togglePlayback} addToQueue={addToQueue} onAddToPlaylist={onAddToPlaylist} context="album" />
        </section>
      </div>
      <RelatedRows rows={data.related} saved={saved} onToggleSaved={(card) => onToggleSaved(card.itemId)} />
    </main>
  );
}

function AlbumHero({
  title,
  artist,
  artistHref,
  year,
  trackCount,
  overview,
  posterUrl,
  backdropUrl,
  onPlayAll,
  onShuffle,
  saved,
  onToggleSaved,
}: {
  title: string;
  artist: string;
  artistHref: string;
  year: number | null;
  trackCount: number;
  overview: string;
  posterUrl: string;
  backdropUrl: string;
  onPlayAll?: () => void;
  onShuffle?: () => void;
  saved: boolean;
  onToggleSaved: () => void;
}) {
  return (
    <section className="album-hero" aria-label="Album summary">
      {(backdropUrl || posterUrl) && <img className="album-backdrop" src={backdropUrl || posterUrl} alt="" decoding="async" fetchPriority="high" />}
      <div className="album-hero-art">
        <img src={posterUrl || backdropUrl} alt="" decoding="async" fetchPriority="high" />
      </div>
      <div className="album-hero-copy">
        <p className="eyebrow">Album</p>
        <h1 dir="auto">{title}</h1>
        {artistHref && artist && <a className="album-artist-link" href={artistHref}>{artist}</a>}
        <div className="album-stats" aria-label="Album metadata">
          {year && <span>{year}</span>}
          <span>{trackCount} track{trackCount === 1 ? '' : 's'}</span>
        </div>
        {overview && <p className="album-overview">{overview}</p>}
        <div className="hero-actions">
          {onPlayAll && (
            <button type="button" className="primary-action" onClick={onPlayAll}>
              <PlayIcon />
              <span>Play all</span>
            </button>
          )}
          {onShuffle && (
            <button type="button" className="secondary-action" onClick={onShuffle}>
              <ShuffleIcon />
              <span>Shuffle</span>
            </button>
          )}
          <button type="button" className={saved ? 'secondary-action saved-action' : 'secondary-action'} onClick={onToggleSaved}>
            {saved ? <CheckIcon /> : <BookmarkIcon />}
            <span>{saved ? 'Saved' : 'Save'}</span>
          </button>
        </div>
      </div>
    </section>
  );
}

function ArtistDetail({
  data,
  playTrack,
  togglePlayback,
  addToQueue,
  player,
  onAddToPlaylist,
  saved,
  onToggleSaved,
}: {
  data: ArtistDetailResponse;
  playTrack: (track: WatchTrack, queue?: WatchTrack[]) => void;
  togglePlayback: (track?: WatchTrack, queue?: WatchTrack[]) => void;
  addToQueue: (track: WatchTrack, playNext?: boolean) => void;
  player: PlayerState;
  onAddToPlaylist?: (track: WatchTrack) => void;
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
        <TrackList tracks={data.tracks} queue={data.tracks} player={player} togglePlayback={togglePlayback} addToQueue={addToQueue} onAddToPlaylist={onAddToPlaylist} />
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
  onAddToPlaylist,
  context = 'default',
}: {
  tracks: WatchTrack[];
  queue: WatchTrack[];
  player: PlayerState;
  togglePlayback: (track?: WatchTrack, queue?: WatchTrack[]) => void;
  addToQueue: (track: WatchTrack, playNext?: boolean) => void;
  onAddToPlaylist?: (track: WatchTrack) => void;
  context?: 'default' | 'album';
}) {
  const subtitleForTrack = (track: WatchTrack) => {
    if (context === 'album') return [track.artist, track.format].filter(Boolean).join(' - ');
    return [track.artist, track.albumTitle, track.qualityLabel].filter(Boolean).join(' - ');
  };

  return (
    <div className="track-list">
      {tracks.map((track, index) => {
        const active = player.track?.key === track.key;
        return (
          <a
            key={track.key}
            className={[
              'track-row',
              onAddToPlaylist ? 'has-playlist' : '',
              active ? 'active' : '',
            ].filter(Boolean).join(' ')}
            href={track.appHref}
          >
            <span className="track-number">{track.trackNumber || index + 1}</span>
            <span className="track-title">
              <strong>{track.title}</strong>
              <span>{subtitleForTrack(track)}</span>
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
              aria-label={active && player.playing ? `Pause ${track.title}` : `Play ${track.title}`}
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
              aria-label={`Play ${track.title} next`}
            >
              <ListIcon />
            </button>
            <button
              type="button"
              className="icon-button"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                addToQueue(track, false);
              }}
              aria-label={`Add ${track.title} to queue`}
            >
              <span aria-hidden="true">+</span>
            </button>
            {onAddToPlaylist && (
              <button
                type="button"
                className="icon-button"
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  onAddToPlaylist(track);
                }}
                aria-label={`Add ${track.title} to playlist`}
              >
                <ListPlusIcon />
              </button>
            )}
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
