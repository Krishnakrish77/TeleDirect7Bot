import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  connectPhotosChannel,
  createPhotoAlbum,
  deletePhotoAlbum,
  disconnectPhotosChannel,
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
import type { Photo, PhotoAlbum, PhotosChannelStatus, TimelineResponse } from '../types';

type TimelineData = { items: Photo[]; nextCursor: string | null };

function dayLabel(iso: string | null): string {
  if (!iso) return 'Unknown date';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return 'Unknown date';
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
}

function usePhotoStatus() {
  const [status, setStatus] = useState<PhotosChannelStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await fetchPhotosStatus());
    } catch {
      setStatus({ connected: false });
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  return { status, loading, reload };
}

// ── Connect wizard ────────────────────────────────────────────────────────

export function PhotosConnectPage({ onConnected }: { onConnected: () => void }) {
  const [channel, setChannel] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

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

  return (
    <div className="photos-connect">
      <h1>TeleDirect Photos</h1>
      <p className="photos-connect__lede">
        Private photo backup backed by your own Telegram channel. Create a private channel,
        add this bot as an administrator, then paste the channel link or @username below.
      </p>
      <ol className="photos-connect__steps">
        <li>Create a <strong>private channel</strong> in Telegram (any name).</li>
        <li>Add the bot as an <strong>administrator</strong> with post rights.</li>
        <li>Paste the channel&apos;s @username or <code>-100…</code> id here.</li>
      </ol>
      <form onSubmit={submit} className="photos-connect__form">
        <input
          value={channel}
          onChange={(event) => setChannel(event.target.value)}
          placeholder="@myphotovault or -1001234567890"
          aria-label="Channel username or id"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !channel.trim()}>
          {busy ? 'Verifying…' : 'Connect channel'}
        </button>
      </form>
      {error && <p className="photos-connect__error" role="alert">{error}</p>}
    </div>
  );
}

// ── Lightbox ──────────────────────────────────────────────────────────────

function Lightbox({
  photo,
  onClose,
  onPrev,
  onNext,
  onToggleFavorite,
  onTrash,
}: {
  photo: Photo;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  onToggleFavorite: (photo: Photo) => void;
  onTrash: (photo: Photo) => void;
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
          <video controls autoPlay src={photoFileUrl(photo.messageId)} />
        ) : (
          <img src={photoFileUrl(photo.messageId)} alt={photo.fileName} />
        )}
        <div className="photos-lightbox__meta">
          <span>{dayLabel(photo.takenAt)}</span>
          <span>{photo.fileName}</span>
          {photo.camera && <span>{photo.camera}</span>}
        </div>
      </div>
      <button className="photos-lightbox__nav photos-lightbox__nav--next" onClick={onNext} aria-label="Next">›</button>
      <div className="photos-lightbox__actions">
        <button onClick={() => onToggleFavorite(photo)}>{photo.favorite ? '★ Favorited' : '☆ Favorite'}</button>
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
        src={photoThumbUrl(photo.messageId, 'grid')}
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
}: {
  data: TimelineData | null;
  loading: boolean;
  onLoadMore: () => void;
  onOpen: (photo: Photo) => void;
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
        <p>Drop photos below, or post them to your connected Telegram channel from any device.</p>
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
  const { status, loading: statusLoading, reload: reloadStatus } = usePhotoStatus();
  const [view, setView] = useState<View>('timeline');
  const [timeline, setTimeline] = useState<TimelineData | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [albums, setAlbums] = useState<PhotoAlbum[]>([]);
  const [activeAlbum, setActiveAlbum] = useState<PhotoAlbum | null>(null);
  const [lightbox, setLightbox] = useState<number>(-1);
  const [uploads, setUploads] = useState<Array<{ name: string; percent: number }>>([]);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const albumInputRef = useRef<HTMLInputElement>(null);

  const signedIn = Boolean(user);

  const loadTimeline = useCallback(async (replace: boolean, view_?: View, albumId?: string) => {
    setTimelineLoading(true);
    try {
      const effectiveView = view_ ?? view;
      const qs = effectiveView !== 'timeline' ? effectiveView : undefined;
      const data = await fetchPhotosTimeline({
        view: qs === 'albums' ? 'timeline' : qs,
        album: albumId || undefined,
      });
      setTimeline((current) => (replace ? data : { items: [...(current?.items ?? []), ...data.items], nextCursor: data.nextCursor }));
    } catch {
      if (replace) setTimeline({ items: [], nextCursor: null });
    } finally {
      setTimelineLoading(false);
    }
  }, [view]);

  const loadAlbums = useCallback(async () => {
    try {
      setAlbums((await fetchPhotoAlbums()).albums ?? []);
    } catch { /* stays empty */ }
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
    setUploads(list.map((f) => ({ name: f.name, percent: 0 })));
    try {
      await uploadPhotos(list, {
        albumId: activeAlbum?.id || undefined,
        onProgress: (percent) => setUploads((current) => current.map((u) => ({ ...u, percent }))),
      });
      await loadTimeline(true);
      await reloadStatus();
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
    await setPhotoFavorite(photo.id, !photo.favorite);
    setTimeline((current) => current && ({
      ...current,
      items: current.items.map((p) => p.id === photo.id ? { ...p, favorite: !p.favorite } : p),
    }));
  };

  const trash = async (photo: Photo) => {
    await trashPhotos([photo.id]);
    setLightbox(-1);
    await loadTimeline(true);
    await reloadStatus();
  };

  const restore = async (photo: Photo) => {
    await restorePhotos([photo.id]);
    await loadTimeline(true);
  };

  if (!signedIn) {
    return <div className="photos-page"><p className="photos-connect__lede">Sign in to use TeleDirect Photos.</p></div>;
  }
  if (statusLoading) {
    return <div className="photos-page"><div className="photos-loading">Checking your library…</div></div>;
  }
  if (!status?.connected) {
    return (
      <div className="photos-page">
        <PhotosConnectPage onConnected={() => { void reloadStatus(); }} />
      </div>
    );
  }
  if (status.status && status.status !== 'active') {
    return (
      <div className="photos-page">
        <div className="photos-banner photos-banner--warn" role="alert">
          Your channel is {status.status}. Reconnect it to keep streaming originals.
          <button onClick={async () => { await disconnectPhotosChannel(); await reloadStatus(); }}>Reconnect</button>
        </div>
      </div>
    );
  }

  const isAlbumDetail = view === 'albums' && activeAlbum;
  const currentPhotos = photos;

  return (
    <div
      className="photos-page"
      onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
    >
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
              <img src={photoThumbUrl(photo.messageId, 'grid')} alt={photo.fileName} loading="lazy" />
              <span>{dayLabel(photo.takenAt)}</span>
              <button onClick={() => void restore(photo)}>Restore</button>
            </div>
          ))}
        </div>
      ) : (
        <PhotosTimeline
          data={timeline}
          loading={timelineLoading}
          onLoadMore={() => void loadTimeline(false, undefined, activeAlbum?.id)}
          onOpen={(photo) => setLightbox(photos.findIndex((p) => p.id === photo.id))}
        />
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
          onClose={() => setLightbox(-1)}
          onPrev={() => step(-1)}
          onNext={() => step(1)}
          onToggleFavorite={(p) => void toggleFavorite(p)}
          onTrash={(p) => void trash(p)}
        />
      )}
    </div>
  );
}

// Re-exports for callers that want the connect page directly.
export { PhotosConnectPage as default };
