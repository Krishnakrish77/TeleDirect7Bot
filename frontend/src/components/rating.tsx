import { useEffect, useState } from 'react';
import { fetchRating, setRating } from '../api';
import { ThumbDownIcon, ThumbUpIcon } from '../icons';
import type { RatingCounts } from '../types';

function numericId(value: string | number | null | undefined): string {
  const raw = String(value || '');
  return /^\d+$/.test(raw) ? raw : '';
}

export function RatingControls({ messageId }: { messageId: string | number | null | undefined }) {
  const id = numericId(messageId);
  const [rating, setLocalRating] = useState<'up' | 'down' | null>(null);
  const [counts, setCounts] = useState<RatingCounts>({ up: 0, down: 0 });
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!id) return undefined;
    const controller = new AbortController();
    fetchRating(id, controller.signal)
      .then((data) => {
        setLocalRating(data.rating);
        setCounts(data.counts || { up: 0, down: 0 });
        setLoaded(true);
      })
      .catch(() => setLoaded(false));
    return () => controller.abort();
  }, [id]);

  if (!id || !loaded) return null;

  const vote = (next: 'up' | 'down') => {
    setRating(id, next)
      .then((data) => {
        setLocalRating(data.rating);
        setCounts(data.counts || { up: 0, down: 0 });
      })
      .catch(() => undefined);
  };

  return (
    <div className="rating-controls" aria-label="Title rating">
      <button type="button" className={rating === 'up' ? 'active' : ''} onClick={() => vote('up')} aria-label="Rate up">
        <ThumbUpIcon />
        <strong>{counts.up || ''}</strong>
      </button>
      <button type="button" className={rating === 'down' ? 'active down' : 'down'} onClick={() => vote('down')} aria-label="Rate down">
        <ThumbDownIcon />
        <strong>{counts.down || ''}</strong>
      </button>
    </div>
  );
}
