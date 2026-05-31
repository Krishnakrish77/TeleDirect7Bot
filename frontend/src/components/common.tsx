import { FilmIcon } from '../icons';

export function LoadingRows() {
  return (
    <div className="loading-stack" aria-label="Loading">
      <span />
      <span />
      <span />
    </div>
  );
}

export function ErrorPanel({ message }: { message: string }) {
  return (
    <div className="empty-state error-state">
      <FilmIcon />
      <strong>{message}</strong>
    </div>
  );
}

