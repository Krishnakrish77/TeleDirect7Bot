import { memo, type MouseEvent } from 'react';
import { BookmarkIcon, CheckIcon, FilmIcon, MusicIcon, XIcon } from '../icons';
import type { HubCard, RecommendationMeta } from '../types';

interface MediaCardProps {
  card: HubCard;
  saved: boolean;
  priority?: boolean;
  onToggleSaved: (card: HubCard) => void;
  dismissMeta?: RecommendationMeta | null;
  onDismiss?: (meta: RecommendationMeta, card: HubCard) => void;
}

export function getMediaCardDisplay(card: HubCard): { eyebrow: string; title: string; subtitle: string } {
  const title = card.title;
  if (card.type === 'album') {
    return { eyebrow: 'Album', title, subtitle: card.artist || '' };
  }
  if (card.type === 'track') {
    return { eyebrow: 'Song', title, subtitle: card.artist || card.albumTitle || '' };
  }
  if (card.type === 'series') {
    const subtitle = card.episodeCount
      ? `${card.episodeCount} episode${card.episodeCount === 1 ? '' : 's'}`
      : card.subtitle;
    return { eyebrow: 'Series', title, subtitle };
  }
  if (card.type === 'movie') {
    const subtitle = card.variantCount && card.variantCount > 1
      ? `${card.variantCount} version${card.variantCount === 1 ? '' : 's'}`
      : card.durationLabel || '';
    return { eyebrow: 'Movie', title, subtitle };
  }
  if (card.mediaKind === 'audio') {
    return { eyebrow: 'Song', title, subtitle: card.artist || card.albumTitle || '' };
  }
  return { eyebrow: 'Video', title, subtitle: card.durationLabel || card.genres[0] || '' };
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
