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
import { PhotosIcon } from '../icons';
import LightboxRoot from 'yet-another-react-lightbox';
import Captions from 'yet-another-react-lightbox/plugins/captions';
import Counter from 'yet-another-react-lightbox/plugins/counter';
import Download from 'yet-another-react-lightbox/plugins/download';
import Fullscreen from 'yet-another-react-lightbox/plugins/fullscreen';
import Slideshow from 'yet-another-react-lightbox/plugins/slideshow';
import Thumbnails from 'yet-another-react-lightbox/plugins/thumbnails';
import Video from 'yet-another-react-lightbox/plugins/video';
import Zoom from 'yet-another-react-lightbox/plugins/zoom';
import 'yet-another-react-lightbox/styles.css';
import 'yet-another-react-lightbox/plugins/captions.css';
import 'yet-another-react-lightbox/plugins/counter.css';
import 'yet-another-react-lightbox/plugins/thumbnails.css';
import {
  connectPhotosChannel,
  createPhotoAlbum,
  deletePhotoAlbum,
  disconnectPhotosChannel,
  fetchPendingPhotoChannel,
  fetchPhotoAlbums,
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
import type { Photo, PhotoAlbum, PendingPhotoChannel, PhotosChannelStatus, TimelineResponse } from '../types';
import { Button } from './ui/button';

type TimelineData = { items: Photo[]; nextCursor: string | null };

const PHOTO_GAP = 3;

// Zoom density for the timeline grid (target row height in px). The slider
// morphs between these; a pinched-cell range like Google Photos' zoom.
const ROW_HEIGHT_STEPS = [110, 150, 190, 240, 320] as const;

function dayKey(iso: string | null): string {
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

function buildVirtualModel(groups: DayGroup[], width: number, targetHeight: number): VirtualEntry[] {
  const entries: VirtualEntry[] = [];
  const headerH = 44;
  for (const group of groups) {
    entries.push({ kind: 'header', group, height: headerH });
    const rows = buildJustifiedRows(group.photos, width, targetHeight, PHOTO_GAP);
    for (const row of rows) {
      entries.push({ kind: 'row', row, group, height: row.height });
    }
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
  return (
    <div
      className={`photos-item${selected ? ' is-selected' : ''}`}
      style={{ height: `${height}px`, width: `${width}px` }}
      onClick={(event) => (selectionActive || event.metaKey || event.ctrlKey
        ? onSelect(photo, event)
        : onOpen(photo))}
      role="button"
      tabIndex={0}
      aria-label={photo.fileName}
      aria-pressed={selected}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          if (selectionActive) onSelect(photo, event);
          else onOpen(photo);
        }
      }}
    >
      <img
        src={photoThumbUrl(photo.id, 'grid')}
        alt={photo.fileName}
        loading="lazy"
        decoding="async"
        className={loaded ? 'is-loaded' : undefined}
        onLoad={() => setLoaded(true)}
        draggable={false}
      />
      <span className="photos-item__shade" aria-hidden="true" />
      <span className="photos-item__meta" aria-hidden="true">
        <span>{dayLabel(photo.takenAt)}</span>
        {photo.kind === 'video' && <span>{formatDuration(photo.duration)}</span>}
      </span>
      {photo.kind === 'video' && (
        <span className="photos-item__badge" aria-label="Video">
          ▶{formatDuration(photo.duration) && ` ${formatDuration(photo.duration)}`}
        </span>
      )}
      {photo.favorite && <span className="photos-item__fav" aria-label="Favorite">★</span>}
      {selected && <span className="photos-item__check" aria-hidden="true">✓</span>}
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
}: {
  photos: Photo[];
  width: number;
  targetHeight: number;
  onOpen: (photo: Photo) => void;
  selection: SelectionState;
  onTileClick: (photo: Photo, index: number, event: TileClickEvent) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
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
    <div className="photos-timeline photos-timeline--scroll" ref={scrollRef}>
      {floatingDay && !scrubLabel && (
        <div className="photos-floating-day" aria-hidden="true">{floatingDay}</div>
      )}
      {scrubLabel !== null && (
        <div className="photos-scrubber-bubble" role="presentation">{scrubLabel}</div>
      )}
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
            return (
              <h2 key={`h-${entry.group.key}`} className="photos-day__label" style={style}>
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
      <DateScrubber
        onScrub={onScrubberDrag}
        onEnd={() => setScrubLabel(null)}
        disabled={photos.length < 20}
      />
    </div>
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

  const lastRatio = useRef(0.5);
  if (disabled) return null;
  return (
    <div
      ref={trackRef}
      className="photos-scrubber"
      onPointerDown={(event) => {
        event.preventDefault();
        active.current = true;
        (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
        const ratio = ratioFromEvent(event.clientY);
        lastRatio.current = ratio;
        onScrub(ratio);
      }}
      role="slider"
      aria-label="Scroll by date"
      aria-orientation="vertical"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(lastRatio.current * 100)}
      tabIndex={0}
      onKeyDown={(event) => {
        // 10% steps: a keyboard scrub spans a decade in ten presses —
        // coarse enough to be useful, fine enough to steer.
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          const delta = event.key === 'ArrowDown' ? 0.1 : -0.1;
          const next = Math.min(1, Math.max(0, lastRatio.current + delta));
          lastRatio.current = next;
          onScrub(next);
        }
      }}
    >
      <span className="photos-scrubber__grip" aria-hidden="true" />
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
  rowHeight,
  onRowHeightChange,
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
  rowHeight: number;
  onRowHeightChange: (value: number) => void;
  /** Which lens is empty — drives the empty-state copy. */
  emptyView?: 'timeline' | 'favorites' | 'albums' | 'trash';
  activeAlbumName?: string;
}) {
  const [timelineRef, width] = useMeasuredWidth<HTMLDivElement>();

  if (loading && !data) return <div className="photos-loading">Loading your library…</div>;
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
      <div className="photos-zoom" role="group" aria-label="Thumbnail size">
        <span className="photos-zoom__min" aria-hidden="true">▪</span>
        <input
          type="range"
          min={0}
          max={ROW_HEIGHT_STEPS.length - 1}
          step={1}
          value={ROW_HEIGHT_STEPS.indexOf(rowHeight as never)}
          onChange={(event) => onRowHeightChange(ROW_HEIGHT_STEPS[Number(event.target.value)])}
          aria-label="Thumbnail size"
        />
        <span className="photos-zoom__max" aria-hidden="true">◆</span>
      </div>
      <TimelineFlow
        photos={data.items}
        width={width}
        targetHeight={rowHeight}
        onOpen={onOpen}
        selection={selection}
        onTileClick={onTileClick}
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
    ['Location', exif?.latitude != null ? `${exif.latitude.toFixed(4)}, ${exif.longitude?.toFixed(4)}` : (photo.gps ? `${photo.gps.lat.toFixed(4)}, ${photo.gps.lon.toFixed(4)}` : '')],
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
      {photo.gps && (
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

export function PhotosPage({ user }: { user: { sub: number | string } | null }) {
  const { status, loading: statusLoading, error: statusError, reload: reloadStatus } = usePhotoStatus();
  const [view, setView] = useState<View>('timeline');
  const [timeline, setTimeline] = useState<TimelineData | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [albums, setAlbums] = useState<PhotoAlbum[]>([]);
  const [activeAlbum, setActiveAlbum] = useState<PhotoAlbum | null>(null);
  const [lightboxIndex, setLightboxIndex] = useState(-1);
  const [selection, setSelection] = useState<SelectionState>(emptySelection);
  const [rangeAnchor, setRangeAnchor] = useState<number | null>(null);
  const [rowHeight, setRowHeight] = useState<number>(190);
  const [uploads, setUploads] = useState<Array<{ name: string; url: string }>>([]);
  const [uploadPercent, setUploadPercent] = useState(0);
  const [importState, setImportState] = useState<'idle' | 'running' | 'done' | 'paused' | 'error'>('idle');
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const albumInputRef = useRef<HTMLInputElement>(null);

  const signedIn = Boolean(user);
  const photos = timeline?.items ?? [];
  const openPhoto = lightboxIndex >= 0 ? photos[lightboxIndex] : null;
  const selectionActive = selection.ids.size > 0;

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
      });
      setTimeline((current) => (replace ? data : { items: [...(current?.items ?? []), ...data.items], nextCursor: data.nextCursor }));
      setLoadError('');
    } catch (err) {
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

  const step = (delta: number) => {
    setLightboxIndex((current) => {
      if (!photos.length) return -1;
      return (current + delta + photos.length) % photos.length;
    });
  };

  // Selection click handling: plain click in selection mode toggles; with
  // meta/ctrl toggles individually; shift extends a range from the anchor.
  const onTileClick = useCallback((photo: Photo, index: number, event: TileClickEvent) => {
    setSelection((current) => {
      if (event.shiftKey && rangeAnchor != null) {
        const [from, to] = rangeIndices(rangeAnchor, index);
        const ids = new Set(current.ids);
        const order = [...current.order];
        for (let i = from; i <= to; i += 1) {
          const p = photos[i];
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
  }, [photos, rangeAnchor]);

  const clearSelection = useCallback(() => {
    setSelection(emptySelection());
    setRangeAnchor(null);
  }, []);

  const selectAll = useCallback(() => {
    const ids = new Set(photos.map((p) => p.id));
    setSelection({ ids, order: photos.map((p) => p.id) });
  }, [photos]);

  const exitSelection = useCallback(() => {
    clearSelection();
  }, []);

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
      await trashPhotos(ids);
      setLightboxIndex(-1);
      clearSelection();
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
      await loadTimeline(true, undefined, activeAlbum?.id);
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
        <p className="photos-connect__lede">Sign in to use TeleDirect Photos.</p>
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
            title="Check your Telegram channel for photos that are not in this library yet"
            disabled={importState === 'running'}
            onClick={() => void syncLibrary()}
          >
            {importState === 'running' ? 'Looking…' : 'Find missing photos'}
          </Button>
        </div>
      </header>
      <nav className="photos-tabs" aria-label="Photos sections">
        {(['timeline', 'favorites', 'albums', 'trash'] as View[]).map((v) => (
          <button
            key={v}
            className={view === v ? 'active' : ''}
            aria-current={view === v ? 'page' : undefined}
            onClick={() => { setView(v); setActiveAlbum(null); clearSelection(); }}
          >
            {v[0].toUpperCase() + v.slice(1)}
          </button>
        ))}
      </nav>

      {isAlbumDetail && <h1 className="photos-album-title">{activeAlbum!.name}</h1>}

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
          {photos.length === 0 && <p className="photos-empty">Trash is empty.</p>}
          {photos.map((photo) => (
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
          onOpen={(photo) => {
            // Selection-mode clicks reach onTileClick via PhotoTile; plain
            // clicks open the lightbox.
            setLightboxIndex(photos.findIndex((p) => p.id === photo.id));
          }}
          onScan={() => void syncLibrary()}
          onUpload={() => fileInputRef.current?.click()}
          selection={selection}
          onTileClick={onTileClick}
          rowHeight={rowHeight}
          onRowHeightChange={setRowHeight}
          emptyView={view}
          activeAlbumName={activeAlbum?.name}
        />
      )}

      {selectionActive && (
        <div className="photos-selection-bar" role="toolbar" aria-label="Selected photos">
          <span className="photos-selection-bar__count">
            {selection.ids.size} selected
          </span>
          <Button size="sm" variant="secondary" onClick={selectAll}>Select all</Button>
          <Button size="sm" variant="secondary" onClick={() => void trash([...selection.ids])}>
            Delete
          </Button>
          <Button size="sm" variant="ghost" onClick={exitSelection}>Cancel</Button>
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
          photos={photos}
          index={lightboxIndex}
          albums={albums}
          onClose={() => setLightboxIndex(-1)}
          onNavigate={setLightboxIndex}
          onToggleFavorite={(p) => void toggleFavorite(p)}
          onTrash={(p) => void trash([p.id])}
          onAssignAlbum={(p, albumId, member) => void assignAlbum(p, albumId, member)}
        />
      )}
    </main>
  );
}

// ── Lightbox (YARL-based) ────────────────────────────────────────────────

function PhotoLightbox({
  photos,
  index,
  albums,
  onClose,
  onNavigate,
  onToggleFavorite,
  onTrash,
  onAssignAlbum,
}: {
  photos: Photo[];
  index: number;
  albums: PhotoAlbum[];
  onClose: () => void;
  onNavigate: (index: number) => void;
  onToggleFavorite: (photo: Photo) => void;
  onTrash: (photo: Photo) => void;
  onAssignAlbum: (photo: Photo, albumId: string, member: boolean) => void;
}) {
  const photo = photos[index];
  const [metaOpen, setMetaOpen] = useState(false);

  const slides = useMemo(() => photos.map((p) => (
    p.kind === 'video'
      ? {
          type: 'video' as const,
          sources: [{ src: photoFileUrl(p.id), type: p.mime || 'video/mp4' }],
          poster: photoThumbUrl(p.id, 'preview'),
        }
      : {
          type: 'image' as const,
          src: photoThumbUrl(p.id, 'preview'),
          alt: p.fileName,
          width: p.width || undefined,
          height: p.height || undefined,
        }
  )), [photos]);

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
        plugins={[Zoom, Thumbnails, Captions, Counter, Download, Fullscreen, Slideshow, Video]}
        captions={{
          // Captions plugin reads description from the slide; our slides
          // carry alt only, so render the caption footer ourselves.
          descriptionTextAlign: 'center',
        }}
        render={{
          iconPrev: () => <span aria-hidden="true">‹</span>,
          iconNext: () => <span aria-hidden="true">›</span>,
          // Toolbar extras render through the `controls` slot (absolute
          // positioned by the lightbox chrome).
          controls: () => (
            <div className="photos-lb-toolbar">
              {photo && (
                <>
                  <button
                    className="photos-lb-btn"
                    onClick={() => onToggleFavorite(photo)}
                    title="Favorite"
                  >
                    {photo.favorite ? '★' : '☆'}
                  </button>
                  <button
                    className="photos-lb-btn"
                    onClick={() => setMetaOpen((v) => !v)}
                    title="Info (i)"
                  >
                    ⓘ
                  </button>
                  <button
                    className="photos-lb-btn photos-lb-btn--danger"
                    onClick={() => onTrash(photo)}
                    title="Move to trash"
                  >
                    🗑
                  </button>
                </>
              )}
            </div>
          ),
          slideFooter: photo ? () => (
            <div className="photos-lb-captions">
              <span>{dayLabel(photo.takenAt)}</span>
              <span>{photo.fileName}</span>
            </div>
          ) : undefined,
        }}
        on={{
          // Back/forward: parent state is the single source of truth.
          view: ({ index: next }) => onNavigate(next),
          click: () => undefined,
        }}
        carousel={{ finite: false }}
        controller={{ closeOnBackdropClick: true }}
        styles={{
          container: { backgroundColor: 'rgba(8, 9, 10, 0.94)' },
        }}
        toolbar={{ buttons: ['close'] }}
      />
      {metaOpen && photo && (
        <div className="photos-lb-meta-wrap">
          <MetaPanel photo={photo} />
        </div>
      )}
      {photo && (
        <div className="photos-lb-albums">
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
      )}
    </div>,
    document.body,
  );
}

// Re-exports for callers that want the connect page directly.
export { PhotosConnectPage as default };

// Keep resync import adjacent to its use (tree-shaking clarity).
import { resyncPhotosLibrary } from '../api';
