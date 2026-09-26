type PlaybackSource = {
  quality?: string;
  sourceType?: string;
  title?: string;
  label?: string;
};

/**
 * Give an unlabelled source a meaningful, compact quality label.
 *
 * Precedence: parsed resolution bucket first, theatre-print source tag
 * second ("PreDVD" beats no information), then a neutral fallback —
 * never "Original", which read like a quality claim.
 */
export function playbackOptionInfo(source: PlaybackSource) {
  return {
    label: (source.quality || '').trim()
      || (source.sourceType || '').trim()
      || 'Unknown',
  };
}
