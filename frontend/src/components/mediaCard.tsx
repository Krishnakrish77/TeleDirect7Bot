import { memo, type MouseEvent } from 'react';
import { BookmarkIcon, CheckIcon, FilmIcon, MusicIcon, XIcon } from '../icons';
import type { HubCard, RecommendationMeta } from '../types';

function getLocalCwPct(watchKey: string): number {
  try {
    const raw = localStorage.getItem('td:cw');
    if (!raw) return 0;
    const entry = JSON.parse(raw)[watchKey];
    if (!entry?.dur || !entry?.pos) return 0;
    const pct = entry.pos / entry.dur;
    return pct > 0.02 && pct < 0.95 ? Math.max(4, Math.min(96, Math.round(pct * 100))) : 0;
  } catch {
    return 0;
  }
}

interface MediaCardProps {
  card: HubCard;
  saved: boolean;
  priority?: boolean;
  onToggleSaved: (card: HubCard) => void;
  dismissMeta?: RecommendationMeta | null;
  onDismiss?: (meta: RecommendationMeta, card: HubCard) => void;
}

function joinMetadata(parts: Array<string | number | null | undefined>) {
  const seen = new Set<string>();
  return parts
    .map((part) => String(part || '').trim())
    .filter(Boolean)
    .filter((part) => {
      const key = part.toLocaleLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .join(' - ');
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
}: MediaCardProps) {
  const isMusic = card.type === 'track' || card.type === 'album';
  const width = card.aspect === 'square' ? 512 : 342;
  const height = card.aspect === 'square' ? 512 : 513;
  const display = getMediaCardDisplay(card);
  const progressPct = card.watchKey ? getLocalCwPct(card.watchKey) : 0;

  return (
    <article className={`media-card ${card.aspect === 'square' ? 'square' : 'poster'}`}>
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
        </span>
        <span className="card-copy">
          <span className="eyebrow">{display.eyebrow}</span>
          <strong dir="auto">{display.title}</strong>
          {display.subtitle && <span>{display.subtitle}</span>}
        </span>
      </a>
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
    </article>
  );
}

export const MediaCard = memo(MediaCardBase);
