import { memo, type MouseEvent } from 'react';
import { BookmarkIcon, CheckIcon, FilmIcon, MusicIcon } from '../icons';
import type { HubCard } from '../types';

interface MediaCardProps {
  card: HubCard;
  saved: boolean;
  priority?: boolean;
  onToggleSaved: (card: HubCard) => void;
}

function MediaCardBase({
  card,
  saved,
  priority = false,
  onToggleSaved,
}: MediaCardProps) {
  const isMusic = card.type === 'track' || card.type === 'album';
  const width = card.aspect === 'square' ? 512 : 342;
  const height = card.aspect === 'square' ? 512 : 513;

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
          {card.badge && <span className="card-badge">{card.badge}</span>}
        </span>
        <span className="card-copy">
          <span className="eyebrow">{card.eyebrow}</span>
          <strong dir="auto">{card.title}{card.year ? ` (${card.year})` : ''}</strong>
          {card.subtitle && <span>{card.subtitle}</span>}
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
    </article>
  );
}

export const MediaCard = memo(MediaCardBase);
