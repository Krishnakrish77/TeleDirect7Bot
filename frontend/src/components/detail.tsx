import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AutoplayIcon, BookmarkIcon, CheckIcon, ChevronRightIcon, DownloadIcon, FilmIcon, ListIcon, ListPlusIcon, PauseIcon, PlayIcon, ShuffleIcon, XIcon } from '../icons';
import type { PlayerState } from '../hooks/audio';
import type { AlbumDetailResponse, ArtistDetailResponse, DetailResponse, HubCard, MovieDetailResponse, PersonDetailResponse, SeriesDetailResponse, VideoChoice, WatchTrack } from '../types';
import type { AppRoute } from '../navigation';
import { LoadingRows, ErrorPanel } from './common';
import { MediaCard } from './mediaCard';
import { RatingControls } from './rating';
import { formatExternalRating } from '../utils/externalRating';
import { isLocallyWatched, markLocallyWatched } from '../utils/localWatched';
import { uniqueMetadataParts } from '../utils/metadata';
import { Button } from './ui/button';
import { TrailerModal } from './trailerModal';

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
  startRadio,
  player,
  onAddToPlaylist,
  onMarkWatched,
  canDownload = true,
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
  startRadio?: (seed: { track?: string; artist?: string }) => void;
  player: PlayerState;
  onAddToPlaylist?: (track: WatchTrack) => void;
  onMarkWatched?: (keys: string[], title: string) => void;
  canDownload?: boolean;
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
      return <MovieDetail data={data} saved={saved} onToggleSaved={onToggleSaved} onMarkWatched={onMarkWatched} />;
    case 'series':
      return (
        <SeriesDetail
          data={data}
          saved={saved}
          onToggleSaved={onToggleSaved}
          navigate={navigate}
          onMarkWatched={onMarkWatched}
          canDownload={canDownload}
        />
      );
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
          startRadio={startRadio}
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
          shuffleQueue={shuffleQueue}
          startRadio={startRadio}
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
  imdbHref,
  trailerKey,
  facts = [],
  logoUrl = '',
  saved,
  onToggleSaved,
  extraActions,
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
  imdbHref?: string;
  trailerKey?: string;
  facts?: string[];
  logoUrl?: string;
  saved?: boolean;
  onToggleSaved?: () => void;
  extraActions?: ReactNode;
  children?: ReactNode;
}) {
  const [trailerOpen, setTrailerOpen] = useState(false);
  const trailerButtonRef = useRef<HTMLButtonElement | null>(null);
  const closeTrailer = useCallback(() => setTrailerOpen(false), []);
  const ratingLabel = formatExternalRating(externalRating);
  const metaItems = [...facts, ...(ratingLabel ? [ratingLabel] : []), ...genres.slice(0, 5)];
  return (
    <section className="detail-hero">
      {(backdropUrl || posterUrl) && <img className="detail-backdrop" src={backdropUrl || posterUrl} alt="" decoding="async" fetchPriority="high" />}
      {/* Poster loads at default priority so it doesn't compete with the
          backdrop (the LCP element) for the first-paint fetch budget. */}
      <div className="detail-gradient" />
      <div className="detail-poster">
        <img src={posterUrl || backdropUrl} alt="" decoding="async" />
      </div>
      <div className="detail-copy">
        <p className="eyebrow">{subtitle}</p>
        {logoUrl ? <img className="detail-title-logo" src={logoUrl} alt={title} /> : <h1 dir="auto">{title}</h1>}
        {overview && <p className="detail-overview">{overview}</p>}
        {metaItems.length > 0 && (
          <div className="hero-meta">
            {metaItems.map((item) => <span key={item}>{item}</span>)}
          </div>
        )}
        <div className="hero-actions">
          {playHref && (
            <Button asChild><a href={playHref}><PlayIcon /><span>Play</span></a></Button>
          )}
          {trailerKey && (
            <Button ref={trailerButtonRef} type="button" variant="secondary" onClick={() => setTrailerOpen(true)} title="Watch trailer"><FilmIcon /><span>Trailer</span></Button>
          )}
          {onToggleSaved && (
            <Button type="button" variant="secondary" size="icon" className={saved ? 'saved-action' : ''} onClick={onToggleSaved} aria-label={saved ? 'Saved' : 'Save'} title={saved ? 'Remove from saved' : 'Save'}>
              {saved ? <CheckIcon /> : <BookmarkIcon />}
            </Button>
          )}
          {extraActions}
        </div>
        {children}
      </div>
      {trailerOpen && trailerKey && (
        <TrailerModal trailerKey={trailerKey} title={title} returnFocusTo={trailerButtonRef} onClose={closeTrailer} />
      )}
    </section>
  );
}

function detailCountLabel(count: number, singular: string) {
  return `${count} ${singular}${count === 1 ? '' : 's'}`;
}

function choiceWatchKey(choice: VideoChoice): string {
  return choice.watchKey || choice.key;
}

function uniqueChoiceKeys(choices: VideoChoice[]): string[] {
  const keys = new Set<string>();
  choices.forEach((choice) => {
    const key = choiceWatchKey(choice);
    if (key) keys.add(key);
  });
  return [...keys];
}

function removeLocalContinue(keys: string[]) {
  try {
    const cw = JSON.parse(localStorage.getItem('td:cw') || '{}') || {};
    keys.forEach((key) => {
      delete cw[key];
    });
    localStorage.setItem('td:cw', JSON.stringify(cw));
  } catch {
    // Local resume state is best-effort only.
  }
}

function MarkWatchedAction({
  keys,
  title,
  initiallyWatched,
  onMarkWatched,
  onMarked,
  label = 'Mark watched',
  watchedLabel = 'Watched',
}: {
  keys: string[];
  title: string;
  initiallyWatched: boolean;
  onMarkWatched?: (keys: string[], title: string) => void;
  onMarked?: (keys: string[]) => void;
  label?: string;
  watchedLabel?: string;
}) {
  const keySignature = keys.join('|');
  const [marked, setMarked] = useState(false);
  useEffect(() => {
    setMarked(false);
  }, [keySignature]);
  const watched = keys.length > 0 && (marked || initiallyWatched);
  const handleMarkWatched = useCallback(() => {
    if (!keys.length || watched) return;
    removeLocalContinue(keys);
    keys.forEach(markLocallyWatched);
    setMarked(true);
    onMarked?.(keys);
    onMarkWatched?.(keys, title);
  }, [keys, onMarkWatched, onMarked, title, watched]);

  if (!keys.length) return null;
  return (
    <Button
      type="button"
      variant="secondary"
      className={watched ? 'detail-watched-action watched' : ''}
      onClick={handleMarkWatched}
      disabled={watched}
      title={watched ? watchedLabel : label}
      aria-label={watched ? `${title} watched` : `Mark ${title} as watched`}
    >
      <CheckIcon />
      <span>{watched ? watchedLabel : label}</span>
    </Button>
  );
}

function DetailInfoSection({
  label,
  title,
  overview,
  facts,
  genres,
  directors,
  cast,
  imdbHref,
}: {
  label: string;
  title: string;
  overview: string;
  facts: string[];
  genres: string[];
  directors: Array<{ name: string; href: string }>;
  cast: Array<{ name: string; href: string }>;
  imdbHref: string;
}) {
  const visibleGenres = genres.slice(0, 5);
  const visibleDirectors = directors.slice(0, 3);
  const visibleCast = cast.slice(0, 6);
  if (!overview && !facts.length && !visibleGenres.length && !visibleDirectors.length && !visibleCast.length && !imdbHref) {
    return null;
  }

  return (
    <section className="video-info-section detail-info-section" aria-label="Movie and series information">
      <div className="video-info-copy">
        <p className="eyebrow">{label}</p>
        <h2 dir="auto">{title}</h2>
        {overview && <p className="video-info-overview">{overview}</p>}
        {(facts.length > 0 || visibleGenres.length > 0) && (
          <div className="video-info-chips" aria-label="Media details">
            {facts.map((fact) => <span key={fact}>{fact}</span>)}
            {visibleGenres.map((genre) => <span key={genre}>{genre}</span>)}
          </div>
        )}
      </div>
      {(visibleDirectors.length > 0 || visibleCast.length > 0 || imdbHref) && (
        <dl className="video-info-credits">
          {visibleDirectors.length > 0 && (
            <div>
              <dt>{visibleDirectors.length === 1 ? 'Director' : 'Directors'}</dt>
              <dd>
                {visibleDirectors.map((person) => (
                  <a key={person.href || person.name} href={person.href}>{person.name}</a>
                ))}
              </dd>
            </div>
          )}
          {visibleCast.length > 0 && (
            <div>
              <dt>Cast</dt>
              <dd>
                {visibleCast.map((person) => (
                  <a key={person.href || person.name} href={person.href}>{person.name}</a>
                ))}
              </dd>
            </div>
          )}
          {imdbHref && (
            <div>
              <dt>Links</dt>
              <dd>
                <a href={imdbHref} target="_blank" rel="noopener noreferrer">IMDb</a>
              </dd>
            </div>
          )}
        </dl>
      )}
    </section>
  );
}

function runtimeLabel(minutes: number): string {
  if (!minutes || minutes < 1) return '';
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return hours ? `${hours}h${remainder ? ` ${remainder}m` : ''}` : `${remainder}m`;
}

function MovieDetail({
  data,
  saved,
  onToggleSaved,
  onMarkWatched,
}: {
  data: MovieDetailResponse;
  saved: Set<string>;
  onToggleSaved: (itemId: string) => void;
  onMarkWatched?: (keys: string[], title: string) => void;
}) {
  const ratingId = data.variants[0]?.itemId || null;
  const firstVariant = data.variants[0];
  const movieWatchKeys = useMemo(() => uniqueChoiceKeys(data.variants), [data.variants]);
  const movieWatched = data.variants.some((variant) => Boolean(variant.watched) || isLocallyWatched(choiceWatchKey(variant)));
  const movieFacts = uniqueMetadataParts([
    data.year,
    // One runtime only — prefer the actual file duration, fall back to TMDB.
    firstVariant?.durationLabel || runtimeLabel(data.runtimeMinutes ?? 0),
    data.certification ?? '',
    // "1 version" is noise; only show when there are alternates.
    data.variants.length > 1 ? detailCountLabel(data.variants.length, 'version') : '',
    firstVariant?.fileSizeLabel,
    firstVariant?.quality,
  ]);
  return (
    <main className="detail-main">
      <DetailHero
        title={`${data.title}${data.year ? ` (${data.year})` : ''}`}
        subtitle={`${data.variants.length} version${data.variants.length === 1 ? '' : 's'}`}
        overview=""
        posterUrl={data.posterUrl}
        backdropUrl={data.backdropUrl}
        genres={data.genres}
        externalRating={data.externalRating}
        playHref={data.playHref}
        imdbHref={data.imdbHref}
        trailerKey={data.trailerKey}
        facts={uniqueMetadataParts([runtimeLabel(data.runtimeMinutes ?? 0), data.certification ?? ''])}
        logoUrl={data.logoUrl ?? ''}
        saved={saved.has(data.savedId)}
        onToggleSaved={() => onToggleSaved(data.savedId)}
        extraActions={(
          <MarkWatchedAction
            keys={movieWatchKeys}
            title={data.title}
            initiallyWatched={movieWatched}
            onMarkWatched={onMarkWatched}
          />
        )}
      >
        <RatingControls messageId={ratingId} />
      </DetailHero>

      <DetailInfoSection
        label="About this title"
        title={data.title}
        overview={data.overview}
        facts={movieFacts}
        genres={data.genres}
        directors={data.directors}
        cast={data.cast}
        imdbHref={data.imdbHref}
      />

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
  onMarkWatched,
  canDownload = true,
}: {
  data: SeriesDetailResponse;
  saved: Set<string>;
  onToggleSaved: (itemId: string) => void;
  navigate: (href: string, replace?: boolean) => void;
  onMarkWatched?: (keys: string[], title: string) => void;
  canDownload?: boolean;
}) {
  const ratingId = data.seasonBlocks[0]?.entries[0]?.rep.itemId || null;
  const [, refreshSeriesWatchedState] = useState(0);
  const visibleEntries = useMemo(
    () => data.seasonBlocks.flatMap((block) => block.entries),
    [data.seasonBlocks],
  );
  const visibleChoices = useMemo(
    () => visibleEntries.flatMap((entry) => entry.variants.length ? entry.variants : [entry.rep]),
    [visibleEntries],
  );
  const seriesWatchKeys = useMemo(() => uniqueChoiceKeys(visibleChoices), [visibleChoices]);
  const visibleEntriesWatched = visibleEntries.length > 0 && visibleEntries.every((entry) => {
    const choices = entry.variants.length ? entry.variants : [entry.rep];
    return entry.watched || choices.some((choice) => isLocallyWatched(choiceWatchKey(choice)));
  });
  const handleShownMarked = useCallback(() => {
    refreshSeriesWatchedState((value) => value + 1);
  }, []);
  const seriesFacts = uniqueMetadataParts([
    data.year,
    runtimeLabel(data.runtimeMinutes ?? 0),
    data.certification ?? '',
    detailCountLabel(data.seasonCount, 'season'),
    detailCountLabel(data.totalEpisodeCount, 'episode'),
  ]);
  const [downloadBatch, setDownloadBatch] = useState<{ current: number; total: number } | null>(null);
  const downloadStatusTimer = useRef<number | null>(null);
  const downloadTargets = useMemo(() => {
    if (!canDownload) return [];
    const seen = new Set<string>();
    return data.seasonBlocks.flatMap((block) => (
      block.entries.flatMap((entry) => {
        const href = entry.rep.downloadHref;
        if (!href || seen.has(href)) return [];
        seen.add(href);
        return [href];
      })
    ));
  }, [canDownload, data.seasonBlocks]);

  const clearDownloadStatusTimer = () => {
    if (downloadStatusTimer.current !== null) {
      window.clearTimeout(downloadStatusTimer.current);
      downloadStatusTimer.current = null;
    }
  };

  useEffect(() => () => clearDownloadStatusTimer(), []);

  useEffect(() => {
    clearDownloadStatusTimer();
    setDownloadBatch(null);
  }, [data.key, data.selectedSeason]);

  const handleDownloadAll = () => {
    if (!downloadTargets.length || downloadBatch) return;
    clearDownloadStatusTimer();
    const total = downloadTargets.length;
    downloadTargets.forEach((href) => {
      triggerBrowserDownload(href);
    });
    setDownloadBatch({ current: total, total });
    downloadStatusTimer.current = window.setTimeout(() => {
      setDownloadBatch(null);
      downloadStatusTimer.current = null;
    }, 1600);
  };
  const downloadButtonLabel = downloadBatch
    ? `Starting ${downloadBatch.current}/${downloadBatch.total}`
    : 'Download all';
  return (
    <main className="detail-main">
      <DetailHero
        title={data.title}
        subtitle={`${data.seasonCount} season${data.seasonCount === 1 ? '' : 's'} - ${data.totalEpisodeCount} episodes`}
        overview=""
        posterUrl={data.posterUrl}
        backdropUrl={data.backdropUrl}
        genres={data.genres}
        externalRating={data.externalRating}
        playHref={data.playHref}
        imdbHref={data.imdbHref}
        trailerKey={data.trailerKey}
        facts={uniqueMetadataParts([runtimeLabel(data.runtimeMinutes ?? 0), data.certification ?? ''])}
        logoUrl={data.logoUrl ?? ''}
        saved={saved.has(data.savedId)}
        onToggleSaved={() => onToggleSaved(data.savedId)}
        extraActions={(
          <MarkWatchedAction
            keys={seriesWatchKeys}
            title={data.title}
            initiallyWatched={visibleEntriesWatched}
            onMarkWatched={onMarkWatched}
            onMarked={handleShownMarked}
            label="Watched"
            watchedLabel="Watched"
          />
        )}
      >
        <RatingControls messageId={ratingId} />
      </DetailHero>

      <DetailInfoSection
        label="About this series"
        title={data.title}
        overview={data.overview}
        facts={seriesFacts}
        genres={data.genres}
        directors={data.directors}
        cast={data.cast}
        imdbHref={data.imdbHref}
      />

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
              <Button
                type="button"
                variant="secondary"
                className="season-download-all"
                onClick={handleDownloadAll}
                disabled={Boolean(downloadBatch)}
                aria-label="Download all shown episodes"
              >
                <DownloadIcon />
                <span>{downloadButtonLabel}</span>
              </Button>
            )}
          </div>
        </div>
        <div className="episode-stack">
          {data.seasonBlocks.map((block) => (
            <section key={block.season ?? 'misc'} className="episode-block">
              <h3>{block.season !== null ? `Season ${block.season}` : 'Episodes'}</h3>
              <div className="episode-grid">
                {block.entries.map((entry) => {
                  const choices = entry.variants.length ? entry.variants : [entry.rep];
                  const entryWatched = entry.watched || choices.some((choice) => isLocallyWatched(choiceWatchKey(choice)));
                  const entryProgressPct = entryWatched ? 0 : entry.progressPct;
                  return (
                    <article key={entry.rep.key} className="episode-card">
                      <a className="episode-thumb" href={entry.rep.playHref}>
                        <img src={entry.rep.episodeStillUrl || entry.rep.thumbUrl} alt="" loading="lazy" decoding="async" />
                        <EpisodePlaybackState progressPct={entryProgressPct} watched={entryWatched} />
                        {entry.rep.durationLabel && <span className="card-badge">{entry.rep.durationLabel}</span>}
                      </a>
                      <div>
                        <div className="episode-card-topline">
                          <p className="eyebrow">
                            {entry.rep.episodeLabel || 'Episode'}
                            {entry.rep.firstAired && (
                              <time className="episode-airdate" dateTime={entry.rep.firstAired}>
                                {entry.rep.firstAired.slice(0, 4)}
                              </time>
                            )}
                          </p>
                          {canDownload && entry.rep.downloadHref && (
                            <a
                              className="episode-download-action"
                              href={entry.rep.downloadHref}
                              download
                              aria-label={`Download ${episodeDownloadTitle(entry.rep)}`}
                              title={`Download ${episodeDownloadTitle(entry.rep)}`}
                            >
                              <DownloadIcon />
                            </a>
                          )}
                        </div>
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
                  );
                })}
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
  startRadio,
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
  startRadio?: (seed: { track?: string; artist?: string }) => void;
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
          artistCredits={data.artistCredits || []}
          year={data.year}
          trackCount={data.trackCount}
          overview={data.overview}
          posterUrl={data.posterUrl}
          backdropUrl={data.backdropUrl}
          onPlayAll={first ? () => playTrack(first, data.tracks) : undefined}
          onShuffle={data.tracks.length > 1 ? () => shuffleQueue(data.tracks) : undefined}
          onAutoplay={first && startRadio ? () => startRadio({ track: first.key }) : undefined}
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
  artistCredits,
  year,
  trackCount,
  overview,
  posterUrl,
  backdropUrl,
  onPlayAll,
  onShuffle,
  onAutoplay,
  saved,
  onToggleSaved,
}: {
  title: string;
  artist: string;
  artistHref: string;
  artistCredits: Array<{ name: string; href: string }>;
  year: number | null;
  trackCount: number;
  overview: string;
  posterUrl: string;
  backdropUrl: string;
  onPlayAll?: () => void;
  onShuffle?: () => void;
  onAutoplay?: () => void;
  saved: boolean;
  onToggleSaved: () => void;
}) {
  return (
    <section className="album-hero" aria-label="Album summary">
      {(backdropUrl || posterUrl) && <img className="album-backdrop" src={backdropUrl || posterUrl} alt="" decoding="async" fetchPriority="high" />}
      <div className="album-hero-art">
        <img src={posterUrl || backdropUrl} alt="" decoding="async" />
      </div>
      <div className="album-hero-copy">
        <p className="eyebrow">Album</p>
        <h1 dir="auto">{title}</h1>
        {artistCredits.length > 0 ? (
          <div className="album-artist-credits" aria-label="Artists">
            {artistCredits.map((credit, index) => (
              <span key={credit.href}>
                {index > 0 && <span className="album-artist-separator" aria-hidden="true">, </span>}
                <a className="album-artist-link" href={credit.href}>{credit.name}</a>
              </span>
            ))}
          </div>
        ) : artistHref && artist ? <a className="album-artist-link" href={artistHref}>{artist}</a> : null}
        <div className="album-stats" aria-label="Album metadata">
          {year && <span>{year}</span>}
          <span>{trackCount} track{trackCount === 1 ? '' : 's'}</span>
        </div>
        {overview && <p className="album-overview">{overview}</p>}
        <div className="hero-actions">
          {onPlayAll && (
            <Button type="button" onClick={onPlayAll}>
              <PlayIcon />
              <span>Play all</span>
            </Button>
          )}
          {onShuffle && (
            <Button type="button" variant="secondary" onClick={onShuffle}>
              <ShuffleIcon />
              <span>Shuffle</span>
            </Button>
          )}
          {onAutoplay && (
            <Button type="button" variant="secondary" onClick={onAutoplay}>
              <AutoplayIcon />
              <span>Autoplay</span>
            </Button>
          )}
          <Button type="button" variant="secondary" className={saved ? 'saved-action' : ''} onClick={onToggleSaved}>
            {saved ? <CheckIcon /> : <BookmarkIcon />}
            <span>{saved ? 'Saved' : 'Save'}</span>
          </Button>
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
  shuffleQueue,
  startRadio,
  player,
  onAddToPlaylist,
  saved,
  onToggleSaved,
}: {
  data: ArtistDetailResponse;
  playTrack: (track: WatchTrack, queue?: WatchTrack[]) => void;
  togglePlayback: (track?: WatchTrack, queue?: WatchTrack[]) => void;
  addToQueue: (track: WatchTrack, playNext?: boolean) => void;
  shuffleQueue: (queue: WatchTrack[]) => void;
  startRadio?: (seed: { track?: string; artist?: string }) => void;
  player: PlayerState;
  onAddToPlaylist?: (track: WatchTrack) => void;
  saved: Set<string>;
  onToggleSaved: (itemId: string) => void;
}) {
  const first = data.tracks[0];
  const albumCount = data.albums.length;
  return (
    <main className="detail-main music-detail-main artist-detail-main">
      <section className="artist-hero" aria-label="Artist summary">
        {(data.backdropUrl || data.posterUrl) && <img className="artist-backdrop" src={data.backdropUrl || data.posterUrl} alt="" decoding="async" fetchPriority="high" />}
        <div className="artist-hero-art">
          <img src={data.posterUrl || data.backdropUrl} alt="" decoding="async" />
        </div>
        <div className="artist-hero-copy">
          <p className="eyebrow">Artist</p>
          <h1 dir="auto">{data.title}</h1>
          <div className="artist-stats" aria-label="Artist catalogue metadata">
            {albumCount > 0 && <span>{albumCount} album{albumCount === 1 ? '' : 's'}</span>}
            <span>{data.tracks.length} track{data.tracks.length === 1 ? '' : 's'}</span>
          </div>
          {first && (
            <div className="artist-hero-actions">
              <Button type="button" onClick={() => playTrack(first, data.tracks)}>
                <PlayIcon />
                <span>Play all</span>
              </Button>
              {data.tracks.length > 1 && (
                <Button type="button" variant="secondary" onClick={() => shuffleQueue(data.tracks)}>
                  <ShuffleIcon />
                  <span>Shuffle</span>
                </Button>
              )}
              {startRadio && (
                <Button type="button" variant="secondary" onClick={() => startRadio({ artist: data.key })}>
                  <AutoplayIcon />
                  <span>Autoplay</span>
                </Button>
              )}
            </div>
          )}
        </div>
      </section>
      {data.albums.length > 0 && (
        <section className="detail-section artist-albums-section">
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
            <p className="eyebrow">Catalogue</p>
            <h2>All songs</h2>
          </div>
          <span className="artist-track-count">{data.tracks.length} tracks</span>
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
          <div
            key={track.key}
            className={[
              'track-row',
              onAddToPlaylist ? 'has-playlist' : '',
              active ? 'active' : '',
            ].filter(Boolean).join(' ')}
          >
            <span className="track-number">{track.trackNumber || index + 1}</span>
            <a className="track-title" href={track.appHref}>
              <strong>{track.title}</strong>
              <span>{subtitleForTrack(track)}</span>
            </a>
            <span className="track-duration">{track.durationLabel}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="icon-button"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                togglePlayback(track, queue);
              }}
              aria-label={active && player.playing ? `Pause ${track.title}` : `Play ${track.title}`}
            >
              {active && player.playing ? <PauseIcon /> : <PlayIcon />}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="icon-button"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                addToQueue(track, true);
              }}
              aria-label={`Play ${track.title} next`}
            >
              <ListIcon />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="icon-button"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                addToQueue(track, false);
              }}
              aria-label={`Add ${track.title} to queue`}
            >
              <span aria-hidden="true">+</span>
            </Button>
            {onAddToPlaylist && (
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="icon-button"
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  onAddToPlaylist(track);
                }}
                aria-label={`Add ${track.title} to playlist`}
              >
                <ListPlusIcon />
              </Button>
            )}
          </div>
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
