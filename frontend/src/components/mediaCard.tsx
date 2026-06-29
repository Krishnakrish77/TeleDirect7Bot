import { memo, useEffect, useMemo, useState, type MouseEvent } from 'react';
import { BookmarkIcon, CheckIcon, FilmIcon, MusicIcon, PlayIcon, XIcon } from '../icons';
import type { HubCard, RecommendationMeta } from '../types';
import { formatExternalRating } from '../utils/externalRating';
import { joinMetadata } from '../utils/metadata';

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
  onMarkWatched?: (card: HubCard) => void;
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
      card.year,
      countLabel(card.episodeCount, 'episode') || card.subtitle,
      countLabel(card.seasonCount, 'season'),
    ]);
    return { eyebrow: 'Series', title, subtitle };
  }
  if (card.type === 'movie') {
    const subtitle = joinMetadata([
      card.year,
      countLabel(card.variantCount, 'version') || card.subtitle,
      card.durationLabel,
      card.variantCount && card.variantCount > 1 ? '' : card.quality,
    ]);
    return { eyebrow: 'Movie', title, subtitle };
  }
  if (card.mediaKind === 'audio') {
    return { eyebrow: 'Song', title, subtitle: joinMetadata([card.artist, card.albumTitle]) };
  }
  return {
    eyebrow: 'Video',
    title,
    subtitle: card.subtitle || joinMetadata([card.year, card.durationLabel, card.quality, card.genres[0]]),
  };
}

function MediaCardBase({
  card,
  saved,
  priority = false,
  onToggleSaved,
  dismissMeta,
  onDismiss,
  onMarkWatched,
}: MediaCardProps) {
  const isMusic = card.type === 'track' || card.type === 'album';
  const width = card.aspect === 'square' ? 512 : 342;
  const height = card.aspect === 'square' ? 512 : 513;
  const display = getMediaCardDisplay(card);
  const externalRating = isMusic ? '' : formatExternalRating(card.externalRating);
  const rawProgress = useMemo(() => card.watchKey ? getLocalCwPct(card.watchKey) : 0, [card.watchKey]);
  const [markedWatched, setMarkedWatched] = useState(false);
  useEffect(() => { setMarkedWatched(false); }, [card.watchKey]);
  const progressPct = markedWatched ? 0 : rawProgress;
  const [previewOpen, setPreviewOpen] = useState(false);
  const canPreview = Boolean(card.trailerKey) && !isMusic;

  return (
    <article className={`media-card ${card.aspect === 'square' ? 'square' : 'poster'}${previewOpen ? ' previewing' : ''}`}>
      <a className="media-card-link" href={card.href}>
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
        <span className="card-copy">
          <span className="eyebrow">{display.eyebrow}</span>
          <strong dir="auto">{display.title}</strong>
          {display.subtitle && <span className="card-subtitle">{display.subtitle}</span>}
          {card.recReason && <em className="card-reason">{card.recReason}</em>}
        </span>
      </a>
      {canPreview && (
        <button
          type="button"
          className="preview-button"
          onClick={(event: MouseEvent<HTMLButtonElement>) => {
            event.preventDefault();
            event.stopPropagation();
            setPreviewOpen(true);
          }}
          aria-label={`Preview ${display.title}`}
        >
          <PlayIcon />
          <span>Preview</span>
        </button>
      )}
      {previewOpen && card.trailerKey && (
        <div className="card-preview-panel" role="dialog" aria-label={`${display.title} trailer preview`}>
          <iframe
            src={`https://www.youtube.com/embed/${encodeURIComponent(card.trailerKey)}?autoplay=1&mute=1&controls=0&rel=0&playsinline=1`}
            title={`${display.title} trailer preview`}
            allow="autoplay; encrypted-media; fullscreen"
            allowFullScreen
          />
          <button
            type="button"
            className="icon-button card-preview-close"
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
      <button
        type="button"
        className={saved ? 'save-button saved' : 'save-button'}
        onClick={(event: MouseEvent<HTMLButtonElement>) => {
          event.preventDefault();
          event.stopPropagation();
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
          onClick={(event: MouseEvent<HTMLButtonElement>) => {
            event.preventDefault();
            event.stopPropagation();
            onDismiss(dismissMeta, card);
          }}
          aria-label="Not for me"
        >
          <XIcon />
        </button>
      )}
      {progressPct > 0 && onMarkWatched && (
        <button
          type="button"
          className="mark-watched-button"
          onClick={(event: MouseEvent<HTMLButtonElement>) => {
            event.preventDefault();
            event.stopPropagation();
            try {
              const cw = JSON.parse(localStorage.getItem('td:cw') || '{}') || {};
              delete cw[card.watchKey];
              localStorage.setItem('td:cw', JSON.stringify(cw));
            } catch { /* ignore */ }
            setMarkedWatched(true);
            onMarkWatched(card);
          }}
          aria-label="Mark as watched"
        >
          <CheckIcon />
        </button>
      )}
    </article>
  );
}

export const MediaCard = memo(MediaCardBase);
