type PlaybackSource = {
  quality?: string;
  sourceType?: string;
  title?: string;
  label?: string;
};

/**
 * Give an unlabelled source a meaningful, compact quality label.
 *
 * The theatre-print source tag comes first (it qualifies the resolution —
 * "PreDVD · 720p" is a very different watch than a clean "720p"), the
 * parsed resolution bucket second, then a neutral fallback — never
 * "Original", which read like a quality claim.
 */
export function playbackOptionInfo(source: PlaybackSource) {
  const sourceTag = (source.sourceType || '').trim();
  const quality = (source.quality || '').trim();
  return {
    label: [sourceTag, quality].filter(Boolean).join(' · ') || 'Unknown',
  };
}
