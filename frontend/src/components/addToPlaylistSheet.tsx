import { FormEvent, useEffect, useState } from 'react';
import { addTrackToPlaylist, createPlaylist } from '../api';
import { usePlaylists } from '../hooks/data';
import { CheckIcon, ListPlusIcon, XIcon } from '../icons';
import type { User, WatchTrack } from '../types';
import { LoadingRows } from './common';
import { Button } from './ui/button';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from './ui/dialog';
import { Input } from './ui/input';

export function AddToPlaylistSheet({
  open,
  track,
  user,
  onClose,
  onSignIn,
}: {
  open: boolean;
  track: WatchTrack | null;
  user: User | null;
  onClose: () => void;
  onSignIn: () => void;
}) {
  const playlists = usePlaylists(user, Boolean(open && user));
  const [name, setName] = useState('');
  const [busyId, setBusyId] = useState('');
  const [creating, setCreating] = useState(false);
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (!open) return;
    setName('');
    setBusyId('');
    setCreating(false);
    setStatus('');
  }, [open, track?.key]);

  if (!open || !track) return null;

  const addToExisting = async (playlistId: string, playlistName: string) => {
    setBusyId(playlistId);
    setStatus('');
    try {
      await addTrackToPlaylist(playlistId, track);
      setStatus(`Added to ${playlistName}`);
      void playlists.reload();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Unable to add track');
    } finally {
      setBusyId('');
    }
  };

  const submitCreate = async (event: FormEvent) => {
    event.preventDefault();
    const playlistName = name.trim();
    if (!playlistName) return;
    setCreating(true);
    setStatus('');
    try {
      const created = await createPlaylist(playlistName);
      await addTrackToPlaylist(created.playlistId, track);
      setName('');
      setStatus(`Added to ${created.name}`);
      void playlists.reload();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Unable to create playlist');
    } finally {
      setCreating(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}>
      <DialogContent className="playlist-sheet" aria-describedby="playlist-sheet-track">
        <div className="drawer-heading">
          <div>
            <p className="eyebrow">Playlist</p>
            <DialogTitle>Add song</DialogTitle>
            <DialogDescription id="playlist-sheet-track" className="playlist-sheet-track">{track.title}</DialogDescription>
          </div>
          <DialogClose asChild>
            <Button type="button" variant="ghost" size="icon-sm" className="icon-button" aria-label="Close"><XIcon /></Button>
          </DialogClose>
        </div>

        {!user ? (
          <div className="playlist-sheet-empty">
            <ListPlusIcon />
            <strong>Sign in to save playlists</strong>
            <Button type="button" onClick={onSignIn}>Sign in</Button>
          </div>
        ) : (
          <>
            <form className="playlist-create-form" onSubmit={submitCreate}>
              <Input
                value={name}
                onChange={(event) => setName(event.currentTarget.value)}
                placeholder="New playlist name"
                maxLength={100}
              />
              <Button type="submit" disabled={creating || !name.trim()}>
                <ListPlusIcon />
                <span>{creating ? 'Creating' : 'Create'}</span>
              </Button>
            </form>

            {playlists.loading && <LoadingRows />}
            {playlists.error && <p className="player-error">{playlists.error}</p>}
            {!playlists.loading && playlists.data && !playlists.data.available && (
              <p className="player-error">Playlist storage is unavailable on this deployment.</p>
            )}
            {!playlists.loading && playlists.data?.playlists.length === 0 && (
              <div className="playlist-sheet-empty compact">
                <ListPlusIcon />
                <strong>No playlists yet</strong>
                <span>Create one above and this song will be added to it.</span>
              </div>
            )}
            {playlists.data?.playlists.length ? (
              <div className="playlist-picker-list">
                {playlists.data.playlists.map((playlist) => (
                  <Button
                    key={playlist.playlistId}
                    type="button"
                    variant="outline"
                    className="playlist-picker-row"
                    onClick={() => addToExisting(playlist.playlistId, playlist.name)}
                    disabled={Boolean(busyId)}
                  >
                    <PlaylistCover covers={playlist.coverUrls} name={playlist.name} />
                    <span>
                      <strong>{playlist.name}</strong>
                      <small>{playlist.trackCount} {playlist.trackCount === 1 ? 'track' : 'tracks'}</small>
                    </span>
                    {busyId === playlist.playlistId ? <small>Adding</small> : <ListPlusIcon />}
                  </Button>
                ))}
              </div>
            ) : null}
            {status && (
              <p className="playlist-status" role="status">
                {status.startsWith('Added') && <CheckIcon />}
                <span>{status}</span>
              </p>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function PlaylistCover({ covers, name }: { covers: string[]; name: string }) {
  const initials = name.trim().slice(0, 1).toUpperCase() || 'P';
  return (
    <span className={`playlist-cover cover-count-${Math.min(covers.length, 4)}`} aria-hidden="true">
      {covers.slice(0, 4).map((cover) => (
        <img key={cover} src={cover} alt="" loading="lazy" decoding="async" />
      ))}
      {covers.length === 0 && <strong>{initials}</strong>}
    </span>
  );
}
