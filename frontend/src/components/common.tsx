import { FilmIcon } from '../icons';

export function LoadingRows({ variant = 'hub' }: { variant?: 'hub' | 'detail' | 'grid' | 'music-grid' | 'playlist' }) {
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
  if (variant === 'grid') {
    return (
      <div className="skeleton-grid" aria-label="Loading">
        {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map(i => (
          <div key={i} className="skeleton-grid-item" style={{ '--i': i } as React.CSSProperties}>
            <div className="skeleton-block skeleton-grid-art" />
            <div className="skeleton-block skeleton-grid-title" style={{ animationDelay: `${i * 40 + 120}ms` }} />
            <div className="skeleton-block skeleton-grid-sub" style={{ animationDelay: `${i * 40 + 180}ms` }} />
          </div>
        ))}
      </div>
    );
  }
  if (variant === 'music-grid') {
    return (
      <div className="skeleton-grid skeleton-music-grid" aria-label="Loading">
        {[0, 1, 2, 3, 4, 5, 6, 7].map(i => (
          <div key={i} className="skeleton-grid-item" style={{ '--i': i } as React.CSSProperties}>
            <div className="skeleton-block skeleton-grid-square" />
            <div className="skeleton-block skeleton-grid-title" style={{ animationDelay: `${i * 40 + 120}ms` }} />
            <div className="skeleton-block skeleton-grid-sub" style={{ animationDelay: `${i * 40 + 180}ms` }} />
          </div>
        ))}
      </div>
    );
  }
  if (variant === 'playlist') {
    return (
      <div className="skeleton-playlist" aria-label="Loading">
        <div className="skeleton-playlist-hero">
          <div className="skeleton-block skeleton-playlist-art" />
          <div className="skeleton-playlist-meta">
            <div className="skeleton-block skeleton-playlist-title" />
            <div className="skeleton-block skeleton-playlist-sub" />
            <div className="skeleton-block skeleton-playlist-actions" />
          </div>
        </div>
        {[0, 1, 2, 3, 4, 5].map(i => (
          <div key={i} className="skeleton-track-row" style={{ '--i': i } as React.CSSProperties}>
            <div className="skeleton-block skeleton-track-num" />
            <div className="skeleton-block skeleton-track-art" />
            <div className="skeleton-track-copy">
              <div className="skeleton-block skeleton-track-title" />
              <div className="skeleton-block skeleton-track-sub" />
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

