import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { Button } from './ui/button';

type TimelineData = { items: Photo[]; nextCursor: string | null };

function dayLabel(iso: string | null): string {
  if (!iso) return 'Unknown date';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return 'Unknown date';
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
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
  const manualTimeout = useRef(false);

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
  const detectChannel = async () => {
    setError('');
    setDetecting(true);
    manualTimeout.current = false;
    try {
      for (let attempt = 0; attempt < 20 && !manualTimeout.current; attempt += 1) {
        const pending = await fetchPendingPhotoChannel();
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
      if (!manualTimeout.current) {
        setError(
          `The bot has not reported a channel yet. Check that ${botLabel} is an administrator of it, `
          + 'then press Continue again. Added the bot earlier? Remove and re-add it, or link it manually below.',
        );
        setShowManual(true);
      }
    } catch (err) {
      setError(describeError(err, 'Could not check for the channel'));
      setShowManual(true);
    } finally {
      setDetecting(false);
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
            manualTimeout.current = true;
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

// ── Timeline grid ─────────────────────────────────────────────────────────

function PhotoTile({ photo, onOpen }: { photo: Photo; onOpen: (photo: Photo) => void }) {
  return (
    <button className="photos-tile" onClick={() => onOpen(photo)} aria-label={photo.fileName}>
      <img
        src={photoThumbUrl(photo.id, 'grid')}
        alt={photo.fileName}
        loading="lazy"
      />
      {photo.kind === 'video' && <span className="photos-tile__badge">▶</span>}
      {photo.favorite && <span className="photos-tile__fav">★</span>}
    </button>
  );
}

function PhotosTimeline({
  data,
  loading,
  onLoadMore,
  onOpen,
  onScan,
}: {
  data: TimelineData | null;
  loading: boolean;
  onLoadMore: () => void;
  onOpen: (photo: Photo) => void;
  onScan?: () => void;
}) {
  const groups = useMemo(() => {
    const out: Array<{ day: string; items: Photo[] }> = [];
    for (const photo of data?.items ?? []) {
      const day = dayLabel(photo.takenAt);
      const last = out[out.length - 1];
      if (last && last.day === day) last.items.push(photo);
      else out.push({ day, items: [photo] });
    }
    return out;
  }, [data]);

  if (loading && !data) return <div className="photos-loading">Loading your library…</div>;
  if (!data?.items.length) {
    return (
      <div className="photos-empty">
        <h2>Nothing here yet</h2>
        <p>Post photos to your connected Telegram channel, or drop them anywhere on this page.</p>
        {onScan && (
          <Button variant="secondary" onClick={onScan}>
            Scan channel history
          </Button>
        )}
        <p className="photos-empty__hint">
          Already posted to the channel? Scanning imports those posts — it runs in the background,
          so refresh in a moment.
        </p>
      </div>
    );
  }
  return (
    <div className="photos-timeline">
      {groups.map((group) => (
        <section key={group.day} className="photos-day">
          <h2 className="photos-day__label">{group.day}</h2>
          <div className="photos-day__grid">
            {group.items.map((photo) => (
              <PhotoTile key={photo.id} photo={photo} onOpen={onOpen} />
            ))}
          </div>
        </section>
      ))}
      {data.nextCursor && (
        <button className="photos-more" onClick={onLoadMore} disabled={loading}>
          {loading ? 'Loading…' : 'Load more'}
        </button>
      )}
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
  const [uploads, setUploads] = useState<Array<{ name: string; percent: number }>>([]);
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
    setUploads(list.map((f) => ({ name: f.name, percent: 0 })));
    try {
      // A 200 response can still carry per-file failures (duplicates,
      // over-limit, unsupported type) — a backup UI must not report
      // success for files the vault never received.
      const { results } = await uploadPhotos(list, {
        albumId: activeAlbum?.id || undefined,
        onProgress: (percent) => setUploads((current) => current.map((u) => ({ ...u, percent }))),
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
      setUploads([]);
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
      {status?.beta && <span className="photos-beta-badge" title="TeleDirect Photos is in beta — features may change">Beta</span>}
      <nav className="photos-nav" aria-label="Photos sections">
        {(['timeline', 'favorites', 'albums', 'trash'] as View[]).map((v) => (
          <button
            key={v}
            className={view === v ? 'active' : ''}
            onClick={() => { setView(v); setActiveAlbum(null); }}
          >
            {v[0].toUpperCase() + v.slice(1)}
          </button>
        ))}
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
        <button className="photos-upload-btn" onClick={() => fileInputRef.current?.click()}>Upload</button>
        <button
          className="photos-sync-btn"
          title="Re-scan the channel for posts the bot missed while offline"
          onClick={() => void syncLibrary()}
        >
          Sync
        </button>
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
        <div className="photos-uploads" role="status">
          {uploads.map((u) => (
            <div key={u.name} className="photos-uploads__row">
              <span>{u.name}</span>
              <progress max={100} value={u.percent} />
            </div>
          ))}
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
