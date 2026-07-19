// Stable per-browser device identity + friendly label, for cross-device
// "watched on <device>" handoff hints. Not a hardware id — clearing storage
// yields a new one, which is acceptable for labelling. Kept dependency-free so
// both api.ts and UI can import it without a cycle.

const DEVICE_ID_KEY = 'td:deviceId';

export function getDeviceId(): string {
  try {
    let id = localStorage.getItem(DEVICE_ID_KEY) || '';
    if (!id) {
      id = crypto?.randomUUID?.() || `d-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      localStorage.setItem(DEVICE_ID_KEY, id);
    }
    return id;
  } catch (_) {
    return '';
  }
}

export function getDeviceLabel(): string {
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent || '' : '';
  const browser = /Edg/.test(ua) ? 'Edge'
    : /OPR|Opera/.test(ua) ? 'Opera'
    : /Chrome/.test(ua) ? 'Chrome'
    : /Firefox/.test(ua) ? 'Firefox'
    : /Safari/.test(ua) ? 'Safari' : 'Browser';
  const os = /iPhone|iPad|iPod/.test(ua) ? 'iOS'
    : /Android/.test(ua) ? 'Android'
    : /Macintosh|Mac OS/.test(ua) ? 'Mac'
    : /Windows/.test(ua) ? 'Windows'
    : /Linux/.test(ua) ? 'Linux' : '';
  return os ? `${browser} · ${os}` : browser;
}
