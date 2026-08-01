type PlaybackSource = {
  quality?: string;
  title?: string;
  label?: string;
};

/** Give an unlabelled source a meaningful, compact quality label. */
export function playbackOptionInfo(source: PlaybackSource) {
  return { label: (source.quality || '').trim() || 'Original' };
}
