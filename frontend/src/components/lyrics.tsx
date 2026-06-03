import { useEffect, useMemo, useRef, useState } from 'react';
import { lyricsActiveIndex, useLyrics } from '../hooks/lyrics';
import { XIcon } from '../icons';
import type { WatchTrack } from '../types';

export function LyricsPanel({
  track,
  currentTime,
  seek,
  className = '',
}: {
  track: WatchTrack | null;
  currentTime: number;
  seek: (seconds: number) => void;
  className?: string;
}) {
  const { loading, lyrics } = useLyrics(track);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const activeRef = useRef<HTMLButtonElement | null>(null);
  const activeIndex = useMemo(() => {
    return lyricsActiveIndex(lyrics.synced, currentTime);
  }, [currentTime, lyrics.synced]);
  const displayIndex = activeIndex >= 0
    ? activeIndex
    : lyrics.synced.length ? 0 : -1;

  useEffect(() => {
    const container = scrollRef.current;
    const active = activeRef.current;
    if (!container || !active || displayIndex < 0) return;
    const top = active.offsetTop - container.clientHeight / 2 + active.clientHeight / 2;
    if (typeof container.scrollTo === 'function') {
      container.scrollTo({ top: Math.max(0, top), behavior: 'auto' });
    }
  }, [displayIndex]);

  if (!track) return null;

  return (
    <section className={['lyrics-panel', className].filter(Boolean).join(' ')} aria-label="Lyrics">
      <div className="lyrics-heading">
        <div>
          <p className="eyebrow">Lyrics</p>
          <h2>{track.title}</h2>
        </div>
        {!loading && !lyrics.unavailable && <span>LRCLIB</span>}
      </div>

      {loading ? (
        <div className="lyrics-empty">Loading lyrics...</div>
      ) : lyrics.synced.length ? (
        <div className="lyrics-lines" ref={scrollRef}>
          {lyrics.synced.map((line, index) => {
            const state = index === displayIndex
              ? 'active'
              : activeIndex >= 0 && index < activeIndex ? 'past' : 'future';
            return (
              <button
                key={`${line.t}:${index}`}
                ref={index === displayIndex ? activeRef : undefined}
                type="button"
                className={`lyric-line ${state}`}
                aria-current={index === displayIndex ? 'true' : undefined}
                onClick={() => seek(line.t)}
              >
                {line.text || '\u00a0'}
              </button>
            );
          })}
        </div>
      ) : lyrics.plain ? (
        <p className="lyrics-plain">{lyrics.plain}</p>
      ) : (
        <div className="lyrics-empty">No lyrics available</div>
      )}
    </section>
  );
}

export function LyricsFlipCard({
  track,
  currentTime,
  seek,
}: {
  track: WatchTrack | null;
  currentTime: number;
  seek: (seconds: number) => void;
}) {
  const [flipped, setFlipped] = useState(false);
  if (!track) return null;

  return (
    <div className={flipped ? 'lyrics-flip-card flipped' : 'lyrics-flip-card'}>
      <div className="lyrics-flip-inner">
        <div className="lyrics-flip-face lyrics-flip-front" aria-hidden={flipped || undefined}>
          <img src={track.posterUrl || track.thumbUrl} alt="" decoding="async" />
          <button
            type="button"
            className="lyrics-flip-toggle"
            onClick={() => setFlipped(true)}
            aria-label="Show lyrics"
          >
            <span>Lyrics</span>
          </button>
        </div>
        <div className="lyrics-flip-face lyrics-flip-back" aria-hidden={!flipped || undefined}>
          <div className="lyrics-flip-back-header">
            <span>Lyrics</span>
            <button type="button" onClick={() => setFlipped(false)} aria-label="Hide lyrics">
              <XIcon />
            </button>
          </div>
          {flipped && (
            <LyricsPanel
              className="lyrics-flip-panel"
              track={track}
              currentTime={currentTime}
              seek={seek}
            />
          )}
        </div>
      </div>
    </div>
  );
}
