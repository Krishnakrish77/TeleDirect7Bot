import { useEffect, useState } from 'react';
import { deleteScrobbleSettings, fetchScrobbleSettings, saveScrobbleSettings } from '../api';
import type { ScrobbleSettings } from '../types';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { CheckIcon } from '../icons';

// Minimal JSON editor for the scrobble filter — same grammar as smart
// playlists ({"all":[{"is":{"genre":"Christmas"}}]}). The server validates
// against the shared rule engine and rejects anything it can't evaluate.
export function ScrobbleSettings() {
  const [settings, setSettings] = useState<ScrobbleSettings | null>(null);
  const [token, setToken] = useState('');
  const [filterText, setFilterText] = useState('');
  const [working, setWorking] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetchScrobbleSettings()
      .then((data) => {
        setSettings(data);
        setFilterText(data.filter ? JSON.stringify(data.filter, null, 2) : '');
      })
      .catch(() => setError('Unable to load scrobble settings'))
      .finally(() => setLoaded(true));
  }, []);

  const save = async () => {
    setWorking(true);
    setError('');
    setStatus('');
    let filter = null;
    if (filterText.trim()) {
      try {
        filter = JSON.parse(filterText);
      } catch {
        setError('Filter must be valid JSON');
        setWorking(false);
        return;
      }
    }
    try {
      const next = await saveScrobbleSettings({ provider: 'listenbrainz', token: token.trim(), filter });
      setSettings(next);
      setToken('');
      setStatus(next.hasToken ? 'Scrobbling connected' : 'Settings saved');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to save settings');
    } finally {
      setWorking(false);
    }
  };

  const disconnect = async () => {
    setWorking(true);
    setError('');
    try {
      await deleteScrobbleSettings();
      setSettings({ provider: '', enabled: false, hasToken: false, filter: null });
      setFilterText('');
      setStatus('Scrobbling disconnected');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to disconnect');
    } finally {
      setWorking(false);
    }
  };

  if (!loaded) return null;

  return (
    <section className="scrobble-settings" aria-label="Scrobbling">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Listen history</p>
          <h2>Scrobbling</h2>
        </div>
      </div>
      <p className="scrobble-copy">
        {settings?.hasToken
          ? 'Completed plays are submitted to ListenBrainz.'
          : 'Connect a ListenBrainz user token to submit every completed play. Get a token at listenbrainz.org/settings/profile.'}
      </p>
      {!settings?.hasToken && (
        <label className="scrobble-token">
          <span>ListenBrainz token</span>
          <Input
            value={token}
            onChange={(event) => setToken(event.currentTarget.value)}
            placeholder="paste your user token"
            autoComplete="off"
          />
        </label>
      )}
      <label className="scrobble-filter">
        <span>Scrobble filter (JSON rules — matching songs are kept off your profile)</span>
        <textarea
          value={filterText}
          onChange={(event) => setFilterText(event.currentTarget.value)}
          rows={4}
          placeholder={'{"all":[{"is":{"genre":"Christmas"}}]}'}
          spellCheck={false}
        />
      </label>
      <div className="scrobble-actions">
        <Button type="button" onClick={save} disabled={working}>
          <CheckIcon />
          <span>{settings?.hasToken ? 'Save' : 'Connect'}</span>
        </Button>
        {settings?.hasToken && (
          <Button type="button" variant="outline" onClick={disconnect} disabled={working}>
            <span>Disconnect</span>
          </Button>
        )}
      </div>
      {status && <p className="playlist-status" role="status"><span>{status}</span></p>}
      {error && <p className="playlist-status" role="alert"><span>{error}</span></p>}
    </section>
  );
}
