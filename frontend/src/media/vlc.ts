export function withVlcTracking(url: string, token: string): string {
  if (!token) return url;
  return `${url}${url.includes('?') ? '&' : '?'}vt=${encodeURIComponent(token)}`;
}

export function buildVlcHref(streamUrl: string, trackingToken = '', userAgent = navigator.userAgent || ''): string {
  const url = withVlcTracking(streamUrl, trackingToken);
  if (/Android/i.test(userAgent)) {
    const scheme = url.startsWith('https://') ? 'https' : 'http';
    const stripped = url.replace(/^https?:\/\//, '');
    return `intent://${stripped}#Intent;scheme=${scheme};package=org.videolan.vlc;type=video/*;end`;
  }
  if (/iPad|iPhone|iPod/i.test(userAgent)) {
    return `vlc-x-callback://x-callback-url/stream?url=${encodeURIComponent(url)}`;
  }
  return `vlc://${url}`;
}
