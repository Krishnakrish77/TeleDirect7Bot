import { useEffect, useState } from 'react';
import { ApiError, createAiMix, saveAiMixPlaylist } from '../api';
import { ChevronDownIcon, ChevronUpIcon, ListPlusIcon, MusicIcon, PlayIcon, ShuffleIcon, SparkleIcon, TrashIcon } from '../icons';
import type { AiMixDiscovery, AiMixResponse, WatchTrack } from '../types';
import { Button } from './ui/button';
import { Input } from './ui/input';

const DRAFT_KEY = 'td:ai-mix-draft:v1';

type MixDraft = AiMixResponse & { edited?: boolean };

function readDraft(): MixDraft | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || 'null') as MixDraft | null;
    return value?.tracks?.length ? value : null;
  } catch {
    return null;
  }
}

function displayDuration(tracks: WatchTrack[]) {
  const seconds = tracks.reduce((total, track) => total + (Number(track.duration) || 0), 0);
  if (!seconds) return '';
  const minutes = Math.round(seconds / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} hr ${minutes % 60} min` : `${minutes} min`;
}

export function AiMixPanel({
  onBack,
  onPlay,
  onShuffle,
}: {
  onBack: () => void;
  onPlay: (tracks: WatchTrack[]) => void;
  onShuffle: (tracks: WatchTrack[]) => void;
}) {
  const [draft, setDraft] = useState<MixDraft | null>(readDraft);
  const [prompt, setPrompt] = useState(() => readDraft()?.prompt || '');
  const [discovery, setDiscovery] = useState<AiMixDiscovery>(() => readDraft()?.discovery || 'balanced');
  const [savingName, setSavingName] = useState(() => readDraft()?.title || 'Your AI Mix');
  const [working, setWorking] = useState<'generate' | 'save' | ''>('');
  const [status, setStatus] = useState('');

  useEffect(() => {
    try {
      if (draft) sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
      else sessionStorage.removeItem(DRAFT_KEY);
    } catch {
      // A mix remains usable when session storage is unavailable.
    }
  }, [draft]);

  const generate = async (replace = false) => {
    if (replace && draft?.edited && !window.confirm('Replace your edited mix with a new one?')) return;
    setWorking('generate');
    setStatus('');
    try {
      const mix = await createAiMix({ prompt, discovery });
      setDraft({ ...mix, edited: false });
      setPrompt(mix.prompt);
      setDiscovery(mix.discovery);
      setSavingName(mix.title);
      setStatus(mix.generated ? 'Your mix is ready to review.' : 'Your personalised mix is ready.');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Could not create a mix right now.');
    } finally {
      setWorking('');
    }
  };

  const updateTracks = (tracks: WatchTrack[]) => setDraft((current) => current ? { ...current, tracks, edited: true } : current);
  const remove = (index: number) => updateTracks((draft?.tracks || []).filter((_, itemIndex) => itemIndex !== index));
  const move = (index: number, offset: -1 | 1) => {
    const tracks = (draft?.tracks || []).slice();
    const target = index + offset;
    if (target < 0 || target >= tracks.length) return;
    [tracks[index], tracks[target]] = [tracks[target], tracks[index]];
    updateTracks(tracks);
  };

  const save = async () => {
    const name = savingName.trim();
    if (!draft?.tracks.length || !name) return;
    setWorking('save');
    setStatus('');
    try {
      const playlist = await saveAiMixPlaylist(name, draft.tracks);
      setDraft(null);
      setStatus(`Saved as ${playlist.name}.`);
    } catch (error) {
      setStatus(error instanceof ApiError && error.status === 409
        ? 'A track changed in your library. Regenerate the mix, then save it again.'
        : error instanceof Error ? error.message : 'Could not save this mix.');
    } finally {
      setWorking('');
    }
  };

  return (
    <section className="ai-mix" aria-labelledby="ai-mix-title">
      <div className="ai-mix-heading">
        <div>
          <p className="eyebrow"><MusicIcon /> Music only</p>
          <h3 id="ai-mix-title">Create an AI Mix</h3>
          <p>Build a temporary listening session from your library, then keep it only if it earns a save.</p>
        </div>
        <Button type="button" variant="ghost" size="sm" className="text-button" onClick={onBack}>Back to picks</Button>
      </div>

      <div className="ai-mix-controls">
        <label>
          <span>What do you want to hear?</span>
          <Input value={prompt} onChange={(event) => setPrompt(event.currentTarget.value)} maxLength={240} placeholder="For me, or try ‘Tamil night drive’" disabled={working === 'generate'} />
        </label>
        <div className="ai-mix-suggestions" aria-label="Mix prompt suggestions">
          {['Tamil night drive', 'Focus without lyrics', 'Warm acoustic evening'].map((suggestion) => (
            <Button key={suggestion} type="button" variant="outline" size="sm" onClick={() => setPrompt(suggestion)} disabled={working === 'generate'}>{suggestion}</Button>
          ))}
        </div>
        <div className="ai-mix-discovery" role="group" aria-label="Mix discovery level">
          {([
            ['familiar', 'Familiar'],
            ['balanced', 'Balanced'],
            ['discover', 'Discover'],
          ] as const).map(([value, label]) => (
            <Button key={value} type="button" variant={discovery === value ? 'default' : 'outline'} size="sm" onClick={() => setDiscovery(value)} disabled={working === 'generate'}>{label}</Button>
          ))}
        </div>
        <Button type="button" onClick={() => void generate(Boolean(draft))} disabled={working === 'generate'}>
          <SparkleIcon />
          <span>{working === 'generate' ? 'Building mix' : draft ? 'Regenerate mix' : 'Create mix'}</span>
        </Button>
      </div>

      {draft && (
        <div className="ai-mix-draft">
          <div className="ai-mix-draft-head">
            <div>
              <strong>{draft.title}</strong>
              <span>{draft.description}</span>
              <small>{draft.tracks.length} tracks{displayDuration(draft.tracks) ? ` · ${displayDuration(draft.tracks)}` : ''}</small>
            </div>
            <div className="ai-mix-play-actions">
              <Button type="button" size="sm" onClick={() => onPlay(draft.tracks)}><PlayIcon /> Play</Button>
              <Button type="button" variant="outline" size="sm" onClick={() => onShuffle(draft.tracks)}><ShuffleIcon /> Shuffle</Button>
            </div>
          </div>
          <ol className="ai-mix-track-list">
            {draft.tracks.map((track, index) => (
              <li key={track.key}>
                <span className="ai-mix-track-number">{index + 1}</span>
                <img src={track.posterUrl || track.thumbUrl} alt="" loading="lazy" decoding="async" />
                <span className="ai-mix-track-copy"><strong>{track.title}</strong><small>{[track.artist, track.albumTitle].filter(Boolean).join(' · ')}</small></span>
                <span className="ai-mix-track-actions">
                  <Button type="button" variant="ghost" size="icon-sm" aria-label={`Move ${track.title} up`} disabled={index === 0} onClick={() => move(index, -1)}><ChevronUpIcon /></Button>
                  <Button type="button" variant="ghost" size="icon-sm" aria-label={`Move ${track.title} down`} disabled={index === draft.tracks.length - 1} onClick={() => move(index, 1)}><ChevronDownIcon /></Button>
                  <Button type="button" variant="ghost" size="icon-sm" aria-label={`Remove ${track.title}`} onClick={() => remove(index)}><TrashIcon /></Button>
                </span>
              </li>
            ))}
          </ol>
          {draft.tracks.length > 0 ? (
            <div className="ai-mix-save">
              <Input value={savingName} onChange={(event) => setSavingName(event.currentTarget.value)} maxLength={100} aria-label="Mix playlist name" />
              <Button type="button" onClick={() => void save()} disabled={working === 'save' || !savingName.trim()}><ListPlusIcon /> {working === 'save' ? 'Saving' : 'Save as playlist'}</Button>
            </div>
          ) : <p className="ai-mix-empty">Every track was removed. Regenerate to build a new mix.</p>}
        </div>
      )}
      {status && <p className="ai-mix-status" role="status">{status}</p>}
    </section>
  );
}
