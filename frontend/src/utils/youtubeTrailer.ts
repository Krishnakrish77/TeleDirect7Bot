export const YOUTUBE_TRAILER_ALLOW =
  'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen';

export function youtubeTrailerEmbedSrc(trailerKey: string): string {
  const params = new URLSearchParams({
    controls: '1',
    playsinline: '1',
    rel: '0',
  });
  return `https://www.youtube.com/embed/${encodeURIComponent(trailerKey)}?${params.toString()}`;
}

export function youtubeTrailerWatchUrl(trailerKey: string): string {
  return `https://www.youtube.com/watch?v=${encodeURIComponent(trailerKey)}`;
}
