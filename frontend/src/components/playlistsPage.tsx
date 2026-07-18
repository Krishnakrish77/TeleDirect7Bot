import { FormEvent, useEffect, useMemo, useState } from 'react';
import { createPlaylist, deletePlaylist, removeTrackFromPlaylist, renamePlaylist, reorderPlaylistTracks } from '../api';
import { CheckIcon, ChevronDownIcon, ChevronRightIcon, ChevronUpIcon, ListIcon, ListPlusIcon, PauseIcon, PlayIcon, SearchIcon, ShuffleIcon, XIcon } from '../icons';
import type { PlayerState } from '../hooks/audio';
import type { PlaylistDetailResponse, PlaylistsResponse, User, WatchTrack } from '../types';
import { ErrorPanel, LoadingRows } from './common';
import { PlaylistCover } from './addToPlaylistSheet';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';

function trackSubtitle(track: WatchTrack) {
  return [track.artist, track.albumTitle, track.qualityLabel].filter(Boolean).join(' - ');
}

function trackCountLabel(count: number) {
  return `${count} ${count === 1 ? 'track' : 'tracks'}`;
}

export function PlaylistsPage({
  user,
  data,
  loading,
  error,
  navigate,
  onSignIn,
}: {
  user: User | null;
  data: PlaylistsResponse | null;
  loading: boolean;
  error: string;
  navigate: (href: string, replace?: boolean) => void;
  onSignIn: () => void;
}) {
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');

  if (!user) {
    return (
      <main className="page-main">
        <div className="empty-state">
          <ListPlusIcon />
          <strong>Sign in to create playlists</strong>
          <Button type="button" onClick={onSignIn}>Sign in</Button>
        </div>
      </main>
    );
  }

  const submitCreate = async (event: FormEvent) => {
    event.preventDefault();
    const playlistName = name.trim();
    if (!playlistName) return;
    setCreating(true);
    setCreateError('');
    try {
      const created = await createPlaylist(playlistName);
      navigate(`/app/playlist/${created.playlistId}`);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Unable to create playlist');
    } finally {
      setCreating(false);
    }
  };

  return (
    <main className="page-main playlists-main">
      <div className="playlist-library-hero">
        <div>
          <p className="eyebrow">Music</p>
          <h1>Playlists</h1>
          <p>Build queues for albums, moods, favorites, and long listening sessions.</p>
        </div>
        <form className="playlist-create-form library-create" onSubmit={submitCreate}>
          <Input
            value={name}
            onChange={(event) => setName(event.currentTarget.value)}
            placeholder="New playlist"
            maxLength={100}
          />
          <Button type="submit" disabled={creating || !name.trim()}>
            <ListPlusIcon />
            <span>{creating ? 'Creating' : 'Create'}</span>
          </Button>
        </form>
      </div>

      {createError && <ErrorPanel message={createError} />}
      {loading && <LoadingRows variant="grid" />}
      {error && <ErrorPanel message={error} />}
      {data && !data.available && <ErrorPanel message="Playlist storage is unavailable on this deployment." />}
      {!loading && !error && data?.playlists.length === 0 && (
        <div className="empty-state">
          <ListPlusIcon />
          <strong>No playlists yet</strong>
          <span>Create one above, or save songs from any album or Now Playing screen.</span>
        </div>
      )}
      {data?.playlists.length ? (
        <section className="playlist-grid" aria-label="Playlists">
          {data.playlists.map((playlist) => (
            <a key={playlist.playlistId} className="playlist-card" href={`/app/playlist/${playlist.playlistId}`}>
              <PlaylistCover covers={playlist.coverUrls} name={playlist.name} />
              <span className="playlist-card-copy">
                <strong>{playlist.name}</strong>
                <small>{trackCountLabel(playlist.trackCount)}</small>
              </span>
              <ChevronRightIcon />
            </a>
          ))}
        </section>
      ) : null}
    </main>
  );
}

export function PlaylistDetailPage({
  user,
  data,
  loading,
  error,
  setData,
  navigate,
  onSignIn,
  player,
  playTrack,
  togglePlayback,
  addToQueue,
  shuffleQueue,
  onAddToPlaylist,
}: {
  user: User | null;
  data: PlaylistDetailResponse | null;
  loading: boolean;
  error: string;
  setData: (updater: PlaylistDetailResponse | null | ((current: PlaylistDetailResponse | null) => PlaylistDetailResponse | null)) => void;
  navigate: (href: string, replace?: boolean) => void;
  onSignIn: () => void;
  player: PlayerState;
  playTrack: (track: WatchTrack, queue?: WatchTrack[]) => void;
  togglePlayback: (track?: WatchTrack, queue?: WatchTrack[]) => void;
  addToQueue: (track: WatchTrack, playNext?: boolean) => void;
  shuffleQueue: (queue: WatchTrack[]) => void;
  onAddToPlaylist: (track: WatchTrack) => void;
}) {
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('playlist');
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(data?.name || '');
  const [working, setWorking] = useState('');
  const [status, setStatus] = useState('');

  useEffect(() => {
    setName(data?.name || '');
    setQuery('');
    setSort('playlist');
    setStatus('');
  }, [data?.playlistId]);

  const visibleTracks = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? (data?.tracks || []).filter((track) => (
        `${track.title} ${track.artist} ${track.albumTitle}`.toLowerCase().includes(needle)
      ))
      : (data?.tracks || []);
    const sorted = filtered.slice();
    if (sort === 'title') sorted.sort((a, b) => a.title.localeCompare(b.title));
    if (sort === 'artist') sorted.sort((a, b) => (a.artist || '').localeCompare(b.artist || '') || a.title.localeCompare(b.title));
    if (sort === 'duration') sorted.sort((a, b) => (b.duration || 0) - (a.duration || 0));
    return sorted;
  }, [data?.tracks, query, sort]);

  if (!user) {
    return (
      <main className="page-main">
        <div className="empty-state">
          <ListPlusIcon />
          <strong>Sign in to view playlists</strong>
          <Button type="button" onClick={onSignIn}>Sign in</Button>
        </div>
      </main>
    );
  }
  if (loading) return <main className="page-main"><LoadingRows variant="playlist" /></main>;
  if (error || !data) return <main className="page-main"><ErrorPanel message={error || 'Unable to load playlist'} /></main>;

  const queue = data.tracks;
  const firstTrack = queue[0] || null;
  const canReorder = sort === 'playlist' && query.trim() === '';

  const submitRename = async (event: FormEvent) => {
    event.preventDefault();
    const nextName = name.trim();
    if (!nextName) return;
    setWorking('rename');
    setStatus('');
    try {
      const updated = await renamePlaylist(data.playlistId, nextName);
      setData(updated);
      setRenaming(false);
      setStatus('Playlist renamed');
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Unable to rename playlist');
    } finally {
      setWorking('');
    }
  };

  const removeTrack = async (track: WatchTrack) => {
    setWorking(`remove:${track.messageId}`);
    setStatus('');
    const previous = data;
    setData({ ...data, tracks: data.tracks.filter((item) => item.messageId !== track.messageId), trackCount: Math.max(0, data.trackCount - 1) });
    try {
      const updated = await removeTrackFromPlaylist(data.playlistId, track.messageId);
      setData(updated);
      setStatus(`Removed ${track.title}`);
    } catch (err) {
      setData(previous);
      setStatus(err instanceof Error ? err.message : 'Unable to remove track');
    } finally {
      setWorking('');
    }
  };

  const moveTrack = async (track: WatchTrack, delta: number) => {
    const from = data.tracks.findIndex((item) => item.messageId === track.messageId);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= data.tracks.length) return;
    const nextTracks = data.tracks.slice();
    nextTracks.splice(from, 1);
    nextTracks.splice(to, 0, track);
    const previous = data;
    setData({ ...data, tracks: nextTracks });
    setWorking(`move:${track.messageId}`);
    setStatus('');
    try {
      const updated = await reorderPlaylistTracks(data.playlistId, nextTracks.map((item) => item.messageId));
      setData(updated);
    } catch (err) {
      setData(previous);
      setStatus(err instanceof Error ? err.message : 'Unable to reorder playlist');
    } finally {
      setWorking('');
    }
  };

  const deleteCurrentPlaylist = async () => {
    if (!window.confirm(`Delete "${data.name}"?`)) return;
    setWorking('delete');
    setStatus('');
    try {
      await deletePlaylist(data.playlistId);
      navigate('/app/playlists', true);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Unable to delete playlist');
      setWorking('');
    }
  };

  return (
    <main className="page-main playlist-detail-main">
      <section className="playlist-detail-hero">
        <PlaylistCover covers={data.coverUrls} name={data.name} />
        <div className="playlist-detail-copy">
          <p className="eyebrow">Playlist</p>
          {renaming ? (
            <form className="playlist-rename-form" onSubmit={submitRename}>
              <Input value={name} onChange={(event) => setName(event.currentTarget.value)} maxLength={100} autoFocus />
              <Button type="submit" disabled={working === 'rename' || !name.trim()}>
                <CheckIcon />
                <span>Save</span>
              </Button>
              <Button type="button" variant="outline" onClick={() => { setRenaming(false); setName(data.name); }}>
                <XIcon />
                <span>Cancel</span>
              </Button>
            </form>
          ) : (
            <h1>{data.name}</h1>
          )}
          <p>{trackCountLabel(data.tracks.length)}</p>
          <div className="hero-actions">
            <Button type="button" disabled={!firstTrack} onClick={() => firstTrack && playTrack(firstTrack, queue)}>
              <PlayIcon />
              <span>Play</span>
            </Button>
            <Button type="button" variant="outline" disabled={queue.length < 2} onClick={() => shuffleQueue(queue)}>
              <ShuffleIcon />
              <span>Shuffle</span>
            </Button>
            {!renaming && (
              <Button type="button" variant="outline" onClick={() => setRenaming(true)}>
                <span>Rename</span>
              </Button>
            )}
            <Button type="button" variant="destructive" onClick={deleteCurrentPlaylist} disabled={working === 'delete'}>
              <XIcon />
              <span>Delete</span>
            </Button>
          </div>
          {status && <p className="playlist-status" role="status"><span>{status}</span></p>}
        </div>
      </section>

      <section className="playlist-tools" aria-label="Playlist tools">
        <label className="playlist-search">
          <SearchIcon />
          <Input
            value={query}
            onChange={(event) => setQuery(event.currentTarget.value)}
            placeholder="Search this playlist"
          />
        </label>
        <label className="playlist-sort">
          <span>Sort</span>
          <Select value={sort} onValueChange={setSort}>
            <SelectTrigger aria-label="Sort playlist"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="playlist">Playlist order</SelectItem>
              <SelectItem value="title">Title</SelectItem>
              <SelectItem value="artist">Artist</SelectItem>
              <SelectItem value="duration">Duration</SelectItem>
            </SelectContent>
          </Select>
        </label>
      </section>

      {data.tracks.length === 0 ? (
        <div className="empty-state">
          <ListPlusIcon />
          <strong>This playlist is empty</strong>
          <span>Add songs from album pages, audio pages, or Now Playing.</span>
        </div>
      ) : visibleTracks.length === 0 ? (
        <div className="empty-state">
          <SearchIcon />
          <strong>No songs match your search</strong>
          <span>Clear the search to see the full playlist.</span>
        </div>
      ) : (
        <div className="playlist-track-list">
          {visibleTracks.map((track, displayIndex) => {
            const sourceIndex = data.tracks.findIndex((item) => item.messageId === track.messageId);
            const active = player.track?.key === track.key;
            return (
              <a key={track.key} className={active ? 'playlist-track-row active' : 'playlist-track-row'} href={track.appHref}>
                <span className="track-number">{displayIndex + 1}</span>
                <img src={track.posterUrl || track.thumbUrl} alt="" loading="lazy" decoding="async" />
                <span className="track-title">
                  <strong>{track.title}</strong>
                  <span>{trackSubtitle(track)}</span>
                </span>
                <span className="track-duration">{track.durationLabel}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="playlist-track-action"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    togglePlayback(track, queue);
                  }}
                  aria-label={active && player.playing ? `Pause ${track.title}` : `Play ${track.title}`}
                >
                  {active && player.playing ? <PauseIcon /> : <PlayIcon />}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="playlist-track-action"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    addToQueue(track, true);
                  }}
                  aria-label={`Play ${track.title} next`}
                >
                  <ListIcon />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="playlist-track-action"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    addToQueue(track, false);
                  }}
                  aria-label={`Add ${track.title} to queue`}
                >
                  <span aria-hidden="true">+</span>
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="playlist-track-action"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onAddToPlaylist(track);
                  }}
                  aria-label={`Add ${track.title} to playlist`}
                >
                  <ListPlusIcon />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="playlist-track-action reorder-button"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    moveTrack(track, -1);
                  }}
                  disabled={!canReorder || sourceIndex <= 0 || working === `move:${track.messageId}`}
                  aria-label={`Move ${track.title} up`}
                >
                  <ChevronUpIcon />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="playlist-track-action reorder-button"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    moveTrack(track, 1);
                  }}
                  disabled={!canReorder || sourceIndex + 1 >= data.tracks.length || working === `move:${track.messageId}`}
                  aria-label={`Move ${track.title} down`}
                >
                  <ChevronDownIcon />
                </Button>
                <Button
                  type="button"
                  variant="destructive"
                  size="icon"
                  className="playlist-track-action"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    removeTrack(track);
                  }}
                  disabled={working === `remove:${track.messageId}`}
                  aria-label={`Remove ${track.title} from playlist`}
                >
                  <XIcon />
                </Button>
              </a>
            );
          })}
        </div>
      )}
    </main>
  );
}
