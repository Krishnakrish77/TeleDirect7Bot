import { FormEvent, type Dispatch, type SetStateAction, useMemo, useState } from 'react';
import { deleteAdminIptvChannel, importAdminIptvM3u, importAdminIptvM3uUrl, saveAdminIptvChannel, testAdminIptvStream } from '../api';
import { CheckIcon, PlayIcon, SearchIcon, ShieldIcon, TvIcon, XIcon } from '../icons';
import { ErrorPanel, LoadingRows } from './common';
import { AdminGate } from './adminPage';
import type { AdminIptvResponse, IptvChannel, IptvChannelPayload, User } from '../types';

const emptyForm: IptvChannelPayload = {
  name: '',
  streamUrl: '',
  logoUrl: '',
  category: '',
  enabled: true,
  sortOrder: 0,
};

function channelToPayload(channel: IptvChannel): IptvChannelPayload {
  return {
    id: channel.id,
    name: channel.name,
    streamUrl: channel.streamUrl,
    logoUrl: channel.logoUrl,
    category: channel.category,
    enabled: channel.enabled,
    sortOrder: channel.sortOrder,
  };
}


export function AdminIptvPage({
  user,
  data,
  loading,
  error,
  onSignIn,
  reload,
  setData,
}: {
  user: User | null;
  data: AdminIptvResponse | null;
  loading: boolean;
  error: string;
  onSignIn: () => void;
  reload: () => (() => void) | undefined;
  setData: Dispatch<SetStateAction<AdminIptvResponse | null>>;
}) {
  const [form, setForm] = useState<IptvChannelPayload>(emptyForm);
  const [m3u, setM3u] = useState('');
  const [m3uUrl, setM3uUrl] = useState('');
  const [query, setQuery] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');

  const channels = data?.channels ?? [];
  const filteredChannels = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return channels;
    return channels.filter((channel) => `${channel.name} ${channel.category} ${channel.streamUrl}`.toLowerCase().includes(needle));
  }, [channels, query]);

  if (!user?.is_admin) return <AdminGate user={user} onSignIn={onSignIn} />;

  const applyResponse = (response: AdminIptvResponse) => {
    setData((current) => ({
      ...(current || {}),
      ...response,
      channels: response.channels || current?.channels || [],
    }));
  };

  const submitChannel = async (event: FormEvent) => {
    event.preventDefault();
    setBusy('save');
    setNotice('');
    try {
      const response = await saveAdminIptvChannel({
        ...form,
        name: form.name.trim(),
        streamUrl: form.streamUrl.trim(),
        logoUrl: form.logoUrl.trim(),
        category: form.category.trim() || 'Uncategorized',
      });
      applyResponse(response);
      setForm(emptyForm);
      setNotice(response.channel ? `${response.channel.name} saved` : 'Channel saved');
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Unable to save channel');
    } finally {
      setBusy('');
    }
  };

  const importPlaylist = async () => {
    setBusy('import');
    setNotice('');
    try {
      const response = await importAdminIptvM3u(m3u);
      applyResponse(response);
      setM3u('');
      setNotice(`Imported ${response.imported || 0} of ${response.parsed || 0} channels`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Unable to import M3U');
    } finally {
      setBusy('');
    }
  };

  const importPlaylistUrl = async () => {
    setBusy('import-url');
    setNotice('');
    try {
      const response = await importAdminIptvM3uUrl(m3uUrl.trim());
      applyResponse(response);
      setM3uUrl('');
      setNotice(`Imported ${response.imported || 0} of ${response.parsed || 0} channels`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Unable to import M3U URL');
    } finally {
      setBusy('');
    }
  };

  const toggleChannel = async (channel: IptvChannel) => {
    setBusy(channel.id);
    setNotice('');
    try {
      const response = await saveAdminIptvChannel({ ...channelToPayload(channel), enabled: !channel.enabled });
      applyResponse(response);
      setNotice(`${channel.name} ${channel.enabled ? 'disabled' : 'enabled'}`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Unable to update channel');
    } finally {
      setBusy('');
    }
  };

  const deleteChannel = async (channel: IptvChannel) => {
    if (!window.confirm(`Delete ${channel.name}?`)) return;
    setBusy(channel.id);
    setNotice('');
    try {
      const response = await deleteAdminIptvChannel(channel.id);
      applyResponse(response);
      setNotice(`${channel.name} deleted`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Unable to delete channel');
    } finally {
      setBusy('');
    }
  };

  const testStream = async () => {
    setBusy('test');
    setNotice('');
    try {
      const response = await testAdminIptvStream(form.streamUrl);
      setNotice(response.message);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Unable to validate URL');
    } finally {
      setBusy('');
    }
  };

  return (
    <main className="admin-main admin-iptv-main">
      <section className="admin-hero iptv-admin-hero">
        <div className="admin-hero-top">
          <div className="admin-hero-copy">
            <p className="eyebrow">Admin</p>
            <h1>IPTV channels</h1>
            <p>{channels.length.toLocaleString()} configured channels. {data?.mongoAvailable ? 'Mongo storage is active.' : 'JSON fallback storage is active.'}</p>
          </div>
          <div className="admin-hero-actions">
            <a className="secondary-action" href="/app/admin">
              <ShieldIcon />
              <span>Console</span>
            </a>
            <a className="secondary-action" href="/app/live-tv">
              <TvIcon />
              <span>Live TV</span>
            </a>
            <button type="button" className="secondary-action" onClick={() => reload()} disabled={loading}>
              <CheckIcon />
              <span>Refresh</span>
            </button>
          </div>
        </div>
      </section>

      {loading && !data && <LoadingRows variant="detail" />}
      {error && <ErrorPanel message={error} />}
      {notice && <p className="admin-notice" role="status">{notice}</p>}

      <section className="iptv-admin-layout" aria-label="IPTV manager">
        <div className="admin-panel iptv-editor-panel">
          <div className="section-heading">
            <div>
              <span>Channel</span>
              <h2>{form.id ? 'Edit channel' : 'Add channel'}</h2>
            </div>
            {form.id && (
              <button type="button" className="secondary-action" onClick={() => setForm(emptyForm)}>
                <XIcon />
                <span>Clear</span>
              </button>
            )}
          </div>
          <form className="iptv-channel-form" onSubmit={submitChannel}>
            <label>
              <span>Name</span>
              <input value={form.name} onChange={(event) => setForm({ ...form, name: event.currentTarget.value })} required />
            </label>
            <label>
              <span>Category</span>
              <input value={form.category} onChange={(event) => setForm({ ...form, category: event.currentTarget.value })} placeholder="News" />
            </label>
            <label className="wide">
              <span>Stream URL</span>
              <input value={form.streamUrl} onChange={(event) => setForm({ ...form, streamUrl: event.currentTarget.value })} required />
            </label>
            <label className="wide">
              <span>Logo URL</span>
              <input value={form.logoUrl} onChange={(event) => setForm({ ...form, logoUrl: event.currentTarget.value })} />
            </label>
            <label>
              <span>Sort</span>
              <input type="number" value={form.sortOrder} onChange={(event) => setForm({ ...form, sortOrder: Number(event.currentTarget.value) || 0 })} />
            </label>
            <label className="iptv-toggle-field">
              <input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.currentTarget.checked })} />
              <span>Enabled</span>
            </label>
            <div className="iptv-form-actions">
              <button type="submit" className="primary-action" disabled={busy === 'save'}>
                <CheckIcon />
                <span>{busy === 'save' ? 'Saving' : 'Save'}</span>
              </button>
              <button type="button" className="secondary-action" onClick={testStream} disabled={busy === 'test' || !form.streamUrl.trim()}>
                <PlayIcon />
                <span>{busy === 'test' ? 'Checking' : 'Validate'}</span>
              </button>
            </div>
          </form>
        </div>

        <div className="admin-panel iptv-import-panel">
          <div className="section-heading">
            <div>
              <span>Playlist</span>
              <h2>M3U import</h2>
            </div>
          </div>
          <label className="iptv-import-url">
            <span>Playlist URL</span>
            <input
              value={m3uUrl}
              onChange={(event) => setM3uUrl(event.currentTarget.value)}
              placeholder="https://iptv-org.github.io/iptv/categories/news.m3u"
            />
          </label>
          <button type="button" className="primary-action" onClick={importPlaylistUrl} disabled={busy === 'import-url' || !m3uUrl.trim()}>
            <CheckIcon />
            <span>{busy === 'import-url' ? 'Importing' : 'Import URL'}</span>
          </button>
          <textarea value={m3u} onChange={(event) => setM3u(event.currentTarget.value)} placeholder="#EXTM3U" />
          <button type="button" className="primary-action" onClick={importPlaylist} disabled={busy === 'import' || !m3u.trim()}>
            <CheckIcon />
            <span>{busy === 'import' ? 'Importing' : 'Import M3U'}</span>
          </button>
        </div>

        <div className="admin-panel iptv-list-panel">
          <div className="iptv-list-head">
            <div className="section-heading">
              <div>
                <span>Configured</span>
                <h2>Channels</h2>
              </div>
            </div>
            <label className="iptv-list-search">
              <SearchIcon />
              <input value={query} onChange={(event) => setQuery(event.currentTarget.value)} placeholder="Search channels" />
              {query && (
                <button type="button" className="icon-button" aria-label="Clear IPTV search" onClick={() => setQuery('')}>
                  <XIcon />
                </button>
              )}
            </label>
          </div>
          <div className="iptv-channel-admin-list">
            {filteredChannels.map((channel) => (
              <article key={channel.id} className={channel.enabled ? 'iptv-admin-row' : 'iptv-admin-row disabled'}>
                {channel.logoUrl ? <img src={channel.logoUrl} alt="" /> : <span><TvIcon /></span>}
                <div>
                  <strong>{channel.name}</strong>
                  <small>{channel.category || 'Uncategorized'}</small>
                  <em>{channel.streamUrl}</em>
                </div>
                <div className="iptv-row-state">
                  <i>{channel.enabled ? 'Enabled' : 'Disabled'}</i>
                  <small>Sort {channel.sortOrder || 0}</small>
                </div>
                <div className="iptv-row-actions">
                  <button type="button" onClick={() => setForm(channelToPayload(channel))}>Edit</button>
                  <button type="button" onClick={() => toggleChannel(channel)} disabled={busy === channel.id}>
                    {channel.enabled ? 'Disable' : 'Enable'}
                  </button>
                  <button type="button" className="danger-text" onClick={() => deleteChannel(channel)} disabled={busy === channel.id}>Delete</button>
                </div>
              </article>
            ))}
            {!filteredChannels.length && (
              <div className="admin-empty-list">
                <TvIcon />
                <strong>No channels found</strong>
              </div>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}
