import { FilmIcon } from '../icons';

export function LoadingRows({ variant = 'hub' }: { variant?: 'hub' | 'detail' }) {
  if (variant === 'hub') {
    return (
      <div className="skeleton-hub" aria-label="Loading">
        <div className="skeleton-block skeleton-hero" />
        {[0, 1, 2].map(s => (
          <div key={s} className="skeleton-shelf">
            <div className="skeleton-block skeleton-heading" style={{ animationDelay: `${s * 80}ms` }} />
            <div className="skeleton-cards">
              {[0, 1, 2, 3, 4, 5, 6].map(c => (
                <div
                  key={c}
                  className="skeleton-block skeleton-card"
                  style={{ '--i': c } as React.CSSProperties}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }
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

