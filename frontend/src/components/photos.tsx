import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  connectPhotosChannel,
  createPhotoAlbum,
  deletePhotoAlbum,
  disconnectPhotosChannel,
  fetchPhotoAlbums,
  fetchPendingPhotoChannel,
  fetchPhotosStatus,
  fetchPhotosTimeline,
  photoFileUrl,
  photoThumbUrl,
  renamePhotoAlbum,
  restorePhotos,
  resyncPhotosLibrary,
  setAlbumPhotos,
  setPhotoFavorite,
  trashPhotos,
  uploadPhotos,
} from '../api';
import type { Photo, PhotoAlbum, PhotosChannelStatus, TimelineResponse } from '../types';
import { PhotosIcon } from '../icons';
import { Button } from './ui/button';

type TimelineData = { items: Photo[]; nextCursor: string | null };

/** Gutter between justified rows/items — kept in sync with the CSS. */
const PHOTO_GAP = 4;

function dayLabel(iso: string | null): string {
  if (!iso) return 'Unknown date';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return 'Unknown date';
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
}

/** mm:ss for video tiles, matching how photo apps label clips. */
function formatDuration(seconds: number | null): string {
  if (!seconds || seconds < 1) return '';
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  return `${minutes}:${String(total % 60).padStart(2, '0')}`;
}

/** Consistent, user-visible description for a failed Photos API call. */
function describeError(err: unknown, fallback: string): string {
  const detail = err instanceof Error && err.message ? err.message : '';
  return detail ? `${fallback}: ${detail}` : fallback;
}

function usePhotoStatus() {
  const [status, setStatus] = useState<PhotosChannelStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const reload = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setStatus(await fetchPhotosStatus());
    } catch (err) {
      // Distinguish "feature/Mongo unavailable" from "no channel yet" —
      // otherwise onboarding invites the user into a connect flow that can
      // only fail.
      setStatus(null);
      setError(err instanceof Error ? err.message : 'Photos is unavailable');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  return { status, loading, error, reload };
}

// ── Connect wizard ────────────────────────────────────────────────────────

export function PhotosConnectPage({
  onConnected,
  status,
}: {
  onConnected: () => void;
  status?: PhotosChannelStatus | null;
}) {
  const [channel, setChannel] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const detectCancelled = useRef(false);
  const detectAbort = useRef<AbortController | null>(null);

  const botLabel = status?.botUsername ? `@${status.botUsername}` : 'the bot';
  const addUrl = status?.addToChannelUrl;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      const result = await connectPhotosChannel(channel.trim());
      if (result.error) {
        setError(result.error);
      } else {
        onConnected();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Connection failed');
    } finally {
      setBusy(false);
    }
  };

  /**
   * "Continue": ask the server which channel the bot was just added to.
   * The bot receives Telegram's own membership update, so no id or link is
   * needed. Polls briefly because the user may still be in Telegram when they
   * press it, then falls back to the manual field.
   */
  // Leaving the page must stop the polling loop, not keep it running for a
  // minute against an unmounted component.
  useEffect(() => () => {
    detectCancelled.current = true;
    detectAbort.current?.abort();
  }, []);

  const detectChannel = async () => {
    setError('');
    setDetecting(true);
    detectCancelled.current = false;
    const controller = new AbortController();
    detectAbort.current = controller;
    try {
      for (let attempt = 0; attempt < 20 && !detectCancelled.current; attempt += 1) {
        const pending = await fetchPendingPhotoChannel(controller.signal);
        if (pending.channelId) {
          const result = await connectPhotosChannel(String(pending.channelId));
          if (result.error) {
            setError(result.error);
            return;
          }
          onConnected();
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 3000));
      }
      if (!detectCancelled.current) {
        setError(
          `The bot has not reported a channel yet. Check that ${botLabel} is an administrator of it, `
          + 'then press Continue again. Added the bot earlier? Remove and re-add it, or link it manually below.',
        );
        setShowManual(true);
      }
    } catch (err) {
      if (detectCancelled.current) return; // unmounted or cancelled: stay quiet
      setError(describeError(err, 'Could not check for the channel'));
      setShowManual(true);
    } finally {
      if (detectAbort.current === controller) detectAbort.current = null;
      if (!detectCancelled.current) setDetecting(false);
    }
  };

  return (
    <div className="photos-connect">
      <h1>TeleDirect Photos <span className="photos-beta-badge" title="TeleDirect Photos is in beta — features may change">Beta</span></h1>
      <p className="photos-connect__lede">
        Your photos stay in your own private Telegram channel. This app only streams them back to
        you — nothing is copied to the public library. Setup takes about a minute.
      </p>
      <ol className="photos-connect__steps">
        <li>
          <span className="photos-connect__step-title">Create a private channel in Telegram</span>
          <span className="photos-connect__step-hint">
            Open Telegram → New Channel. Leave the public link (@username) empty — a public
            channel is rejected, since anyone could then read your vault.
          </span>
        </li>
        <li>
          <span className="photos-connect__step-title">Add {botLabel} as an administrator</span>
          <span className="photos-connect__step-hint">
            Post rights are enough: the bot only ever posts what you send through this page.
          </span>
          {addUrl && (
            <a className="photos-connect__bot-link" href={addUrl} target="_blank" rel="noreferrer">
              Add {botLabel} to a channel
            </a>
          )}
        </li>
        <li>
          <span className="photos-connect__step-title">Press Continue</span>
          <span className="photos-connect__step-hint">
            Telegram reports the new channel to {botLabel} automatically — no ids or links to copy.
            {addUrl ? '' : ' Add the bot as an administrator first.'}
          </span>
        </li>
      </ol>
      <div className="photos-connect__detect">
        <Button onClick={() => void detectChannel()} disabled={detecting || busy}>
          {detecting ? 'Looking for the channel…' : 'Continue'}
        </Button>
        {detecting && (
          <span className="photos-connect__detect-status" role="status">
            Waiting for Telegram to report the channel…
          </span>
        )}
      </div>
      {error && <p className="photos-connect__error" role="alert">{error}</p>}
      {!showManual ? (
        <button
          type="button"
          className="photos-connect__manual-toggle"
          onClick={() => {
            detectCancelled.current = true;
            detectAbort.current?.abort();
            setDetecting(false);
            setShowManual(true);
          }}
        >
          Already added the bot earlier? Paste a link or id instead
        </button>
      ) : (
        <form onSubmit={submit} className="photos-connect__form">
          <label className="photos-connect__label" htmlFor="photos-channel-input">
            Channel link or id
          </label>
          <div className="photos-connect__field-row">
            <input
              id="photos-channel-input"
              value={channel}
              onChange={(event) => setChannel(event.target.value)}
              placeholder="e.g. https://t.me/c/1234567890/12"
              inputMode="url"
              autoComplete="off"
              disabled={busy}
            />
            <Button type="submit" disabled={busy || !channel.trim()}>
              {busy ? 'Verifying…' : 'Connect channel'}
            </Button>
          </div>
        </form>
      )}
      <p className="photos-connect__footnote">
        New posts import automatically. Photos you posted before connecting can be pulled in with
        <strong> Scan channel history</strong> once you are set up.
      </p>
    </div>
  );
}

// ── Lightbox ──────────────────────────────────────────────────────────────

function Lightbox({
  photo,
  albums,
  onClose,
  onPrev,
  onNext,
  onToggleFavorite,
  onTrash,
  onAssignAlbum,
}: {
  photo: Photo;
  albums: PhotoAlbum[];
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  onToggleFavorite: (photo: Photo) => void;
  onTrash: (photo: Photo) => void;
  onAssignAlbum: (photo: Photo, albumId: string, member: boolean) => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'ArrowLeft') onPrev();
      if (event.key === 'ArrowRight') onNext();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, onPrev, onNext]);

  const isVideo = photo.kind === 'video';
  return (
    <div className="photos-lightbox" role="dialog" aria-label={photo.fileName}>
      <button className="photos-lightbox__close" onClick={onClose} aria-label="Close">×</button>
      <button className="photos-lightbox__nav photos-lightbox__nav--prev" onClick={onPrev} aria-label="Previous">‹</button>
      <div className="photos-lightbox__stage">
        {isVideo ? (
          <video controls autoPlay src={photoFileUrl(photo.id)} />
        ) : (
          // Preview webp, not the original: browsers can't render HEIC/RAW,
          // and casual browsing must not pull full originals from Telegram.
          <img src={photoThumbUrl(photo.id, 'preview')} alt={photo.fileName} />
        )}
        <div className="photos-lightbox__meta">
          <span>{dayLabel(photo.takenAt)}</span>
          <span>{photo.fileName}</span>
          {photo.camera && <span>{photo.camera}</span>}
        </div>
      </div>
      <button className="photos-lightbox__nav photos-lightbox__nav--next" onClick={onNext} aria-label="Next">›</button>
      <div className="photos-lightbox__actions">
        <div className="photos-lightbox__albums">
          {albums
            .filter((album) => photo.albumIds.includes(album.id))
            .map((album) => (
              <button
                key={album.id}
                className="photos-lightbox__album-chip"
                title={`Remove from ${album.name}`}
                onClick={() => onAssignAlbum(photo, album.id, false)}
              >
                {album.name} ×
              </button>
            ))}
          <select
            value=""
            aria-label="Add to album"
            onChange={(event) => {
              if (event.target.value) onAssignAlbum(photo, event.target.value, true);
            }}
          >
            <option value="">Add to album…</option>
            {albums
              .filter((album) => !photo.albumIds.includes(album.id))
              .map((album) => (
                <option key={album.id} value={album.id}>{album.name}</option>
              ))}
          </select>
        </div>
        <button onClick={() => onToggleFavorite(photo)}>{photo.favorite ? '★ Favorited' : '☆ Favorite'}</button>
        <a href={photoFileUrl(photo.id)} download={photo.fileName || true} className="photos-lightbox__download">Download original</a>
        <button onClick={() => onTrash(photo)}>Delete</button>
      </div>
    </div>
  );
}

// ── Timeline (justified rows) ─────────────────────────────────────────────

/** Aspect ratio of a photo, tolerating missing dimensions. */
function aspectRatio(photo: Photo): number {
  const w = Number(photo.width) || 0;
  const h = Number(photo.height) || 0;
  if (w > 0 && h > 0) return Math.min(3, Math.max(0.33, w / h));
  return 1;
}

export interface JustifiedRow {
  items: Photo[];
  height: number;
}

/** How far a trailing partial row may grow. Deliberately small: a big
 *  growth here makes the last row a visibly different height band from the
 *  rows above it, which reads worse than a ragged right edge. */
const PARTIAL_ROW_MAX_SCALE = 1.15;

/**
 * Flickr/Google-Photos style justified layout: rows are filled edge to edge
 * with the photos' true aspect ratios (nothing is centre-cropped), and the
 * row height is what flexes. Square grids crop every portrait shot, which is
 * why serious photo apps do not use them for browsing.
 */
export function buildJustifiedRows(
  photos: Photo[],
  containerWidth: number,
  targetHeight: number,
  gap: number,
): JustifiedRow[] {
  const rows: JustifiedRow[] = [];
  if (!photos.length || containerWidth <= 0) {
    return photos.length ? [{ items: photos, height: targetHeight }] : [];
  }
  let current: Photo[] = [];
  let ratioSum = 0;
  const pushRow = (items: Photo[], ratios: number[], fill: boolean) => {
    const available = containerWidth - gap * Math.max(0, items.length - 1);
    const ratioSum = ratios.reduce((a, b) => a + b, 0);
    // Full rows are scaled so they end exactly at the right edge (the classic
    // justified look); the trailing partial row keeps the target height and
    // stays left-aligned instead of blowing one photo up to fill the width.
    // Complete rows are scaled so they end exactly at the right edge (the
    // classic justified look). A trailing partial row also tries to fill, but
    // only up to PARTIAL_ROW_MAX_SCALE — otherwise a day with one photo would
    // become a single full-width monster. Sub-pixel values on purpose:
    // rounding here would leave every row a few pixels short of the edge.
    const filled = ratioSum ? available / ratioSum : targetHeight;
    const height = fill ? filled : Math.min(filled, targetHeight * PARTIAL_ROW_MAX_SCALE);
    rows.push({ items, height });
  };

  for (const photo of photos) {
    const ratio = aspectRatio(photo);
    current.push(photo);
    ratioSum += ratio;
    // Close the row once the photos at the target height overflow the width.
    if (ratioSum * targetHeight + gap * (current.length - 1) >= containerWidth) {
      pushRow(current, current.map(aspectRatio), true);
      current = [];
      ratioSum = 0;
    }
  }
  if (current.length) {
    // Trailing partial row keeps its natural size (not stretched to fill).
    pushRow(current, current.map(aspectRatio), false);
  }
  return rows;
}

/**
 * Container width for the justified layout, measured as soon as the node is
 * attached and kept current across resizes.
 *
 * A callback ref on purpose: the timeline renders loading/empty markup first,
 * so an effect keyed on a stable ref object would run once with no node and
 * never measure — leaving the grid stuck on its placeholder.
 */
function useMeasuredWidth<T extends HTMLElement>(): [React.RefCallback<T>, number] {
  const [width, setWidth] = useState(0);
  const cleanup = useRef<(() => void) | null>(null);

  const attach = useCallback((node: T | null) => {
    cleanup.current?.();
    cleanup.current = null;
    if (!node) return;
    const measure = () => {
      const next = Math.round(node.getBoundingClientRect().width);
      setWidth((current) => (Math.abs(current - next) > 0.5 ? next : current));
    };
    measure();
    // First paint can land before layout resolves; re-measure next frame and
    // rely on the observer (plus a resize fallback) afterwards.
    const frame = requestAnimationFrame(measure);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(node);
    window.addEventListener('resize', measure);
    cleanup.current = () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, []);

  useEffect(() => () => cleanup.current?.(), []);
  return [attach, width];
}

function PhotoItem({
  photo,
  height,
  width,
  onOpen,
}: {
  photo: Photo;
  height: number;
  width: number;
  onOpen: (photo: Photo) => void;
}) {
  return (
    <button
      className="photos-item"
      style={{ height: `${height}px`, width: `${width}px` }}
      onClick={() => onOpen(photo)}
      aria-label={photo.fileName}
    >
      <img src={photoThumbUrl(photo.id, 'grid')} alt={photo.fileName} loading="lazy" />
      <span className="photos-item__shade" aria-hidden="true" />
      {photo.kind === 'video' && (
        <span className="photos-item__badge" aria-label="Video">
          ▶{formatDuration(photo.duration) && ` ${formatDuration(photo.duration)}`}
        </span>
      )}
      {photo.favorite && <span className="photos-item__fav" aria-label="Favorite">★</span>}
    </button>
  );
}

function PhotosTimeline({
  data,
  loading,
  onLoadMore,
  onOpen,
  onScan,
  onUpload,
}: {
  data: TimelineData | null;
  loading: boolean;
  onLoadMore: () => void;
  onOpen: (photo: Photo) => void;
  onScan?: () => void;
  onUpload?: () => void;
}) {
  const [timelineRef, width] = useMeasuredWidth<HTMLDivElement>();

  // One continuous justified flow with date anchors, the way Google Photos
  // and Immich browse: restarting the rows for every day leaves a tall ragged
  // strip whenever a day holds one or two photos.
  if (loading && !data) return <div className="photos-loading">Loading your library…</div>;
  if (!data?.items.length) {
    return (
      <div className="photos-empty">
        <span className="photos-empty__icon" aria-hidden="true">
          <PhotosIcon />
        </span>
        <h2>Your library is empty</h2>
        <p>Add photos and videos here, or post them to your private Telegram channel from any device.</p>
        <div className="photos-empty__actions">
          {onUpload && <Button onClick={onUpload}>Upload photos</Button>}
          {onScan && (
            <Button variant="secondary" onClick={onScan}>
              Import from Telegram
            </Button>
          )}
        </div>
        <p className="photos-empty__hint">
          Already posted to the channel? “Import from Telegram” adds those photos — it runs in the
          background, so refresh in a moment.
        </p>
      </div>
    );
  }

  return (
    <div className="photos-timeline" ref={timelineRef}>
      <TimelineFlow photos={data.items} width={width} onOpen={onOpen} />
      {data.nextCursor && (
        <Button variant="secondary" className="photos-more" onClick={onLoadMore} disabled={loading}>
          {loading ? 'Loading…' : 'Load more'}
        </Button>
      )}
    </div>
  );
}

function TimelineFlow({
  photos,
  width,
  onOpen,
}: {
  photos: Photo[];
  width: number;
  onOpen: (photo: Photo) => void;
}) {
  const targetHeight = width < 640 ? 130 : width < 1100 ? 180 : 230;
  const rows = useMemo(
    () => buildJustifiedRows(photos, width, targetHeight, PHOTO_GAP),
    [photos, width, targetHeight],
  );
  if (!width) {
    // Placeholder of the same height instead of a wrong (unmeasured) layout,
    // so the first paint does not jump when the width arrives.
    return (
      <div className="photos-rows" aria-busy="true">
        <div className="photos-row photos-row--skeleton" style={{ height: `${targetHeight}px` }} />
      </div>
    );
  }
  let lastDay: string | null = null;
  return (
    <div className="photos-rows">
      {rows.map((row) => {
        const day = dayLabel(row.items[0].takenAt);
        // A date anchor whenever the flow reaches a new day — the row may also
        // continue with the next day's photos, exactly like Google Photos.
        const anchor = day !== lastDay ? day : null;
        lastDay = day;
        return (
          <Fragment key={row.items[0].id}>
            {anchor && <h2 className="photos-day__label">{anchor}</h2>}
            <div className="photos-row" style={{ height: `${row.height}px` }}>
              {row.items.map((photo) => (
                <PhotoItem
                  key={photo.id}
                  photo={photo}
                  height={row.height}
                  width={aspectRatio(photo) * row.height}
                  onOpen={onOpen}
                />
              ))}
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}

// ── Main photos page ──────────────────────────────────────────────────────

type View = 'timeline' | 'favorites' | 'albums' | 'trash';

export function PhotosPage({ user }: { user: { sub: number | string } | null }) {
  const { status, loading: statusLoading, error: statusError, reload: reloadStatus } = usePhotoStatus();
  const [view, setView] = useState<View>('timeline');
  const [timeline, setTimeline] = useState<TimelineData | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [albums, setAlbums] = useState<PhotoAlbum[]>([]);
  const [activeAlbum, setActiveAlbum] = useState<PhotoAlbum | null>(null);
  const [lightbox, setLightbox] = useState<number>(-1);
  const [uploads, setUploads] = useState<Array<{ name: string; url: string }>>([]);
  const [uploadPercent, setUploadPercent] = useState(0);
  // Action errors (upload, favorite, album, scan) are user-initiated and
  // dismissible; load failures stay until a refresh actually succeeds — a
  // successful reload must not silently clear them, and vice versa.
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const albumInputRef = useRef<HTMLInputElement>(null);

  const signedIn = Boolean(user);

  const loadTimeline = useCallback(async (replace: boolean, view_?: View, albumId?: string) => {
    setTimelineLoading(true);
    try {
      const effectiveView = view_ ?? view;
      const qs = effectiveView !== 'timeline' ? effectiveView : undefined;
      // Pagination: pass the current cursor when appending, none when
      // replacing (fresh view / reload).
      const cursor = replace ? undefined : timeline?.nextCursor ?? undefined;
      const data = await fetchPhotosTimeline({
        cursor,
        view: qs === 'albums' ? 'timeline' : qs,
        album: albumId || undefined,
      });
      setTimeline((current) => (replace ? data : { items: [...(current?.items ?? []), ...data.items], nextCursor: data.nextCursor }));
      setLoadError('');
    } catch (err) {
      // Never leave the user staring at an empty library that only *looks*
      // like "no photos" — say the load failed and offer a retry.
      setLoadError(describeError(err, 'Could not load your library'));
      if (replace) setTimeline({ items: [], nextCursor: null });
    } finally {
      setTimelineLoading(false);
    }
  }, [view, timeline?.nextCursor]);

  const loadAlbums = useCallback(async () => {
    try {
      setAlbums((await fetchPhotoAlbums()).albums ?? []);
    } catch (err) {
      setError(describeError(err, 'Could not load your albums'));
    }
  }, []);

  useEffect(() => {
    if (!signedIn) return;
    void loadTimeline(true);
    void loadAlbums();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn, view]);

  const photos = timeline?.items ?? [];
  const openPhoto = lightbox >= 0 ? photos[lightbox] : null;

  const step = (delta: number) => {
    setLightbox((current) => {
      if (!photos.length) return -1;
      const next = (current + delta + photos.length) % photos.length;
      return next;
    });
  };

  const handleFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    setError('');
    setUploadPercent(0);
    // Object-URL previews, like a real photo app's upload tray.
    const previews = list.map((file) => ({ name: file.name, url: URL.createObjectURL(file) }));
    setUploads(previews);
    try {
      // A 200 response can still carry per-file failures (duplicates,
      // over-limit, unsupported type) — a backup UI must not report
      // success for files the vault never received.
      const { results } = await uploadPhotos(list, {
        albumId: activeAlbum?.id || undefined,
        onProgress: setUploadPercent,
      });
      const problems = (results ?? []).filter((r) => r.error || r.duplicate);
      if (problems.length) {
        setError(
          problems
            .map((r) => `${r.fileName}: ${r.error || 'already in your library (duplicate)'}`)
            .join(' · '),
        );
      }
      // Keep the active album's filter — an upload landing inside an
      // album detail must refresh that album, not the whole library.
      await loadTimeline(true, undefined, activeAlbum?.id);
      await reloadStatus();
    } catch (err) {
      setError(describeError(err, 'Upload failed'));
    } finally {
      previews.forEach((preview) => URL.revokeObjectURL(preview.url));
      setUploads([]);
      setUploadPercent(0);
    }
  };

  const onDrop = (event: React.DragEvent) => {
    event.preventDefault();
    setDragOver(false);
    if (event.dataTransfer?.files?.length) void handleFiles(event.dataTransfer.files);
  };

  const toggleFavorite = async (photo: Photo) => {
    try {
      await setPhotoFavorite(photo.id, !photo.favorite);
      setTimeline((current) => current && ({
        ...current,
        items: current.items.map((p) => p.id === photo.id ? { ...p, favorite: !p.favorite } : p),
      }));
    } catch (err) {
      setError(describeError(err, 'Could not update the favorite'));
    }
  };

  const trash = async (photo: Photo) => {
    try {
      await trashPhotos([photo.id]);
      setLightbox(-1);
      // Album-scoped delete must refresh the album view, not the library.
      await loadTimeline(true, undefined, activeAlbum?.id);
      await reloadStatus();
    } catch (err) {
      setError(describeError(err, 'Could not move the photo to the trash'));
    }
  };

  const restore = async (photo: Photo) => {
    try {
      await restorePhotos([photo.id]);
      await loadTimeline(true);
    } catch (err) {
      setError(describeError(err, 'Could not restore the photo'));
    }
  };

  const assignAlbum = async (photo: Photo, albumId: string, member: boolean) => {
    try {
      await setAlbumPhotos(albumId, [photo.id], member);
      // Album membership changes the album view too: refresh it in place so
      // a removal from an open album actually drops the tile.
      await loadTimeline(true, undefined, activeAlbum?.id);
    } catch (err) {
      setError(describeError(err, 'Could not update the album'));
    }
  };

  const syncLibrary = async () => {
    try {
      await resyncPhotosLibrary();
      await loadTimeline(true, undefined, activeAlbum?.id);
      await reloadStatus();
    } catch (err) {
      setError(describeError(err, 'Could not start the channel scan'));
    }
  };

  if (!signedIn) {
    return (
      <main className="photos-page photos-page--centered">
        <p className="photos-connect__lede">Sign in to use TeleDirect Photos.</p>
      </main>
    );
  }
  if (statusError) {
    // 503 (feature off / Mongo down) and auth/network failures both land here
    // — say so instead of dropping the user into a connect flow that cannot
    // succeed.
    return (
      <main className="photos-page photos-page--centered">
        <div className="photos-banner photos-banner--warn" role="alert">
          TeleDirect Photos isn’t available right now: {statusError}
          <Button variant="secondary" size="sm" onClick={() => void reloadStatus()}>Retry</Button>
        </div>
      </main>
    );
  }
  if (statusLoading) {
    return (
      <main className="photos-page photos-page--centered">
        <div className="photos-loading">Checking your library…</div>
      </main>
    );
  }
  if (!status?.connected) {
    return (
      <main className="photos-page">
        <PhotosConnectPage
          status={status}
          onConnected={() => {
            void reloadStatus();
            void loadTimeline(true);
          }}
        />
      </main>
    );
  }
  if (status.status && status.status !== 'active') {
    return (
      <main className="photos-page">
        <div className="photos-banner photos-banner--warn" role="alert">
          Your channel is {status.status}. Reconnect it to keep streaming originals.
          <Button
            variant="secondary"
            size="sm"
            onClick={async () => { await disconnectPhotosChannel(); await reloadStatus(); }}
          >
            Reconnect
          </Button>
        </div>
      </main>
    );
  }

  const isAlbumDetail = view === 'albums' && activeAlbum;
  const currentPhotos = photos;

  return (
    <main
      className="photos-page"
      onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
    >
      <header className="photos-header">
        <div>
          <h1 className="photos-title">
            Photos
            {status?.beta && (
              <span
                className="photos-beta-badge"
                title="TeleDirect Photos is in beta — features may change"
              >
                Beta
              </span>
            )}
          </h1>
          {typeof status?.photoCount === 'number' && status.photoCount > 0 && (
            <p className="photos-subtitle">
              {status.photoCount.toLocaleString()}{' '}
              {status.photoCount === 1 ? 'item' : 'items'} in your private channel
            </p>
          )}
        </div>
        <div className="photos-header__actions">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/*,video/*"
            hidden
            onChange={(event) => {
              if (event.target.files?.length) void handleFiles(event.target.files);
              event.target.value = '';
            }}
          />
          <Button onClick={() => fileInputRef.current?.click()}>Upload</Button>
          <Button
            variant="secondary"
            title="Look for photos in your channel that are not in this library yet"
            onClick={() => void syncLibrary()}
          >
            Import from Telegram
          </Button>
        </div>
      </header>
      <nav className="photos-tabs" aria-label="Photos sections">
        {(['timeline', 'favorites', 'albums', 'trash'] as View[]).map((v) => (
          <button
            key={v}
            className={view === v ? 'active' : ''}
            aria-current={view === v ? 'page' : undefined}
            onClick={() => { setView(v); setActiveAlbum(null); }}
          >
            {v[0].toUpperCase() + v.slice(1)}
          </button>
        ))}
      </nav>

      {isAlbumDetail && (
        <h1 className="photos-album-title">{activeAlbum!.name}</h1>
      )}

      {view === 'albums' && !activeAlbum && (
        <div className="photos-albums">
          <input
            ref={albumInputRef}
            type="text"
            placeholder="New album name"
            onKeyDown={(event) => {
              if (event.key === 'Enter' && albumInputRef.current?.value.trim()) {
                void createPhotoAlbum(albumInputRef.current.value.trim()).then(() => {
                  if (albumInputRef.current) albumInputRef.current.value = '';
                  void loadAlbums();
                });
              }
            }}
          />
          {albums.map((album) => (
            <div key={album.id} className="photos-album-row">
              <button onClick={() => { setActiveAlbum(album); void loadTimeline(true, 'albums', album.id); }}>
                {album.name}
              </button>
              <button
                aria-label={`Rename ${album.name}`}
                onClick={() => {
                  const name = window.prompt('New album name', album.name);
                  if (name && name.trim()) {
                    void renamePhotoAlbum(album.id, name.trim()).then(loadAlbums);
                  }
                }}
              >
                Rename
              </button>
              <button
                aria-label={`Delete ${album.name}`}
                onClick={() => {
                  if (window.confirm(`Delete album "${album.name}"? Photos are kept.`)) {
                    void deletePhotoAlbum(album.id).then(() => { void loadAlbums(); void loadTimeline(true); });
                  }
                }}
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      )}

      {view === 'trash' ? (
        <div className="photos-timeline">
          {currentPhotos.length === 0 && <p className="photos-empty">Trash is empty.</p>}
          {currentPhotos.map((photo) => (
            <div key={photo.id} className="photos-trash-row">
              <img src={photoThumbUrl(photo.id, 'grid')} alt={photo.fileName} loading="lazy" />
              <span>{dayLabel(photo.takenAt)}</span>
              <button onClick={() => void restore(photo)}>Restore</button>
            </div>
          ))}
        </div>
      ) : loadError ? null : (
        <PhotosTimeline
          data={timeline}
          loading={timelineLoading}
          onLoadMore={() => void loadTimeline(false, undefined, activeAlbum?.id)}
          onOpen={(photo) => setLightbox(photos.findIndex((p) => p.id === photo.id))}
          onScan={() => void syncLibrary()}
          onUpload={() => fileInputRef.current?.click()}
        />
      )}

      {(error || loadError) && (
        <div className="photos-banner photos-banner--warn" role="alert">
          {error || loadError}
          {loadError && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void loadTimeline(true, undefined, activeAlbum?.id)}
            >
              Retry
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setError('');
              setLoadError('');
            }}
          >
            Dismiss
          </Button>
        </div>
      )}
      {uploads.length > 0 && (
        <div className="photos-uploads" role="status" aria-label="Uploading">
          <div className="photos-uploads__head">
            <span>
              Uploading {uploads.length} {uploads.length === 1 ? 'file' : 'files'}…
            </span>
            <span>{uploadPercent}%</span>
          </div>
          <span className="photos-uploads__bar" aria-hidden="true">
            <span style={{ width: `${uploadPercent}%` }} />
          </span>
          <div className="photos-uploads__grid">
            {uploads.map((u) => (
              <img key={u.name} src={u.url} alt={u.name} title={u.name} />
            ))}
          </div>
        </div>
      )}
      {dragOver && <div className="photos-dropzone-hint">Drop to upload</div>}

      {openPhoto && (
        <Lightbox
          photo={openPhoto}
          albums={albums}
          onClose={() => setLightbox(-1)}
          onPrev={() => step(-1)}
          onNext={() => step(1)}
          onToggleFavorite={(p) => void toggleFavorite(p)}
          onTrash={(p) => void trash(p)}
          onAssignAlbum={(p, albumId, member) => void assignAlbum(p, albumId, member)}
        />
      )}
    </main>
  );
}

// Re-exports for callers that want the connect page directly.
export { PhotosConnectPage as default };
