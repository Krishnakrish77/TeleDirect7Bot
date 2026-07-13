import { memo, useMemo, useState, type MouseEvent } from 'react';
import { BookmarkIcon, CheckIcon, FilmIcon, MusicIcon, PlayIcon, ThumbDownIcon, ThumbUpIcon, XIcon } from '../icons';
import type { HubCard, RatingCounts, RecommendationMeta } from '../types';
import { formatExternalRating } from '../utils/externalRating';
import { isLocallyWatched } from '../utils/localWatched';
import { joinMetadata } from '../utils/metadata';
import { YOUTUBE_TRAILER_ALLOW, youtubeTrailerEmbedSrc } from '../utils/youtubeTrailer';

// Parsed once per render cycle; microtask clears it so the next render reads fresh.
let _cwCache: Record<string, { pos: number; dur: number }> | null = null;

function readCwMap(): Record<string, { pos: number; dur: number }> {
  if (_cwCache !== null) return _cwCache;
  let parsed: Record<string, { pos: number; dur: number }> = {};
  try { parsed = JSON.parse(localStorage.getItem('td:cw') || '{}') || {}; } catch { /* ignore */ }
  _cwCache = parsed;
  queueMicrotask(() => { _cwCache = null; });
  return parsed;
}

function getLocalCwPct(watchKey: string): number {
  const entry = readCwMap()[watchKey];
  if (!entry?.dur || !entry?.pos) return 0;
  const pct = entry.pos / entry.dur;
  return pct > 0.02 && pct < 0.95 ? Math.max(4, Math.min(96, Math.round(pct * 100))) : 0;
}

interface MediaCardProps {
  card: HubCard;
  saved: boolean;
  priority?: boolean;
  onToggleSaved: (card: HubCard) => void;
  dismissMeta?: RecommendationMeta | null;
  onDismiss?: (meta: RecommendationMeta, card: HubCard) => void;
  interactionDisabled?: boolean;
}


function countLabel(count: number | undefined, singular: string) {
  if (!count) return '';
  return `${count} ${singular}${count === 1 ? '' : 's'}`;
}

export function getMediaCardDisplay(card: HubCard): { eyebrow: string; title: string; subtitle: string } {
  const title = card.title;
  if (card.type === 'album') {
    return {
      eyebrow: 'Album',
      title,
      subtitle: joinMetadata([
        card.artist,
        countLabel(card.trackCount, 'track'),
      ]) || card.subtitle,
    };
  }
  if (card.type === 'track') {
    return {
      eyebrow: 'Song',
      title,
      subtitle: joinMetadata([card.artist, card.albumTitle]),
    };
  }
  if (card.type === 'series') {
    const subtitle = joinMetadata([
      countLabel(card.episodeCount, 'episode') || card.subtitle,
      countLabel(card.seasonCount, 'season'),
    ]);
    return { eyebrow: 'Series', title, subtitle };
  }
  if (card.type === 'movie') {
    const subtitle = joinMetadata([
      card.genres[0],
      card.variantCount && card.variantCount > 1 ? countLabel(card.variantCount, 'version') : '',
    ]);
    return { eyebrow: 'Movie', title, subtitle: subtitle || card.subtitle };
  }
  if (card.mediaKind === 'audio') {
    return { eyebrow: 'Song', title, subtitle: joinMetadata([card.artist, card.albumTitle]) };
  }
  return {
    eyebrow: 'Video',
    title,
    subtitle: joinMetadata([card.genres[0], card.subtitle && (!card.year || !String(card.subtitle).includes(String(card.year))) ? card.subtitle : ''])
      || card.subtitle
      || joinMetadata([card.durationLabel, card.quality]),
  };
}

export function getMediaCardMetaItems(card: HubCard): string[] {
  if (card.type === 'track' || card.type === 'album' || card.mediaKind === 'audio') return [];
  return [
    card.year ? String(card.year) : '',
    card.durationLabel,
    card.quality,
  ].filter((value, index, items) => Boolean(value) && items.indexOf(value) === index);
}

function communityRatingLabel(counts?: RatingCounts | null) {
  const up = Number(counts?.up || 0);
  const down = Number(counts?.down || 0);
  if (up + down <= 0) return '';
  return [
    up ? `${up} up` : '',
    down ? `${down} down` : '',
  ].filter(Boolean).join(', ');
}

function MediaCardBase({
  card,
  saved,
  priority = false,
  onToggleSaved,
  dismissMeta,
  onDismiss,
  interactionDisabled = false,
}: MediaCardProps) {
  const isMusic = card.type === 'track' || card.type === 'album';
  const width = card.aspect === 'square' ? 512 : 342;
  const height = card.aspect === 'square' ? 512 : 513;
  const display = getMediaCardDisplay(card);
  const externalRating = isMusic ? '' : formatExternalRating(card.externalRating);
  const metaItems = getMediaCardMetaItems(card);
  const communityRating = isMusic ? '' : communityRatingLabel(card.ratingCounts);
  const rawProgress = useMemo(() => card.watchKey ? getLocalCwPct(card.watchKey) : 0, [card.watchKey]);
  const watched = !isMusic && card.type !== 'series' && (Boolean(card.watched) || isLocallyWatched(card.watchKey));
  const progressPct = watched ? 0 : rawProgress;
  const newEpisodeText = card.newEpisode
    ? [card.newEpisode.label, card.newEpisode.title].filter(Boolean).join(' · ')
    : '';
  const [previewOpen, setPreviewOpen] = useState(false);
  const canPreview = Boolean(card.trailerKey) && !isMusic;
  const preventDisabledNavigation = (event: MouseEvent<HTMLAnchorElement>) => {
    if (!interactionDisabled) return;
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <article className={`media-card ${card.aspect === 'square' ? 'square' : 'poster'}${previewOpen ? ' previewing' : ''}`}>
      <span className="poster-frame">
        <a
          className="media-card-poster-link"
          href={card.href}
          aria-label={`Open ${display.title} from poster`}
          aria-disabled={interactionDisabled || undefined}
          tabIndex={interactionDisabled ? -1 : undefined}
          onClick={preventDisabledNavigation}
        >
          <span className="poster-wrap">
            <span className="poster-placeholder">
              {isMusic ? <MusicIcon /> : <FilmIcon />}
            </span>
            <img
              className="poster-image"
              src={card.posterUrl}
              alt=""
              width={width}
              height={height}
              loading={priority ? 'eager' : 'lazy'}
              decoding="async"
              fetchPriority={priority ? 'high' : undefined}
              draggable={false}
              onLoad={(event) => {
                const image = event.currentTarget;
                const decode = image.decode?.();
                if (decode) {
                  void decode
                    .catch(() => undefined)
                    .finally(() => image.classList.add('ready'));
                  return;
                }
                image.classList.add('ready');
              }}
              onError={(event) => {
                event.currentTarget.hidden = true;
              }}
            />
            {progressPct > 0 && (
              <span className="card-progress" aria-hidden="true">
                <span style={{ width: `${progressPct}%` }} />
              </span>
            )}
            {externalRating && (
              <span className="card-rating-badge" aria-label={`External rating ${externalRating}`}>
                {externalRating}
              </span>
            )}
          </span>
        </a>
        {canPreview && (
          <button
            type="button"
            className="preview-button"
            title={`Preview ${display.title}`}
            disabled={interactionDisabled}
            onClick={(event: MouseEvent<HTMLButtonElement>) => {
              event.preventDefault();
              event.stopPropagation();
              if (interactionDisabled) return;
              setPreviewOpen(true);
            }}
            aria-label={`Preview ${display.title}`}
          >
            <PlayIcon />
          </button>
        )}
        {previewOpen && card.trailerKey && (
          <div className="card-preview-panel" role="dialog" aria-label={`${display.title} trailer preview`}>
            <iframe
              src={youtubeTrailerEmbedSrc(card.trailerKey)}
              title={`${display.title} trailer preview`}
              allow={YOUTUBE_TRAILER_ALLOW}
              allowFullScreen
            />
            <button
              type="button"
              className="icon-button card-preview-close"
              title="Close preview"
              onClick={(event: MouseEvent<HTMLButtonElement>) => {
                event.preventDefault();
                event.stopPropagation();
                setPreviewOpen(false);
              }}
              aria-label="Close preview"
            >
              <XIcon />
            </button>
          </div>
        )}
      </span>
      <a
        className="media-card-link"
        href={card.href}
        aria-disabled={interactionDisabled || undefined}
        tabIndex={interactionDisabled ? -1 : undefined}
        onClick={preventDisabledNavigation}
      >
        <span className="card-copy">
          <span className="card-eyebrow-row">
            <span className="eyebrow">{display.eyebrow}</span>
            {watched && (
              <span className="card-watched-status" aria-label={`${display.title} watched`}>
                <CheckIcon />
                <span>Watched</span>
              </span>
            )}
          </span>
          <strong dir="auto">{display.title}</strong>
          {newEpisodeText && (
            <span className="card-new-episode" aria-label={`${display.title} has new episode ${newEpisodeText}`}>
              <span>New</span>
              {newEpisodeText}
            </span>
          )}
          {metaItems.length > 0 && (
            <span className="card-meta-strip" aria-label={`${display.title} metadata`}>
              {metaItems.map((item) => <span key={item} className={item === externalRating ? 'rating' : undefined}>{item}</span>)}
            </span>
          )}
          {communityRating && (
            <span className="card-community-rating" aria-label={`Community rating: ${communityRating}`}>
              {card.ratingCounts?.up ? (
                <span>
                  <ThumbUpIcon />
                  <strong>{card.ratingCounts.up}</strong>
                </span>
              ) : null}
              {card.ratingCounts?.down ? (
                <span className="down">
                  <ThumbDownIcon />
                  <strong>{card.ratingCounts.down}</strong>
                </span>
              ) : null}
            </span>
          )}
          {display.subtitle && <span className="card-subtitle">{display.subtitle}</span>}
          {card.recReason && <em className="card-reason">{card.recReason}</em>}
        </span>
      </a>
      {/*
        Save and dismiss controls stay as article-level siblings so they are not nested
        inside card links, but remain anchored to the poster's top edge.
      */}
      <button
        type="button"
        className={saved ? 'save-button saved' : 'save-button'}
        disabled={interactionDisabled}
        title={saved ? 'Remove from watchlist' : 'Add to watchlist'}
        onClick={(event: MouseEvent<HTMLButtonElement>) => {
          event.preventDefault();
          event.stopPropagation();
          if (interactionDisabled) return;
          onToggleSaved(card);
        }}
        aria-label={saved ? 'Remove from watchlist' : 'Add to watchlist'}
      >
        {saved ? <CheckIcon /> : <BookmarkIcon />}
      </button>
      {dismissMeta && onDismiss && (
        <button
          type="button"
          className="dismiss-button"
          disabled={interactionDisabled}
          title="Not for me"
          onClick={(event: MouseEvent<HTMLButtonElement>) => {
            event.preventDefault();
            event.stopPropagation();
            if (interactionDisabled) return;
            onDismiss(dismissMeta, card);
          }}
          aria-label="Not for me"
        >
          <XIcon />
        </button>
      )}
    </article>
  );
}

export const MediaCard = memo(MediaCardBase);
