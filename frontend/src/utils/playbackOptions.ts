type PlaybackSource = {
  quality?: string;
  title?: string;
  label?: string;
};

const QUALITY_DESCRIPTIONS: Record<string, string> = {
  '4k': 'Ultra HD · highest data use',
  '2160p': 'Ultra HD · highest data use',
  '1080p': 'Full HD',
  '720p': 'HD · balanced quality and data use',
  '480p': 'Standard definition · uses less data',
};

function clean(value?: string) {
  return (value || '').trim();
}

/** Give source files a human explanation instead of exposing empty metadata. */
export function playbackOptionInfo(source: PlaybackSource, mediaTitle = '') {
  const quality = clean(source.quality);
  const label = quality || 'Original upload';
  const qualityDescription = quality
    ? QUALITY_DESCRIPTIONS[quality.toLowerCase()] || `${quality} resolution`
    : 'Resolution not labelled';
  const sourceTitle = clean(source.title || source.label);
  const isDistinctTitle = sourceTitle
    && sourceTitle.toLocaleLowerCase() !== clean(mediaTitle).toLocaleLowerCase()
    && sourceTitle.toLocaleLowerCase() !== label.toLocaleLowerCase();

  return {
    label,
    description: [qualityDescription, isDistinctTitle ? sourceTitle : ''].filter(Boolean).join(' · '),
  };
}
