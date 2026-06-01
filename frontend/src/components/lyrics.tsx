import { useEffect, useMemo, useRef } from 'react';
import { useLyrics } from '../hooks/lyrics';
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
    if (!lyrics.synced.length) return -1;
    let active = 0;
    for (let index = 0; index < lyrics.synced.length; index += 1) {
      if (lyrics.synced[index].t <= currentTime) active = index;
      else break;
    }
    return active;
  }, [currentTime, lyrics.synced]);

  useEffect(() => {
    const container = scrollRef.current;
    const active = activeRef.current;
    if (!container || !active || activeIndex < 0) return;
    const top = active.offsetTop - container.clientHeight / 2 + active.clientHeight / 2;
    if (typeof container.scrollTo === 'function') {
      container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    }
  }, [activeIndex]);

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
            const state = index === activeIndex ? 'active' : index < activeIndex ? 'past' : 'future';
            return (
              <button
                key={`${line.t}:${index}`}
                ref={index === activeIndex ? activeRef : undefined}
                type="button"
                className={`lyric-line ${state}`}
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
