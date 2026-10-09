import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  AlbumIcon,
  ArrowLeftIcon,
  CheckIcon,
  FilterIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  DownloadIcon,
  ImageIcon,
  InfoIcon,
  MoreVerticalIcon,
  PencilIcon,
  PhotosIcon,
  PlayIcon,
  PlusIcon,
  RestoreIcon,
  SearchIcon,
  StarIcon,
  TrashIcon,
  XIcon,
} from '../icons';
import LightboxRoot from 'yet-another-react-lightbox';
import type { Slide } from 'yet-another-react-lightbox';
import Thumbnails from 'yet-another-react-lightbox/plugins/thumbnails';
import Video from 'yet-another-react-lightbox/plugins/video';
import Zoom from 'yet-another-react-lightbox/plugins/zoom';
import 'yet-another-react-lightbox/styles.css';
import 'yet-another-react-lightbox/plugins/thumbnails.css';
import {
  connectPhotosChannel,
  createPhotoAlbum,
  deletePhotoAlbum,
  disconnectPhotosChannel,
  fetchPendingPhotoChannel,
  fetchPhotoAlbums,
  fetchPhotoFacets,
  fetchPhotosStatus,
  fetchPhotosTimeline,
  photoFileUrl,
  photoThumbUrl,
  renamePhotoAlbum,
  restorePhotos,
  setAlbumPhotos,
  setPhotoFavorite,
  trashPhotos,
  uploadPhotos,
} from '../api';
import type { Photo, PhotoAlbum, PhotoFacets, PhotoSearchParams, PendingPhotoChannel, PhotosChannelStatus, TimelineResponse } from '../types';
import { resyncPhotosLibrary } from '../api';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogTitle } from './ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

type TimelineData = { items: Photo[]; nextCursor: string | null };

const PHOTO_GAP = 3;

// Zoom density for the timeline grid (target row height in px). The slider
// morphs between these; a pinched-cell range like Google Photos' zoom.
const ROW_HEIGHT_STEPS = [110, 150, 190, 240, 320] as const;

export function dayKey(iso: string | null): string {
  return (iso || '').slice(0, 10) || 'unknown';
}

function dayLabel(iso: string | null): string {
  if (!iso) return 'Unknown date';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return 'Unknown date';
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
}

/** Short month label for the scrubber bubble ("Sep 2026"). */
function scrubberLabel(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
}

/**
 * Touch/narrow layout flag for the lightbox (filmstrip off, actions in an
 * overflow menu). jsdom has no matchMedia — desktop is the safe default there.
 */
function isNarrowViewport(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia('(max-width: 680px)').matches || window.matchMedia('(pointer: coarse)').matches;
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return '';
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

function describeError(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

function usePhotoStatus() {
  const [status, setStatus] = useState<PhotosChannelStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const reload = useCallback(async (): Promise<PhotosChannelStatus | null> => {
    setLoading(true);
    try {
      const next = await fetchPhotosStatus();
      setStatus(next);
      setError('');
      return next;
    } catch (err) {
      setError(describeError(err, 'Could not reach the photo service'));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  return { status, loading, error, reload };
}

// ── Connect wizard ────────────────────────────────────────────────────────

export function PhotosConnectPage({
  status,
  onConnected,
}: {
  status: PhotosChannelStatus | null;
  onConnected: () => void;
}) {
  const botUsername = status?.botUsername || 'TeleDirect7Bot';
  const botLabel = `@${botUsername}`;
  const addUrl = status?.addToChannelUrl
    || `https://t.me/${botUsername}?startchannel=true&admin=post_messages`;
  const [channel, setChannel] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const [pending, setPending] = useState<PendingPhotoChannel | null>(null);

  // Poll the handshake while the wizard shows the "press Continue" step:
  // the bot records which channel the user just added it to.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await fetchPendingPhotoChannel();
        if (!cancelled) setPending(data?.channelId ? data : null);
      } catch { /* handshake polling is best-effort */ }
    };
    const timer = setInterval(poll, 2500);
    void poll();
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const connect = async (value: string) => {
    setError('');
    setBusy(true);
    try {
      const result = await connectPhotosChannel(value.trim());
      if (result.error) {
        setError(result.error);
      } else {
        onConnected();
      }
    } catch (err) {
      setError(describeError(err, 'Connection failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="photos-connect">
      <h1>TeleDirect Photos <span className="photos-beta-badge">Beta</span></h1>
      <p className="photos-connect__lede">
        Private photo backup backed by your own Telegram channel. Create a private channel,
        add {botLabel} as an administrator, then press Continue.
      </p>
      <ol className="photos-connect__steps">
        <li>
          <span className="photos-connect__step-title">Create a private channel in Telegram</span>
          <span className="photos-connect__step-hint">Any name — this becomes your photo vault.</span>
        </li>
        <li>
          <span className="photos-connect__step-title">
            <a className="photos-connect__bot-link" href={addUrl} target="_blank" rel="noreferrer">
              Add {botLabel} to a channel
            </a>
          </span>
          <span className="photos-connect__step-hint">
            The bot needs post rights to receive your uploads.
          </span>
        </li>
        <li>
          <span className="photos-connect__step-title">Press Continue</span>
          <span className="photos-connect__step-hint">
            The bot detects the new channel automatically.
          </span>
        </li>
      </ol>
      <div className="photos-connect__detect">
        <Button
          onClick={() => {
            // Re-poll on click: the 2.5s poll may not have caught the
            // handshake yet, and the click IS the user saying "look now".
            (async () => {
              try {
                const fresh = pending ?? await fetchPendingPhotoChannel().catch(() => null);
                if (fresh?.channelId) {
                  setPending(fresh);
                  await connect(String(fresh.channelId));
                } else {
                  setError('Add the bot to your channel first — still waiting for the handshake.');
                }
              } catch {
                setError('Add the bot to your channel first — still waiting for the handshake.');
              }
            })();
          }}
          disabled={busy}
        >
          Continue
        </Button>
        {pending && (
          <span className="photos-connect__detect-status" role="status">
            Detected {pending.title || pending.channelId} — press Continue.
          </span>
        )}
      </div>
      {error && <p className="photos-connect__error" role="alert">{error}</p>}
      <button className="photos-connect__manual-toggle" onClick={() => setShowManual((v) => !v)}>
        {showManual ? 'Hide manual link' : 'Already added the bot earlier? Paste a link instead'}
      </button>
      {showManual && (
        <form onSubmit={(event) => { event.preventDefault(); void connect(channel); }} className="photos-connect__form">
          <label className="photos-connect__label" htmlFor="photos-channel-input">
            Channel link or id
          </label>
          <div className="photos-connect__field-row">
            <input
              id="photos-channel-input"
              value={channel}
              onChange={(event) => setChannel(event.target.value)}
              placeholder="https://t.me/c/…/3 or -1001234567890"
              disabled={busy}
            />
            <Button type="submit" disabled={busy || !channel.trim()}>
              Connect channel
            </Button>
          </div>
        </form>
      )}
      <p className="photos-connect__footnote">
        Only your Telegram account can see this library — every request is scoped to your session.
      </p>
    </div>
  );
}

// ── Day grouping ──────────────────────────────────────────────────────────

interface DayGroup {
  key: string;
  label: string;
  /** First photo's takenAt ISO — the scrubber reads this. */
  takenAt: string | null;
  photos: Photo[];
}

/** Group a sorted photo list by calendar day. */
function groupByDay(photos: Photo[]): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const photo of photos) {
    const key = dayKey(photo.takenAt);
    const last = groups[groups.length - 1];
    if (last && last.key === key) {
      last.photos.push(photo);
    } else {
      groups.push({
        key,
        label: dayLabel(photo.takenAt),
        takenAt: photo.takenAt,
        photos: [photo],
      });
    }
  }
  return groups;
}

// ── Selection model ───────────────────────────────────────────────────────

interface SelectionState {
  /** Ids in insertion order for range-select arithmetic. */
  order: string[];
  ids: Set<string>;
}

function emptySelection(): SelectionState {
  return { order: [], ids: new Set() };
}

/** Click or keyboard activation on a tile — only modifiers are read. */
type TileClickEvent = Pick<React.MouseEvent, 'shiftKey' | 'metaKey' | 'ctrlKey'>;

/** The trash endpoint accepts at most this many ids per request. */
const TRASH_BATCH_SIZE = 500;

/** Split a selection into request-sized batches so "select all" never 400s. */
export function trashBatches(ids: string[]): string[][] {
  const batches: string[][] = [];
  for (let i = 0; i < ids.length; i += TRASH_BATCH_SIZE) {
    batches.push(ids.slice(i, i + TRASH_BATCH_SIZE));
  }
  return batches;
}

/** Drag-range select: indices between anchor and head, both inclusive. */
function rangeIndices(anchor: number, head: number): [number, number] {
  return anchor <= head ? [anchor, head] : [head, anchor];
}

// ── Virtualized justified timeline ────────────────────────────────────────

/** Aspect ratio of a photo, tolerating missing dimensions. */
function aspectRatio(photo: Photo): number {
  const w = Number(photo.width) || 0;
  const h = Number(photo.height) || 0;
  if (w > 0 && h > 0) return Math.min(3, Math.max(0.33, w / h));
  return 1;
}

interface JustifiedRow {
  items: Photo[];
  height: number;
}

const PARTIAL_ROW_MAX_SCALE = 1.15;

/**
 * Flickr/Google-Photos justified layout: fill rows edge to edge with true
 * aspect ratios; the row height flexes. Pure function over (photos, width,
 * targetHeight) — the virtualizer maps row index → these rows.
 */
export function buildJustifiedRows(
  photos: Photo[],
  width: number,
  targetHeight: number,
  gap: number,
): JustifiedRow[] {
  if (width <= 0 || photos.length === 0) return [];
  const rows: JustifiedRow[] = [];
  let items: Photo[] = [];
  let ratios: number[] = [];
  const pushRow = (rowItems: Photo[], rowRatios: number[], fill: boolean) => {
    if (!rowItems.length) return;
    const arSum = rowRatios.reduce((sum, r) => sum + r, 0);
    let height = (width - gap * (rowItems.length - 1)) / arSum;
    if (!fill) {
      // Trailing partial row: grow toward the target, capped, keep ragged edge.
      const natural = targetHeight * PARTIAL_ROW_MAX_SCALE;
      height = Math.min(height, natural);
    }
    // Sub-pixel heights on purpose: rounding leaves every row a few pixels
    // short of the right edge and the justified look falls apart.
    rows.push({ items: rowItems, height });
  };
  for (const photo of photos) {
    items.push(photo);
    ratios.push(aspectRatio(photo));
    const heightIfClosed = (width - gap * (items.length - 1)) / ratios.reduce((s, r) => s + r, 0);
    if (heightIfClosed <= targetHeight) {
      pushRow(items, ratios, true);
      items = [];
      ratios = [];
    }
  }
  if (items.length) pushRow(items, ratios, false);
  return rows;
}

/** Flat virtualizer model: a mix of date-header rows and photo rows. */
type VirtualEntry =
  | { kind: 'header'; group: DayGroup; height: number }
  | { kind: 'row'; row: JustifiedRow; group: DayGroup; height: number };

/**
 * Virtualizer model: date headers interleaved with justified rows.
 *
 * Rows restart at every day boundary: a row can only carry one header, so
 * when photos from several days shared a row the later days got no header
 * and their photos were mislabeled under the first photo's day (a 3-photo
 * library shot across three months rendered as a single day). The ragged
 * trailing row on sparse days is the price of truthful dates — the same
 * tradeoff Google Photos makes.
 */
export function buildVirtualModel(groups: DayGroup[], width: number, targetHeight: number): VirtualEntry[] {
  const entries: VirtualEntry[] = [];
  const headerH = 44;
  for (const group of groups) {
    const rows = buildJustifiedRows(group.photos, width, targetHeight, PHOTO_GAP);
    if (!rows.length) continue;
    entries.push({ kind: 'header', group, height: headerH });
    for (const row of rows) entries.push({ kind: 'row', row, group, height: row.height });
  }
  return entries;
}

function PhotoTile({
  photo,
  height,
  width,
  onOpen,
  selected,
  onSelect,
  selectionActive,
}: {
  photo: Photo;
  height: number;
  width: number;
  onOpen: (photo: Photo) => void;
  selected: boolean;
  onSelect: (photo: Photo, event: TileClickEvent) => void;
  selectionActive: boolean;
}) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  // Long-press (touch) enters selection mode — Google Photos' gesture. The
  // timer dies on movement or lift, and the synthetic click after a fired
  // long-press must not also open the lightbox.
  const pressTimer = useRef<number | null>(null);
  const pressStart = useRef<{ x: number; y: number } | null>(null);
  const longPressed = useRef(false);

  const cancelPress = useCallback(() => {
    if (pressTimer.current != null) {
      window.clearTimeout(pressTimer.current);
      pressTimer.current = null;
    }
    pressStart.current = null;
  }, []);

  useEffect(() => cancelPress, [cancelPress]);

  return (
    <div
      className={`photos-item${selected ? ' is-selected' : ''}${selectionActive ? ' is-selecting' : ''}`}
      style={{ height: `${height}px`, width: `${width}px` }}
      onClick={(event) => {
        if (longPressed.current) {
          longPressed.current = false;
          return;
        }
        if (selectionActive || event.metaKey || event.ctrlKey) onSelect(photo, event);
        else onOpen(photo);
      }}
      role="button"
      tabIndex={0}
      aria-label={photo.fileName}
      aria-pressed={selected}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          if (selectionActive) onSelect(photo, event);
          else onOpen(photo);
        }
      }}
      onPointerDown={(event) => {
        if (event.pointerType !== 'touch') return;
        longPressed.current = false;
        pressStart.current = { x: event.clientX, y: event.clientY };
        pressTimer.current = window.setTimeout(() => {
          longPressed.current = true;
          onSelect(photo, { shiftKey: false, metaKey: false, ctrlKey: false });
        }, 450);
      }}
      onPointerMove={(event) => {
        const start = pressStart.current;
        if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 10) {
          cancelPress();
        }
      }}
      onPointerUp={cancelPress}
      onPointerCancel={cancelPress}
      onContextMenu={(event) => {
        // Android Safari/Chrome fire contextmenu on the same long-press.
        if (longPressed.current) event.preventDefault();
      }}
    >
      <img
        src={photoThumbUrl(photo.id, 'grid')}
        alt={photo.fileName}
        loading="lazy"
        decoding="async"
        className={`photos-item__img${loaded ? ' is-loaded' : ''}${failed ? ' is-failed' : ''}`}
        onLoad={() => setLoaded(true)}
        onError={() => {
          // Thumb 404s while the backend is still generating (ingest
          // pipeline lag / regen back-off). One quiet retry after 2s —
          // most thumbs appear without any user action.
          if (failed) return;
          setFailed(true);
          window.setTimeout(() => {
            const img = new window.Image();
            img.onload = () => setLoaded(true);
            img.onerror = () => setFailed(true);
            img.src = `${photoThumbUrl(photo.id, 'grid')}?r=${Date.now()}`;
            setFailed(false);
          }, 2000);
        }}
        draggable={false}
      />
      <span className="photos-item__shade" aria-hidden="true" />
      <button
        type="button"
        className="photos-item__check"
        aria-label={`Select ${photo.fileName}`}
        aria-pressed={selected}
        tabIndex={-1}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={(event) => {
          event.stopPropagation();
          onSelect(photo, event);
        }}
      >
        <CheckIcon />
      </button>
      {photo.kind === 'video' && (
        <span className="photos-item__badge" aria-label="Video">
          <PlayIcon />
          {formatDuration(photo.duration) && ` ${formatDuration(photo.duration)}`}
        </span>
      )}
      {photo.favorite && (
        <span className="photos-item__fav" aria-label="Favorite">
          <StarIcon filled />
        </span>
      )}
    </div>
  );
}

function TimelineFlow({
  photos,
  width,
  targetHeight,
  onOpen,
  selection,
  onTileClick,
  onToggleDay,
  onPinchStep,
}: {
  photos: Photo[];
  width: number;
  targetHeight: number;
  onOpen: (photo: Photo) => void;
  selection: SelectionState;
  onTileClick: (photo: Photo, index: number, event: TileClickEvent) => void;
  onToggleDay: (group: DayGroup) => void;
  /** Pinch gesture changed density: +1 zoom in (larger thumbs), -1 zoom out. */
  onPinchStep: (delta: number) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // Two-pointer pinch → grid density (Google Photos' signature gesture).
  // Steps fire when the pinch distance grows/shrinks past a ratio band, then
  // re-baseline so a continuous pinch walks through multiple steps.
  const pinch = useRef<{ base: number | null; points: Map<number, { x: number; y: number }> }>({
    base: null,
    points: new Map(),
  });

  const pinchHandlers = {
    onPointerDown: (event: React.PointerEvent) => {
      if (event.pointerType !== 'touch') return;
      pinch.current.points.set(event.pointerId, { x: event.clientX, y: event.clientY });
    },
    onPointerMove: (event: React.PointerEvent) => {
      if (!pinch.current.points.has(event.pointerId)) return;
      pinch.current.points.set(event.pointerId, { x: event.clientX, y: event.clientY });
      const points = [...pinch.current.points.values()];
      if (points.length < 2) return;
      const dist = Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
      const base = pinch.current.base;
      if (base == null) {
        pinch.current.base = dist;
        return;
      }
      if (dist > base * 1.22) {
        onPinchStep(1);
        pinch.current.base = dist;
      } else if (dist < base / 1.22) {
        onPinchStep(-1);
        pinch.current.base = dist;
      }
    },
    onPointerUp: (event: React.PointerEvent) => {
      pinch.current.points.delete(event.pointerId);
      if (pinch.current.points.size < 2) pinch.current.base = null;
    },
    onPointerCancel: (event: React.PointerEvent) => {
      pinch.current.points.delete(event.pointerId);
      if (pinch.current.points.size < 2) pinch.current.base = null;
    },
  };
  const groups = useMemo(() => groupByDay(photos), [photos]);
  const entries = useMemo(
    () => (width > 0 ? buildVirtualModel(groups, width, targetHeight) : []),
    [groups, width, targetHeight],
  );
  // The photos array is flat-sorted by date; mapping entries → photo index
  // lets the scrubber jump by photo index.
  const entryPhotoIndex = useMemo(() => {
    const out: number[] = [];
    let next = 0;
    for (const entry of entries) {
      out.push(next);
      if (entry.kind === 'row') next += entry.row.items.length;
    }
    return out;
  }, [entries]);

  // Own scroll container — element-based virtualization is deterministic
  // (no document-offset math). The window-virtualizer variant produced
  // offsets relative to the document while tiles were positioned relative
  // to this list, so tiles floated out of the page when listOffset lagged.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const canMeasure = mounted;
  const virtualizer = useVirtualizer({
    count: entries.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => entries[i].height + (entries[i].kind === 'header' ? 8 : PHOTO_GAP),
    overscan: 6,
  });
  // jsdom / first paint: the scroll element has no height → the virtualizer
  // would render nothing; render everything until it measures.
  const virtualItems = canMeasure && (scrollRef.current?.clientHeight || 0) > 0
    ? virtualizer.getVirtualItems()
    : null;

  // Scrubber state: the bubble shows the month/year under the drag head.
  const [scrubLabel, setScrubLabel] = useState<string | null>(null);

  const jumpToPhotoIndex = useCallback((photoIndex: number) => {
    // Binary search: find the row entry containing photoIndex.
    let lo = 0;
    let hi = entries.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const entryIdx = entryPhotoIndex[mid];
      const nextIdx = entryPhotoIndex[mid + 1] ?? photos.length;
      if (photoIndex < entryIdx) hi = mid - 1;
      else if (photoIndex >= nextIdx) lo = mid + 1;
      else {
        const target = entries[mid];
        if (target.kind === 'row') {
          virtualizer.scrollToIndex(mid, { align: 'start' });
        }
        return;
      }
    }
  }, [entries, entryPhotoIndex, photos.length, virtualizer]);

  const onScrubberDrag = useCallback((ratio: number) => {
    // ratio 0..1 over the scroll height → photo index → month label + jump.
    const photoIndex = Math.min(photos.length - 1, Math.floor(ratio * photos.length));
    const photo = photos[photoIndex];
    if (photo) setScrubLabel(scrubberLabel(photo.takenAt));
    jumpToPhotoIndex(photoIndex);
  }, [photos, jumpToPhotoIndex]);

  // Floating "current day" label — GPhotos pins the day you're scrolling
  // through. Virtualized headers are absolutely positioned (sticky can't
  // work), so track the group under the scroll top and render it pinned.
  const [floatingDay, setFloatingDay] = useState<string | null>(null);
  useEffect(() => {
    if (!virtualItems || !virtualItems.length) return;
    const scrollOffset = virtualizer.scrollOffset ?? 0;
    let current = '';
    for (const item of virtualItems) {
      const entry = entries[item.index];
      if (entry.kind === 'header' && item.start <= scrollOffset + 4) {
        current = entry.group.label;
      }
    }
    // Rows visible above their own header (continuation rows) keep the
    // last header passed.
    if (!current) {
      const first = entries[virtualItems[0].index];
      if (first) current = first.group.label;
    }
    setFloatingDay(current || null);
  }, [virtualItems, entries, virtualizer.scrollOffset]);

  return (
    <>
      {floatingDay && !scrubLabel && (
        <div className="photos-floating-day" aria-hidden="true">{floatingDay}</div>
      )}
      {scrubLabel !== null && (
        <div className="photos-scrubber-bubble" role="presentation">{scrubLabel}</div>
      )}
      <div className="photos-timeline photos-timeline--scroll" ref={scrollRef} {...pinchHandlers}>
        <div
          style={{
            height: virtualItems ? virtualizer.getTotalSize() : undefined,
            position: 'relative',
          }}
        >
        {(virtualItems ?? entries.map((entry, index) => ({
          index,
          start: entries.slice(0, index).reduce((sum, e) => sum + e.height + 8, 0),
        }))).map((virtualRow) => {
          const entry = entries[virtualRow.index];
          const style = {
            position: (virtualItems ? 'absolute' : 'relative') as 'absolute' | 'relative',
            top: 0,
            left: 0,
            width: '100%',
            transform: virtualItems ? `translateY(${virtualRow.start}px)` : undefined,
          };
          if (entry.kind === 'header') {
            // The floating pinned header already shows this day — an
            // in-flow copy right under it reads as a duplicate.
            if (virtualItems && floatingDay === entry.group.label) return null;
            const allSelected = entry.group.photos.every((p) => selection.ids.has(p.id));
            return (
              <h2 key={`h-${entry.group.key}`} className="photos-day__label" style={style}>
                <button
                  type="button"
                  className="photos-day__check"
                  aria-label={`Select all from ${entry.group.label}`}
                  aria-pressed={allSelected}
                  tabIndex={-1}
                  onClick={() => onToggleDay(entry.group)}
                >
                  <CheckIcon />
                </button>
                {entry.group.label}
              </h2>
            );
          }
          return (
            <div key={`r-${entry.group.key}-${virtualRow.index}`} className="photos-row" style={style}>
              {entry.row.items.map((photo, itemIdx) => {
                const photoIndex = entryPhotoIndex[virtualRow.index] + itemIdx;
                return (
                  <PhotoTile
                    key={photo.id}
                    photo={photo}
                    height={entry.row.height}
                    width={aspectRatio(photo) * entry.row.height}
                    onOpen={onOpen}
                    selected={selection.ids.has(photo.id)}
                    selectionActive={selection.ids.size > 0}
                    onSelect={(p, event) => onTileClick(p, photoIndex, event)}
                  />
                );
              })}
            </div>
          );
        })}
        </div>
      </div>
      <DateScrubber
        onScrub={onScrubberDrag}
        onEnd={() => setScrubLabel(null)}
        disabled={photos.length < 20}
      />
    </>
  );
}

/** Right-edge drag scrubber — Google Photos' signature date navigation. */
function DateScrubber({
  onScrub,
  onEnd,
  disabled,
}: {
  onScrub: (ratio: number) => void;
  onEnd: () => void;
  disabled: boolean;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const active = useRef(false);
  const [ratio, setRatio] = useState(0.5);
  const applyRatio = useCallback((next: number) => {
    setRatio(next);
    onScrub(next);
  }, [onScrub]);

  const ratioFromEvent = useCallback((clientY: number) => {
    const el = trackRef.current;
    if (!el) return 0;
    const rect = el.getBoundingClientRect();
    return Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
  }, []);

  useEffect(() => {
    if (disabled) return;
    const onMove = (event: PointerEvent) => {
      if (!active.current) return;
      event.preventDefault();
      onScrub(ratioFromEvent(event.clientY));
    };
    const onUp = () => {
      if (!active.current) return;
      active.current = false;
      onEnd();
    };
    window.addEventListener('pointermove', onMove, { passive: false });
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [disabled, onScrub, onEnd, ratioFromEvent]);

  if (disabled) return null;
  return (
    <div
      ref={trackRef}
      className="photos-scrubber"
      onPointerDown={(event) => {
        event.preventDefault();
        active.current = true;
        (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
        applyRatio(ratioFromEvent(event.clientY));
      }}
      role="slider"
      aria-label="Scroll by date"
      aria-orientation="vertical"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(ratio * 100)}
      tabIndex={0}
      onKeyDown={(event) => {
        // 10% steps: a keyboard scrub spans a decade in ten presses —
        // coarse enough to be useful, fine enough to steer.
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          const delta = event.key === 'ArrowDown' ? 0.1 : -0.1;
          applyRatio(Math.min(1, Math.max(0, ratio + delta)));
        }
      }}
    >
      <span className="photos-scrubber__grip" aria-hidden="true" />
    </div>
  );
}

/** Shimmer placeholder rows while the first timeline page loads. */
function TimelineSkeleton({ width }: { width: number }) {
  const effective = width > 0 ? width : 1200;
  const rows = [];
  for (let r = 0; r < 6; r += 1) {
    const tiles = [];
    let remaining = effective;
    let i = 0;
    while (remaining > 150) {
      // Deterministic pseudo-varied widths: stable across renders, mixed
      // enough to read as a photo grid rather than a striped bar.
      const w = Math.min(remaining, 150 + ((r * 7 + i * 13) % 4) * 55);
      tiles.push(<span key={i} className="photos-skeleton__tile" style={{ width: w }} />);
      remaining -= w + PHOTO_GAP;
      i += 1;
    }
    rows.push(
      <Fragment key={r}>
        {r % 2 === 0 && <span className="photos-skeleton__day" />}
        <div className="photos-skeleton__row">{tiles}</div>
      </Fragment>,
    );
  }
  return (
    <div className="photos-skeleton" role="status" aria-label="Loading your library">
      {rows}
    </div>
  );
}

function PhotosTimeline({
  data,
  loading,
  onLoadMore,
  onOpen,
  onScan,
  onUpload,
  selection,
  onTileClick,
  onToggleDay,
  onPinchStep,
  rowHeight,
  emptyView,
  activeAlbumName,
}: {
  data: TimelineData | null;
  loading: boolean;
  onLoadMore: () => void;
  onOpen: (photo: Photo) => void;
  onScan?: () => void;
  onUpload?: () => void;
  selection: SelectionState;
  onTileClick: (photo: Photo, index: number, event: TileClickEvent) => void;
  onToggleDay: (group: DayGroup) => void;
  onPinchStep: (delta: number) => void;
  rowHeight: number;
  /** Which lens is empty — drives the empty-state copy. */
  emptyView?: 'timeline' | 'favorites' | 'albums' | 'trash';
  activeAlbumName?: string;
}) {
  const [timelineRef, width] = useMeasuredWidth<HTMLDivElement>();

  if (loading && !data) {
    return (
      <div className="photos-timeline-wrap" ref={timelineRef}>
        <TimelineSkeleton width={width} />
      </div>
    );
  }
  if (!data?.items.length) {
    // A filtered view (Favorites/Trash/album) is not an empty library —
    // saying "Your library is empty" with upload CTAs is wrong when the
    // timeline has items and this lens just doesn't match any.
    const emptyCopy = {
      timeline: {
        title: 'Your library is empty',
        body: 'Add photos and videos here, or post them to your private Telegram channel from any device.',
      },
      favorites: {
        title: 'No favorites yet',
        body: 'Tap the star on a photo to pin it here.',
      },
      albums: {
        title: activeAlbumName ? `Nothing in “${activeAlbumName}” yet` : 'No albums yet',
        body: 'Add photos to an album from the lightbox, or create one below the timeline.',
      },
      trash: {
        title: 'Trash is empty',
        body: 'Deleted photos land here first — nothing to restore.',
      },
    }[emptyView ?? 'timeline'];
    const isPlainLibrary = (emptyView ?? 'timeline') === 'timeline' && !activeAlbumName;
    return (
      <div className="photos-empty">
        <span className="photos-empty__icon" aria-hidden="true">
          <PhotosIcon />
        </span>
        <h2>{emptyCopy.title}</h2>
        <p>{emptyCopy.body}</p>
        {isPlainLibrary && (
          <>
            <div className="photos-empty__actions">
              {onUpload && <Button onClick={onUpload}>Upload photos</Button>}
              {onScan && (
                <Button variant="secondary" onClick={onScan}>
                  Find missing photos
                </Button>
              )}
            </div>
            <p className="photos-empty__hint">
              Already posted to the channel? “Find missing photos” adds whatever is not in your
              library yet — it runs in the background, so refresh in a moment.
            </p>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="photos-timeline-wrap" ref={timelineRef}>
      <TimelineFlow
        photos={data.items}
        width={width}
        targetHeight={rowHeight}
        onOpen={onOpen}
        selection={selection}
        onTileClick={onTileClick}
        onToggleDay={onToggleDay}
        onPinchStep={onPinchStep}
      />
      {data.nextCursor && (
        <Button variant="secondary" className="photos-more" onClick={onLoadMore} disabled={loading}>
          {loading ? 'Loading…' : 'Load more'}
        </Button>
      )}
    </div>
  );
}

function useMeasuredWidth<T extends HTMLElement>(): [React.RefCallback<T>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  const observer = useRef<ResizeObserver | null>(null);
  const cleanup = useRef<(() => void) | null>(null);

  const attach = useCallback((node: T | null) => {
    cleanup.current?.();
    cleanup.current = null;
    ref.current = node;
    if (!node) return;
    const measure = () => setWidth(node.getBoundingClientRect().width);
    measure();
    observer.current = new ResizeObserver(measure);
    observer.current.observe(node);
    window.addEventListener('resize', measure);
    cleanup.current = () => {
      observer.current?.disconnect();
      observer.current = null;
      window.removeEventListener('resize', measure);
    };
  }, []);

  useEffect(() => () => cleanup.current?.(), []);
  return [attach, width];
}

/**
 * Height for the timeline's fill layout on touch layouts.
 *
 * Measures the page element itself (viewport minus its top minus the shell's
 * reserved bottom space) and exposes it as --photos-fill-height. The page owns
 * that height; the grid then flexes to the remainder after the header and the
 * section switcher. (Sizing BOTH page and grid from the same measured number
 * over-constrained the layout: the page clipped the switcher and left a dead
 * band below the grid.)
 */
function useGridHeight(active: boolean, revision: string): [React.RefCallback<HTMLElement>, number] {
  const [height, setHeight] = useState(0);
  const nodeRef = useRef<HTMLElement | null>(null);
  const cleanup = useRef<(() => void) | null>(null);
  const activeRef = useRef(false);

  const measure = useCallback(() => {
    const node = nodeRef.current;
    if (!node) return;
    if (!activeRef.current) return;
    // Anchor on the page element: its top is stable (below the app header) and
    // everything it must clear is inside it, so one measurement suffices.
    const reserved = parseFloat(
      getComputedStyle(document.querySelector('.app-shell') ?? document.body).paddingBottom || '0',
    );
    setHeight(
      Math.max(220, Math.round(window.innerHeight - node.getBoundingClientRect().top - reserved)),
    );
  }, []);

  const attach = useCallback((node: HTMLElement | null) => {
    cleanup.current?.();
    cleanup.current = null;
    nodeRef.current = node;
    if (!node) return;
    window.addEventListener('resize', measure);
    // The mini-player changes the shell's reserve without a resize.
    const shell = document.querySelector('.app-shell');
    const observer = shell ? new MutationObserver(measure) : null;
    observer?.observe(shell as Node, { attributes: true, attributeFilter: ['class'] });
    // Measure on attach: the effect below only re-runs when active/revision
    // change. The grid mounts late (channel status resolves first) and an
    // empty library keeps revision constant, so without this the height never
    // gets set — the fill page then collapses to its padding and its
    // overflow:hidden clips the whole view (the mobile black void).
    const frame = requestAnimationFrame(measure);
    cleanup.current = () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', measure);
      observer?.disconnect();
    };
  }, [measure]);

  // Re-measure when the grid view (re)appears — the scroller mounts then.
  // Re-measure whenever the grid's content changes (view switch, data arriving,
  // load-more): keying only on `active` left the height unset when the grid
  // mounted after the first pass, which is timing dependent and flaky.
  useEffect(() => {
    activeRef.current = active;
    if (!active) return;
    const frame = requestAnimationFrame(measure);
    return () => cancelAnimationFrame(frame);
  }, [active, revision, measure]);

  useEffect(() => () => cleanup.current?.(), []);
  return [attach, height];
}

// ── Metadata panel (lazy exifr) ───────────────────────────────────────────

type ExifSummary = {
  Make?: string;
  Model?: string;
  DateTimeOriginal?: Date;
  ExposureTime?: number;
  FNumber?: number;
  ISO?: number;
  FocalLength?: number;
  latitude?: number;
  longitude?: number;
};

/** Fetch the original and parse EXIF in the browser (lazy-loaded exifr). */
async function loadExif(photo: Photo): Promise<ExifSummary | null> {
  try {
    const [{ default: exifr }, res] = await Promise.all([
      import('exifr'),
      fetch(photoFileUrl(photo.id), { credentials: 'same-origin' }),
    ]);
    if (!res.ok) return null;
    const buf = await res.arrayBuffer();
    if (photo.kind === 'video') return null;
    const parsed = await exifr.parse(buf as ArrayBuffer, {
      pick: ['Make', 'Model', 'DateTimeOriginal', 'ExposureTime', 'FNumber', 'ISO', 'FocalLength', 'latitude', 'longitude'],
    });
    return (parsed as ExifSummary) || null;
  } catch {
    return null;
  }
}

function formatExposure(value: number | undefined): string {
  if (!value) return '';
  return value >= 1 ? `${value}s` : `1/${Math.round(1 / value)}`;
}

function MetaPanel({ photo }: { photo: Photo }) {
  const [exif, setExif] = useState<ExifSummary | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setExif(null);
    if (photo.kind === 'video') return;
    setLoading(true);
    void loadExif(photo).then((result) => {
      if (!cancelled) setExif(result);
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [photo.id, photo.kind]);

  // Location: EXIF first, stored GPS second. Both legs verify the numbers
  // themselves — older Mongo docs and partial EXIF can carry null
  // components inside a truthy object, and `null.toFixed` killed the panel.
  const coords = (lat: unknown, lon: unknown): string =>
    typeof lat === 'number' && typeof lon === 'number' && Number.isFinite(lat) && Number.isFinite(lon)
      ? `${lat.toFixed(4)}, ${lon.toFixed(4)}`
      : '';
  const location = coords(exif?.latitude, exif?.longitude) || coords(photo.gps?.lat, photo.gps?.lon);
  const hasGps = Boolean(coords(photo.gps?.lat, photo.gps?.lon));

  const rows: Array<[string, string]> = ([
    ['Taken', dayLabel(photo.takenAt)],
    ['File', photo.fileName],
    ['Size', photo.size ? `${(photo.size / (1024 * 1024)).toFixed(1)} MB` : ''],
    ['Dimensions', photo.width && photo.height ? `${photo.width} × ${photo.height}` : ''],
    ['Camera', exif?.Make || exif?.Model ? [exif?.Make, exif?.Model].filter(Boolean).join(' ') : photo.camera || ''],
    ['Exposure', exif?.ExposureTime ? formatExposure(exif.ExposureTime) : ''],
    ['Aperture', exif?.FNumber ? `f/${exif.FNumber}` : ''],
    ['ISO', exif?.ISO ? String(exif.ISO) : ''],
    ['Focal length', exif?.FocalLength ? `${Math.round(exif.FocalLength)}mm` : ''],
    ['Place', photo.place || ''],
    ['Location', location],
  ] as Array<[string, string]>).filter(([, v]) => v);

  return (
    <aside className="photos-meta" aria-label="Photo details">
      {loading && <p className="photos-meta__hint">Reading details…</p>}
      <dl className="photos-meta__rows">
        {rows.map(([label, value]) => (
          <div key={label} className="photos-meta__row">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {hasGps && photo.gps && (
        <a
          className="photos-meta__map-link"
          href={`https://www.openstreetmap.org/?mlat=${photo.gps.lat}&mlon=${photo.gps.lon}#map=15/${photo.gps.lat}/${photo.gps.lon}`}
          target="_blank"
          rel="noreferrer"
        >
          Open in map ↗
        </a>
      )}
    </aside>
  );
}

// ── Main photos page ──────────────────────────────────────────────────────

type View = 'timeline' | 'favorites' | 'albums' | 'trash';

const PHOTOS_NAV: Array<{
  key: View;
  label: string;
  icon: (props: React.SVGProps<SVGSVGElement>) => React.JSX.Element;
}> = [
  { key: 'timeline', label: 'Photos', icon: PhotosIcon },
  { key: 'favorites', label: 'Favorites', icon: StarIcon },
  { key: 'albums', label: 'Albums', icon: AlbumIcon },
  { key: 'trash', label: 'Trash', icon: TrashIcon },
];

// ── Albums cover grid ─────────────────────────────────────────────────────

function AlbumsGrid({
  albums,
  creating,
  onStartCreate,
  onCancelCreate,
  onSubmitCreate,
  nameInputRef,
  onOpen,
  onRename,
  onDelete,
}: {
  albums: PhotoAlbum[];
  creating: boolean;
  onStartCreate: () => void;
  onCancelCreate: () => void;
  onSubmitCreate: () => void;
  nameInputRef: React.RefObject<HTMLInputElement | null>;
  onOpen: (album: PhotoAlbum) => void;
  onRename: (album: PhotoAlbum) => void;
  onDelete: (album: PhotoAlbum) => void;
}) {
  return (
    <div className="photos-albums-grid">
      {creating ? (
        <form
          className="photos-album-card photos-album-card--form"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmitCreate();
          }}
        >
          <label className="photos-album-card__form-label" htmlFor="photos-new-album">
            Album name
          </label>
          <input
            id="photos-new-album"
            ref={nameInputRef}
            type="text"
            placeholder="e.g. Portugal 2026"
            // eslint-disable-next-line jsx-a11y/no-autofocus -- the card IS the focused task
            autoFocus
            onKeyDown={(event) => {
              if (event.key === 'Escape') onCancelCreate();
            }}
          />
          <div className="photos-album-card__form-actions">
            <Button size="sm" type="submit">Create</Button>
            <Button size="sm" variant="ghost" onClick={onCancelCreate}>Cancel</Button>
          </div>
        </form>
      ) : (
        <button type="button" className="photos-album-card photos-album-card--new" onClick={onStartCreate}>
          <PlusIcon />
          <span>New album</span>
        </button>
      )}
      {albums.map((album) => (
        <div key={album.id} className="photos-album-card">
          <button
            type="button"
            className="photos-album-card__cover"
            aria-label={`Open ${album.name}`}
            onClick={() => onOpen(album)}
          >
            {album.coverPhotoId ? (
              <img src={photoThumbUrl(album.coverPhotoId, 'grid')} alt="" loading="lazy" decoding="async" />
            ) : (
              <ImageIcon className="photos-album-card__placeholder" />
            )}
          </button>
          <div className="photos-album-card__meta">
            <button type="button" className="photos-album-card__name" onClick={() => onOpen(album)}>
              {album.name}
            </button>
            {typeof album.photoCount === 'number' && (
              <span className="photos-album-card__count">
                {album.photoCount.toLocaleString()} {album.photoCount === 1 ? 'item' : 'items'}
              </span>
            )}
            <span className="photos-album-card__actions">
              <button
                type="button"
                className="photos-iconbtn"
                aria-label={`Rename ${album.name}`}
                title="Rename"
                onClick={() => onRename(album)}
              >
                <PencilIcon />
              </button>
              <button
                type="button"
                className="photos-iconbtn photos-iconbtn--danger"
                aria-label={`Delete ${album.name}`}
                title="Delete album"
                onClick={() => onDelete(album)}
              >
                <TrashIcon />
              </button>
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Filter chip row (facets) ─────────────────────────────────────────────

const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

/** Kind chips are fixed (Photos / Videos); camera + month come from facets. */
function PhotosFilterBar({
  facets,
  filters,
  onChange,
}: {
  facets: PhotoFacets | null;
  filters: PhotoSearchParams;
  onChange: (next: PhotoSearchParams) => void;
}) {
  const [sheetOpen, setSheetOpen] = useState(false);
  const kindOptions: Array<{ value: string; label: string; count?: number }> = [
    { value: 'photo', label: 'Photos', count: facets?.kinds.photo },
    { value: 'video', label: 'Videos', count: facets?.kinds.video },
  ];
  const monthLabel = (m: { year: number; month: number }) =>
    `${MONTH_NAMES[m.month - 1]} ${m.year}`;
  const activeMonth = (() => {
    if (!filters.takenAfter || !filters.takenBefore) return null;
    const after = filters.takenAfter.slice(0, 10);
    const start = new Date(`${after}T00:00:00Z`);
    if (Number.isNaN(start.getTime())) return null;
    const m = { year: start.getUTCFullYear(), month: start.getUTCMonth() + 1 };
    const match = facets?.months.find((x) => x.year === m.year && x.month === m.month);
    return match || m;
  })();

  if (!facets && !filters.kind && !filters.camera && !activeMonth) return null;

  const activeCount =
    (filters.kind ? 1 : 0) +
    (filters.camera ? 1 : 0) +
    (filters.place ? 1 : 0) +
    (activeMonth ? 1 : 0);

  // One option model drives both the desktop chip row and the mobile sheet.
  const sections: Array<{
    title: string;
    options: Array<{ key: string; label: string; count?: number; active: boolean; toggle: () => void }>;
  }> = [
    {
      title: 'Type',
      options: kindOptions.map((k) => ({
        key: `kind-${k.value}`,
        label: k.label,
        count: k.count,
        active: filters.kind === k.value,
        toggle: () => onChange({ ...filters, kind: filters.kind === k.value ? undefined : k.value }),
      })),
    },
    {
      title: 'Camera',
      options: (facets?.cameras ?? []).map((c) => ({
        key: `cam-${c.camera}`,
        label: c.camera,
        count: c.count,
        active: filters.camera === c.camera,
        toggle: () => onChange({ ...filters, camera: filters.camera === c.camera ? undefined : c.camera }),
      })),
    },
    {
      title: 'Place',
      options: (facets?.places ?? []).map((p) => ({
        key: `place-${p.place}`,
        label: p.place,
        count: p.count,
        active: filters.place === p.place,
        toggle: () => onChange({ ...filters, place: filters.place === p.place ? undefined : p.place }),
      })),
    },
    {
      title: 'Month',
      options: (facets?.months ?? []).slice(0, 12).map((m) => {
        const active = Boolean(activeMonth && activeMonth.year === m.year && activeMonth.month === m.month);
        return {
          key: `mon-${m.year}-${m.month}`,
          label: monthLabel(m),
          count: m.count,
          active,
          toggle: () => onChange(active
            ? { ...filters, takenAfter: undefined, takenBefore: undefined }
            : {
                ...filters,
                takenAfter: `${m.year}-${String(m.month).padStart(2, '0')}-01`,
                // First day of the following month — the range end the
                // month still "contains".
                takenBefore: m.month === 12
                  ? `${m.year + 1}-01-01`
                  : `${m.year}-${String(m.month + 1).padStart(2, '0')}-01`,
              }),
        };
      }),
    },
  ];

  return (
    <>
      {/* Desktop: the whole option set as a chip row. */}
      <div className="photos-filters" role="group" aria-label="Filters">
        {sections.flatMap((section) =>
          section.options.map((option) => (
            <button
              key={option.key}
              type="button"
              className={`photos-chip${option.active ? ' photos-chip--active' : ''}`}
              aria-pressed={option.active}
              onClick={option.toggle}
            >
              {option.label}
              {typeof option.count === 'number' && (
                <span className="photos-chip__count">{option.count.toLocaleString()}</span>
              )}
            </button>
          )),
        )}
      </div>

      {/* Mobile: chips don't scale (kinds + cameras + places + months wrap or
          scroll off-screen), so the row collapses to one button with an
          active-count badge and the options move to a bottom sheet. */}
      <button
        type="button"
        className="photos-filters__trigger"
        aria-haspopup="dialog"
        onClick={() => setSheetOpen(true)}
      >
        <FilterIcon aria-hidden="true" />
        <span>Filters</span>
        {activeCount > 0 && <span className="photos-filters__badge">{activeCount}</span>}
      </button>
      <Dialog open={sheetOpen} onOpenChange={setSheetOpen}>
        <DialogContent className="photos-filter-sheet" aria-describedby={undefined}>
          <div className="photos-filter-sheet__head">
            <DialogTitle>Filters</DialogTitle>
            {activeCount > 0 && (
              <button
                type="button"
                className="photos-filter-sheet__clear"
                onClick={() => onChange({})}
              >
                Clear all
              </button>
            )}
          </div>
          {sections.map((section) => section.options.length > 0 && (
            <div className="photos-filter-sheet__section" key={section.title}>
              <h3>{section.title}</h3>
              <div className="photos-filter-sheet__options" role="group" aria-label={section.title}>
                {section.options.map((option) => (
                  <button
                    key={option.key}
                    type="button"
                    className={`photos-chip${option.active ? ' photos-chip--active' : ''}`}
                    aria-pressed={option.active}
                    onClick={option.toggle}
                  >
                    {option.label}
                    {typeof option.count === 'number' && (
                      <span className="photos-chip__count">{option.count.toLocaleString()}</span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </DialogContent>
      </Dialog>
    </>
  );
}

export function PhotosPage({ user, onSignIn }: { user: { sub: number | string } | null; onSignIn?: () => void }) {
  const { status, loading: statusLoading, error: statusError, reload: reloadStatus } = usePhotoStatus();
  const [view, setView] = useState<View>('timeline');
  const [timeline, setTimeline] = useState<TimelineData | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [albums, setAlbums] = useState<PhotoAlbum[]>([]);
  const [activeAlbum, setActiveAlbum] = useState<PhotoAlbum | null>(null);
  const [lightboxIndex, setLightboxIndex] = useState(-1);
  const [selection, setSelection] = useState<SelectionState>(emptySelection);
  const [rangeAnchor, setRangeAnchor] = useState<number | null>(null);
  const [rowHeight, setRowHeight] = useState<number>(() => {
    // Phones start denser: the zoom slider is hidden on touch layouts.
    if (typeof window === 'undefined') return 190;
    return window.innerWidth < 680 ? ROW_HEIGHT_STEPS[0] : window.innerWidth < 1100 ? ROW_HEIGHT_STEPS[1] : 190;
  });
  const [uploads, setUploads] = useState<Array<{ name: string; url: string }>>([]);
  const [uploadPercent, setUploadPercent] = useState(0);
  const [importState, setImportState] = useState<'idle' | 'running' | 'done' | 'paused' | 'error'>('idle');
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [dragOver, setDragOver] = useState(false);
  // Server-side search + filter chips. `query` mirrors the input (immediate),
  // `searchQ` is the debounced value actually sent to the API.
  const [query, setQuery] = useState('');
  const [searchQ, setSearchQ] = useState('');
  const [filters, setFilters] = useState<PhotoSearchParams>({});
  const [facets, setFacets] = useState<PhotoFacets | null>(null);
  const [creatingAlbum, setCreatingAlbum] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const albumInputRef = useRef<HTMLInputElement>(null);
  // Hooks must run before the early returns below (a hook after a conditional
  // return changes the hook count between renders and React unmounts the page).
  const [fillRef, gridHeight] = useGridHeight(
    view === 'timeline' || view === 'favorites',
    `${view}:${timeline?.items.length ?? 0}`,
  );

  const signedIn = Boolean(user);
  const photos = timeline?.items ?? [];

  // Server-side search: the API filters across the whole library, so the
  // visible list is just what came back. Debounce: `query` mirrors the input
  // for a live box; `searchQ` chases it and drives the fetch.
  const visiblePhotos = photos;

  // Grid, selection range math and lightbox all index the *visible* list so a
  // search filter can't shift them onto photos the user isn't looking at.
  const openPhoto = lightboxIndex >= 0 ? visiblePhotos[lightboxIndex] : null;
  const selectionActive = selection.ids.size > 0;

  useEffect(() => {
    const t = window.setTimeout(() => setSearchQ(query.trim()), 250);
    return () => window.clearTimeout(t);
  }, [query]);

  const loadTimeline = useCallback(async (replace: boolean, view_?: View, albumId?: string) => {
    setTimelineLoading(true);
    try {
      const effectiveView = view_ ?? view;
      const qs = effectiveView !== 'timeline' ? effectiveView : undefined;
      const cursor = replace ? undefined : timeline?.nextCursor ?? undefined;
      const data = await fetchPhotosTimeline({
        cursor,
        view: qs === 'albums' ? 'timeline' : qs,
        album: albumId || undefined,
        ...filters,
        q: searchQ || undefined,
      });
      setTimeline((current) => (replace ? data : { items: [...(current?.items ?? []), ...data.items], nextCursor: data.nextCursor }));
      setLoadError('');
    } catch (err) {
      setLoadError(describeError(err, 'Could not load your library'));
      if (replace) setTimeline({ items: [], nextCursor: null });
    } finally {
      setTimelineLoading(false);
    }
  }, [view, timeline?.nextCursor, filters, searchQ]);

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

  // Re-query when the debounced search or the filter chips change.
  const filterKey = `${searchQ}|${JSON.stringify(filters)}`;
  useEffect(() => {
    if (!signedIn) return;
    void loadTimeline(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn, filterKey]);

  // Facet counts follow the search box + album context (not the kind/camera
  // chips — chips must not zero themselves out).
  const facetKey = `${searchQ}|${activeAlbum?.id ?? ''}`;
  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    fetchPhotoFacets({ q: searchQ || undefined, album: activeAlbum?.id || undefined })
      .then((f) => { if (!cancelled) setFacets(f); })
      .catch(() => { if (!cancelled) setFacets(null); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn, facetKey]);

  // Selection click handling: plain click in selection mode toggles; with
  // meta/ctrl toggles individually; shift extends a range from the anchor.
  // Indices address the *visible* list so a search filter can't shift the
  // range arithmetic onto photos the user isn't looking at.
  const onTileClick = useCallback((photo: Photo, index: number, event: TileClickEvent) => {
    setSelection((current) => {
      if (event.shiftKey && rangeAnchor != null) {
        const [from, to] = rangeIndices(rangeAnchor, index);
        const ids = new Set(current.ids);
        const order = [...current.order];
        for (let i = from; i <= to; i += 1) {
          const p = visiblePhotos[i];
          if (p && !ids.has(p.id)) {
            ids.add(p.id);
            order.push(p.id);
          }
        }
        return { ids, order };
      }
      const ids = new Set(current.ids);
      const order = [...current.order];
      if (ids.has(photo.id)) {
        ids.delete(photo.id);
        order.splice(order.indexOf(photo.id), 1);
      } else {
        ids.add(photo.id);
        order.push(photo.id);
      }
      setRangeAnchor(index);
      return { ids, order };
    });
    if (!event.shiftKey) setRangeAnchor(index);
  }, [visiblePhotos, rangeAnchor]);

  const clearSelection = useCallback(() => {
    setSelection(emptySelection());
    setRangeAnchor(null);
  }, []);

  const selectAll = useCallback(() => {
    const ids = new Set(visiblePhotos.map((p) => p.id));
    setSelection({ ids, order: visiblePhotos.map((p) => p.id) });
  }, [visiblePhotos]);

  const exitSelection = useCallback(() => {
    clearSelection();
  }, []);

  // Day-header checkbox: toggle the whole day in/out of the selection.
  const toggleDay = useCallback((group: DayGroup) => {
    setSelection((current) => {
      const ids = new Set(current.ids);
      const order = [...current.order];
      if (group.photos.every((p) => ids.has(p.id))) {
        for (const p of group.photos) {
          ids.delete(p.id);
          const at = order.indexOf(p.id);
          if (at >= 0) order.splice(at, 1);
        }
      } else {
        for (const p of group.photos) {
          if (!ids.has(p.id)) {
            ids.add(p.id);
            order.push(p.id);
          }
        }
      }
      return { ids, order };
    });
  }, []);

  const onPinchStep = useCallback((delta: number) => {
    setRowHeight((current) => {
      const idx = ROW_HEIGHT_STEPS.indexOf(current as never);
      const next = Math.min(ROW_HEIGHT_STEPS.length - 1, Math.max(0, idx + delta));
      return ROW_HEIGHT_STEPS[next];
    });
  }, []);

  /** Same-origin originals → anchor downloads; staggered so the browser
   *  doesn't throttle the burst into silence. */
  const downloadSelection = useCallback((ids: string[]) => {
    ids.slice(0, 50).forEach((id, i) => {
      window.setTimeout(() => {
        const anchor = document.createElement('a');
        anchor.href = photoFileUrl(id);
        anchor.download = '';
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      }, i * 350);
    });
  }, []);

  const addSelectionToAlbum = useCallback(async (albumId: string) => {
    const ids = [...selection.ids];
    try {
      await setAlbumPhotos(albumId, ids, true);
      clearSelection();
      await loadTimeline(true, undefined, activeAlbum?.id);
      await loadAlbums();
    } catch (err) {
      setError(describeError(err, 'Could not add the photos to the album'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.ids, activeAlbum?.id, loadTimeline, loadAlbums, clearSelection]);

  const restoreSelection = useCallback(async (ids: string[]) => {
    try {
      for (const batch of trashBatches(ids)) {
        await restorePhotos(batch);
      }
      clearSelection();
      await loadTimeline(true, undefined, activeAlbum?.id);
      await reloadStatus();
    } catch (err) {
      setError(describeError(err, 'Could not restore the photos'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeAlbum?.id, loadTimeline, reloadStatus, clearSelection]);

  const openAlbum = useCallback((album: PhotoAlbum) => {
    setActiveAlbum(album);
    clearSelection();
    void loadTimeline(true, 'albums', album.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadTimeline, clearSelection]);

  const submitCreateAlbum = useCallback(async () => {
    const name = albumInputRef.current?.value.trim();
    if (!name) return;
    try {
      await createPhotoAlbum(name);
      setCreatingAlbum(false);
      await loadAlbums();
    } catch (err) {
      setError(describeError(err, 'Could not create the album'));
    }
  }, [loadAlbums]);

  const handleFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    setError('');
    setUploadPercent(0);
    const previews = list.map((file) => ({ name: file.name, url: URL.createObjectURL(file) }));
    setUploads(previews);
    try {
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

  const trash = async (ids: string[]) => {
    try {
      for (const batch of trashBatches(ids)) {
        await trashPhotos(batch);
      }
      setLightboxIndex(-1);
      clearSelection();
      await loadTimeline(true, undefined, activeAlbum?.id);
      await reloadStatus();
    } catch (err) {
      setError(describeError(err, 'Could not move the photo to the trash'));
    }
  };

  const assignAlbum = async (photo: Photo, albumId: string, member: boolean) => {
    try {
      await setAlbumPhotos(albumId, [photo.id], member);
      await loadTimeline(true, undefined, activeAlbum?.id);
      await loadAlbums();
    } catch (err) {
      setError(describeError(err, 'Could not update the album'));
    }
  };

  const syncLibrary = async () => {
    setImportState('running');
    setError('');
    try {
      await resyncPhotosLibrary();
      for (let attempt = 0; attempt < 12; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2500));
        const status = await reloadStatus();
        const scan = status?.scan;
        if (scan && scan.state !== 'running') {
          setImportState(scan.state);
          if (scan.state === 'error' || scan.state === 'paused') {
            setError(scan.error || 'The import could not finish');
          }
          break;
        }
        await loadTimeline(true, undefined, activeAlbum?.id);
      }
      await loadTimeline(true, undefined, activeAlbum?.id);
    } catch (err) {
      setImportState('error');
      setError(describeError(err, 'Could not start the import'));
    }
  };

  if (!signedIn) {
    return (
      <main className="photos-page photos-page--centered">
        <section className="photos-signin" aria-labelledby="photos-signin-title">
          <div className="photos-signin__visual" aria-hidden="true">
            <span className="photos-signin__photo photos-signin__photo--a" />
            <span className="photos-signin__photo photos-signin__photo--b" />
            <span className="photos-signin__photo photos-signin__photo--c">
              <ImageIcon />
            </span>
          </div>
          <h1 id="photos-signin-title">TeleDirect Photos</h1>
          <p className="photos-signin__lede">
            Your private photo vault — backed by your own Telegram channel and
            streamed straight from Telegram's servers. Nothing is stored on ours.
          </p>
          <ul className="photos-signin__perks">
            <li>
              <CheckIcon />
              <span>Unlimited-duration backup on your own channel</span>
            </li>
            <li>
              <CheckIcon />
              <span>Timeline search, albums, favorites and a lightbox</span>
            </li>
            <li>
              <CheckIcon />
              <span>Private by design — only your account can browse it</span>
            </li>
          </ul>
          {onSignIn ? (
            <Button type="button" onClick={onSignIn}>Sign in to use Photos</Button>
          ) : (
            <p className="photos-signin__hint">Use the Sign in button in the header, then come back.</p>
          )}
        </section>
      </main>
    );
  }
  if (statusError) {
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
    // Chrome + shimmer skeleton instead of a bare "Checking…" line: the page
    // structure is known before the status resolves, and the text-only
    // variant read as a black void on phones (the reported broken state).
    return (
      <main className="photos-page photos-page--loading">
        <div className="photos-shell">
          <nav className="photos-nav" aria-label="Photos sections">
            {PHOTOS_NAV.map(({ key, label, icon: NavIcon }) => (
              <button key={key} type="button" className={key === 'timeline' ? 'active' : ''} tabIndex={-1}>
                <NavIcon />
                <span>{label}</span>
              </button>
            ))}
          </nav>
          <section className="photos-main">
            <header className="photos-header" aria-hidden="true" />
            <TimelineSkeleton width={0} />
          </section>
        </div>
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
  // Only the grid views get the measured fill height; the albums list and the
  // trash rows scroll with the document (the shell already reserves the nav).
  const fillsViewport = view === 'timeline' || view === 'favorites' || Boolean(isAlbumDetail);
  const pageStyle = fillsViewport && gridHeight ? { ['--photos-fill-height' as string]: `${gridHeight}px` } : undefined;
  // The fill layout is overflow:hidden and sizes from --photos-fill-height:
  // applying it before the first measurement collapses the page to its
  // padding and clips the whole view. Until the height lands, fall back to
  // the normal document flow (one paint of a scrollable page, then the
  // measured layout takes over).
  const fillActive = fillsViewport && gridHeight > 0;
  const searching = searchQ.length > 0;
  const hasFilters = Boolean(filters.kind || filters.camera || filters.place || filters.takenAfter || filters.takenBefore || filters.minSize);
  const timelineData: TimelineData | null = timeline && {
    items: visiblePhotos,
    // Server search pages normally — the cursor is preserved so "Load more"
    // keeps fetching within the same search/filter context.
    nextCursor: timeline.nextCursor,
  };

  return (
    <main
      ref={fillRef}
      className={`photos-page${fillActive ? ' photos-page--fill' : ''}${selectionActive ? ' photos-page--selecting' : ''}`}
      style={pageStyle}
      onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
    >
      <div className="photos-shell">
        <nav className="photos-nav" aria-label="Photos sections">
          {PHOTOS_NAV.map(({ key, label, icon: NavIcon }) => (
            <button
              key={key}
              type="button"
              className={view === key ? 'active' : ''}
              aria-current={view === key ? 'page' : undefined}
              onClick={() => {
                setView(key);
                setActiveAlbum(null);
                setCreatingAlbum(false);
                clearSelection();
                setQuery('');
                setSearchQ('');
                setFilters({});
              }}
            >
              <NavIcon />
              <span>{label}</span>
            </button>
          ))}
        </nav>

        <section className="photos-main">
          <header className="photos-header">
            <div className={`photos-header__lead${isAlbumDetail ? ' photos-header__lead--detail' : ''}`}>
              {isAlbumDetail ? (
                <>
                  <button
                    type="button"
                    className="photos-iconbtn"
                    aria-label="Back to albums"
                    onClick={() => {
                      setActiveAlbum(null);
                      clearSelection();
                      void loadTimeline(true, 'albums');
                    }}
                  >
                    <ArrowLeftIcon />
                  </button>
                  <div>
                    <h1 className="photos-title">{activeAlbum!.name}</h1>
                    {typeof activeAlbum!.photoCount === 'number' && (
                      <p className="photos-subtitle">
                        {activeAlbum!.photoCount.toLocaleString()}{' '}
                        {activeAlbum!.photoCount === 1 ? 'item' : 'items'}
                      </p>
                    )}
                  </div>
                </>
              ) : (
                <div>
                  <h1 className="photos-title">
                    Photos
                    {status?.beta && (
                      <span className="photos-beta-badge" title="TeleDirect Photos is in beta — features may change">
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
                  {importState === 'running' && (
                    <p className="photos-import-line" role="status">
                      Checking your Telegram channel — new photos appear as they finish.
                    </p>
                  )}
                </div>
              )}
            </div>
            <div className="photos-header__actions">
              {/* Thumbnail density — GPhotos keeps this in the header too.
                  Hidden on touch layouts, where pinch covers it. */}
              <div className="photos-zoom" role="group" aria-label="Thumbnail size" title="Thumbnail size">
                <ImageIcon className="photos-zoom__min" />
                <input
                  type="range"
                  min={0}
                  max={ROW_HEIGHT_STEPS.length - 1}
                  step={1}
                  value={ROW_HEIGHT_STEPS.indexOf(rowHeight as never)}
                  onChange={(event) => setRowHeight(ROW_HEIGHT_STEPS[Number(event.target.value)])}
                  aria-label="Thumbnail size"
                />
                <ImageIcon className="photos-zoom__max" />
              </div>
              <div className="photos-search">
                <SearchIcon aria-hidden="true" />
                <input
                  type="search"
                  placeholder="Search file names"
                  aria-label="Search photos by file name"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Escape') setQuery('');
                  }}
                />
              </div>
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
              {/* Maintenance actions live in an overflow menu — a library
                  scan is not a primary CTA on the gallery home. */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button type="button" className="photos-iconbtn" aria-label="More actions" title="More actions">
                    <MoreVerticalIcon />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent className="account-menu" align="end">
                  {/* asChild + real <button>: .account-menu styles only match
                      a/button children; a bare DropdownMenuItem renders a
                      div[role=menuitem] and gets no row styling. */}
                  <DropdownMenuItem
                    asChild
                    disabled={importState === 'running'}
                    onSelect={() => void syncLibrary()}
                  >
                    <button type="button">
                      <SearchIcon />
                      <span>{importState === 'running' ? 'Looking for missing photos…' : 'Find missing photos'}</span>
                    </button>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </header>

          {view !== 'albums' && !activeAlbum && (
            <PhotosFilterBar
              facets={facets}
              filters={filters}
              onChange={setFilters}
            />
          )}

          {view === 'albums' && !activeAlbum ? (
            <AlbumsGrid
              albums={albums}
              creating={creatingAlbum}
              onStartCreate={() => setCreatingAlbum(true)}
              onCancelCreate={() => setCreatingAlbum(false)}
              onSubmitCreate={() => void submitCreateAlbum()}
              nameInputRef={albumInputRef}
              onOpen={openAlbum}
              onRename={(album) => {
                const name = window.prompt('New album name', album.name);
                if (name && name.trim()) {
                  void renamePhotoAlbum(album.id, name.trim()).then(loadAlbums);
                }
              }}
              onDelete={(album) => {
                if (window.confirm(`Delete album "${album.name}"? Photos are kept.`)) {
                  void deletePhotoAlbum(album.id).then(() => { void loadAlbums(); void loadTimeline(true); });
                }
              }}
            />
          ) : searching && !visiblePhotos.length && !timelineLoading ? (
            <div className="photos-empty">
              <span className="photos-empty__icon" aria-hidden="true"><SearchIcon /></span>
              <h2>No matches</h2>
              <p>Nothing in your library matches “{searchQ}”. Try fewer words, or clear the active filters.</p>
            </div>
          ) : hasFilters && !visiblePhotos.length && !timelineLoading ? (
            <div className="photos-empty">
              <span className="photos-empty__icon" aria-hidden="true"><FilterIcon /></span>
              <h2>No photos match these filters</h2>
              <p>Loosen or clear a chip below to widen the view.</p>
            </div>
          ) : loadError ? null : (
            <PhotosTimeline
              data={timelineData}
              loading={timelineLoading}
              onLoadMore={() => void loadTimeline(false, undefined, activeAlbum?.id)}
              onOpen={(photo) => {
                // Selection-mode clicks reach onTileClick via PhotoTile; plain
                // clicks open the lightbox.
                setLightboxIndex(visiblePhotos.findIndex((p) => p.id === photo.id));
              }}
              onScan={() => void syncLibrary()}
              onUpload={() => fileInputRef.current?.click()}
              selection={selection}
              onTileClick={onTileClick}
              onToggleDay={toggleDay}
              onPinchStep={onPinchStep}
              rowHeight={rowHeight}
              emptyView={view}
              activeAlbumName={activeAlbum?.name}
            />
          )}
        </section>
      </div>

      {selectionActive && (
        <div className="photos-selection-bar" role="toolbar" aria-label="Selected photos">
          <button
            type="button"
            className="photos-iconbtn"
            aria-label="Clear selection"
            onClick={exitSelection}
          >
            <XIcon />
          </button>
          <span className="photos-selection-bar__count">
            {selection.ids.size} selected
          </span>
          <span className="photos-selection-bar__spacer" />
          <Button size="sm" variant="ghost" onClick={selectAll}>Select all</Button>
          {view !== 'trash' && albums.length > 0 && (
            <select
              className="photos-selection-bar__albums"
              value=""
              aria-label="Add selected to album"
              onChange={(event) => {
                if (event.target.value) void addSelectionToAlbum(event.target.value);
              }}
            >
              <option value="">Add to album…</option>
              {albums.map((album) => (
                <option key={album.id} value={album.id}>{album.name}</option>
              ))}
            </select>
          )}
          {view !== 'trash' && (
            <button
              type="button"
              className="photos-iconbtn"
              aria-label="Download selected"
              title="Download originals"
              onClick={() => downloadSelection([...selection.ids])}
            >
              <DownloadIcon />
            </button>
          )}
          {view === 'trash' ? (
            <Button size="sm" variant="secondary" onClick={() => void restoreSelection([...selection.ids])}>
              <RestoreIcon /> Restore
            </Button>
          ) : (
            <Button size="sm" variant="secondary" onClick={() => void trash([...selection.ids])}>
              <TrashIcon /> Delete
            </Button>
          )}
        </div>
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
        <PhotoLightbox
          photos={visiblePhotos}
          index={lightboxIndex}
          albums={albums}
          trashView={view === 'trash'}
          onClose={() => setLightboxIndex(-1)}
          onNavigate={setLightboxIndex}
          onToggleFavorite={(p) => void toggleFavorite(p)}
          onTrash={(p) => void trash([p.id])}
          onRestore={(p) => {
            setLightboxIndex(-1);
            void restoreSelection([p.id]);
          }}
          onAssignAlbum={(p, albumId, member) => void assignAlbum(p, albumId, member)}
        />
      )}
    </main>
  );
}

// ── Lightbox (YARL-based) ────────────────────────────────────────────────

/**
 * YARL slides: the stage shows the preview webp (browsers can't render HEIC and
 * browsing must not pull originals), while the download plug-in and video get
 * the owner-scoped original — it defaults to `src`, which saved a webp.
 */
export function buildLightboxSlides(photos: Photo[]): Slide[] {
  return photos.map((p) => {
    const download = photoFileUrl(p.id);
    return p.kind === 'video'
      ? {
          type: 'video' as const,
          sources: [{ src: download, type: p.mime || 'video/mp4' }],
          poster: photoThumbUrl(p.id, 'preview'),
          download,
        }
      : {
          type: 'image' as const,
          src: photoThumbUrl(p.id, 'preview'),
          alt: p.fileName,
          width: p.width || undefined,
          height: p.height || undefined,
          download,
        };
  });
}

function PhotoLightbox({
  photos,
  index,
  albums,
  trashView,
  onClose,
  onNavigate,
  onToggleFavorite,
  onTrash,
  onRestore,
  onAssignAlbum,
}: {
  photos: Photo[];
  index: number;
  albums: PhotoAlbum[];
  /** Trash swaps the destructive action for Restore, like GPhotos' trash viewer. */
  trashView: boolean;
  onClose: () => void;
  onNavigate: (index: number) => void;
  onToggleFavorite: (photo: Photo) => void;
  onTrash: (photo: Photo) => void;
  onRestore: (photo: Photo) => void;
  onAssignAlbum: (photo: Photo, albumId: string, member: boolean) => void;
}) {
  const photo = photos[index];
  const [metaOpen, setMetaOpen] = useState(false);
  // The filmstrip costs ~110px of stage on a phone and swiping is the native
  // navigation there — GPhotos drops it on touch too. Match the CSS touch
  // breakpoint; plugins must be mounted/unmounted, hiding .yarl__thumbnails
  // in CSS collapses the lightbox (that class wraps the carousel).
  const [narrow, setNarrow] = useState(() => isNarrowViewport());
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const queries = ['(max-width: 680px)', '(pointer: coarse)'].map((q) => window.matchMedia(q));
    const onChange = () => setNarrow(queries.some((m) => m.matches));
    queries.forEach((m) => m.addEventListener('change', onChange));
    return () => queries.forEach((m) => m.removeEventListener('change', onChange));
  }, []);

  const slides = useMemo(() => buildLightboxSlides(photos), [photos]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'i') setMetaOpen((v) => !v);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return createPortal(
    <div className="photos-lightbox-root">
      <LightboxRoot
        open
        close={onClose}
        index={index}
        slides={slides}
        /* Lean chrome on purpose: Counter/Captions/Fullscreen/Slideshow each
           pin their own UI into the same corners our topbar owns — they
           rendered as overlapping text/icons. Zoom (wheel/double-tap),
           filmstrip and video are the GPhotos-equivalent set. */
        plugins={narrow ? [Zoom, Video] : [Zoom, Thumbnails, Video]}
        animation={{ fade: 220, swipe: 280 }}
        render={{
          iconPrev: () => <ChevronLeftIcon />,
          iconNext: () => <ChevronRightIcon />,
          // Toolbar chrome renders through the `controls` slot (absolute
          // positioned over the lightbox stage). The info drawer MUST live in
          // here too: YARL marks every other document.body child inert +
          // aria-hidden while open, so anything in our own portal (the old
          // drawer location) renders visually but is dead to clicks and
          // screen readers.
          controls: () => (
            <>
              <div className="photos-lb-topbar">
                <button
                  type="button"
                  className="photos-lb-btn"
                  onClick={onClose}
                  aria-label="Back to library"
                  title="Back"
                >
                  <ArrowLeftIcon />
                </button>
                {photo && (
                  <div className="photos-lb-title">
                    <span className="photos-lb-title__name">{photo.fileName}</span>
                    <span className="photos-lb-title__date">{dayLabel(photo.takenAt)}</span>
                  </div>
                )}
                {photo && !trashView && !narrow && (
                  <>
                    <button
                      type="button"
                      className={`photos-lb-btn${photo.favorite ? ' is-active' : ''}`}
                      onClick={() => onToggleFavorite(photo)}
                      aria-label={photo.favorite ? 'Remove from favorites' : 'Add to favorites'}
                      aria-pressed={photo.favorite}
                      title="Favorite"
                    >
                      <StarIcon filled={photo.favorite} />
                    </button>
                    <a
                      className="photos-lb-btn"
                      href={photoFileUrl(photo.id)}
                      download
                      aria-label="Download original"
                      title="Download original"
                    >
                      <DownloadIcon />
                    </a>
                  </>
                )}
                {photo && (
                  <button
                    type="button"
                    className="photos-lb-btn"
                    onClick={() => setMetaOpen((v) => !v)}
                    aria-label="Info"
                    aria-pressed={metaOpen}
                    title="Info (i)"
                  >
                    <InfoIcon />
                  </button>
                )}
                {/* Phones: actions live in an overflow menu — 5 inline buttons
                    plus YARL's zoom/close cluster left the filename ~20px at
                    390px. Desktop keeps them inline. */}
                {photo && !trashView && narrow && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button type="button" className="photos-lb-btn" aria-label="More actions" title="More">
                        <MoreVerticalIcon />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="photos-lb-menu">
                      <DropdownMenuItem asChild onSelect={() => onToggleFavorite(photo)}>
                        <button type="button">
                          <StarIcon filled={photo.favorite} />
                          <span>{photo.favorite ? 'Remove from favorites' : 'Add to favorites'}</span>
                        </button>
                      </DropdownMenuItem>
                      <DropdownMenuItem asChild>
                        <a href={photoFileUrl(photo.id)} download>
                          <DownloadIcon />
                          <span>Download original</span>
                        </a>
                      </DropdownMenuItem>
                      <DropdownMenuItem asChild onSelect={() => onTrash(photo)}>
                        <button type="button" className="photos-lb-menu__danger">
                          <TrashIcon />
                          <span>Move to trash</span>
                        </button>
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
                {photo && (trashView ? (
                  <button
                    type="button"
                    className="photos-lb-btn"
                    onClick={() => onRestore(photo)}
                    aria-label="Restore"
                    title="Restore"
                  >
                    <RestoreIcon />
                  </button>
                ) : !narrow && (
                  <button
                    type="button"
                    className="photos-lb-btn photos-lb-btn--danger"
                    onClick={() => onTrash(photo)}
                    aria-label="Move to trash"
                    title="Move to trash"
                  >
                    <TrashIcon />
                  </button>
                ))}
              </div>
              {metaOpen && photo && (
                <aside className="photos-info" aria-label="Photo details">
                  <div className="photos-info__head">
                    <h2>Info</h2>
                    <button
                      type="button"
                      className="photos-iconbtn"
                      aria-label="Close info"
                      onClick={() => setMetaOpen(false)}
                    >
                      <XIcon />
                    </button>
                  </div>
                  <MetaPanel photo={photo} />
                  <section className="photos-info__albums" aria-label="Albums">
                    <h3>Albums</h3>
                    <div className="photos-info__chips">
                      {albums
                        .filter((album) => photo.albumIds.includes(album.id))
                        .map((album) => (
                          <button
                            key={album.id}
                            type="button"
                            className="photos-album-chip"
                            title={`Remove from ${album.name}`}
                            onClick={() => onAssignAlbum(photo, album.id, false)}
                          >
                            {album.name}
                            <XIcon />
                          </button>
                        ))}
                      {albums.filter((album) => photo.albumIds.includes(album.id)).length === 0 && (
                        <p className="photos-info__hint">Not in any album yet.</p>
                      )}
                    </div>
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
                  </section>
                </aside>
              )}
            </>
          ),
        }}
        on={{
          // Back/forward: parent state is the single source of truth.
          view: ({ index: next }) => onNavigate(next),
          click: () => undefined,
        }}
        carousel={{ finite: false }}
        controller={{ closeOnBackdropClick: true }}
        styles={{
          container: { backgroundColor: 'rgba(8, 9, 10, 0.96)' },
        }}
        toolbar={{ buttons: ['close'] }}
      />
    </div>,
    document.body,
  );
}

// Re-exports for callers that want the connect page directly.
export { PhotosConnectPage as default };
