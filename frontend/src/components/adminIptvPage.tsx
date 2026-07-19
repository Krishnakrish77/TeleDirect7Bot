import { FormEvent, type Dispatch, type SetStateAction, useMemo, useState } from 'react';
import { deleteAdminIptvChannel, importAdminIptvM3u, importAdminIptvM3uUrl, saveAdminIptvChannel, testAdminIptvStream } from '../api';
import { BroadcastIcon, CheckIcon, PlayIcon, SearchIcon, ShieldIcon, XIcon } from '../icons';
import { ErrorPanel, LoadingRows } from './common';
import { AdminGate } from './adminPage';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import { Checkbox } from './ui/checkbox';
import { Input } from './ui/input';
import { Textarea } from './ui/textarea';
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
    tvgId: channel.tvgId,
    tvgName: channel.tvgName,
    duration: channel.duration,
    attrs: channel.attrs,
    extras: channel.extras,
    streamHeaders: channel.streamHeaders,
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
      const response = await testAdminIptvStream(form.streamUrl, form.streamHeaders || {});
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
            <Button asChild variant="secondary" size="sm"><a href="/app/admin"><ShieldIcon />Console</a></Button>
            <Button asChild variant="secondary" size="sm"><a href="/app/live-tv"><BroadcastIcon />Live TV</a></Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => reload()} disabled={loading}><CheckIcon />Refresh</Button>
          </div>
        </div>
      </section>

      {loading && !data && <LoadingRows variant="detail" />}
      {error && <ErrorPanel message={error} />}
      {notice && <p className="admin-notice" role="status">{notice}</p>}

      <section className="iptv-admin-layout" aria-label="IPTV manager">
        <Card className="iptv-editor-panel">
          <CardContent>
          <div className="section-heading">
            <div>
              <span>Channel</span>
              <h2>{form.id ? 'Edit channel' : 'Add channel'}</h2>
            </div>
            {form.id && (
              <Button type="button" variant="secondary" size="sm" onClick={() => setForm(emptyForm)}><XIcon />Clear</Button>
            )}
          </div>
          <form className="iptv-channel-form" onSubmit={submitChannel}>
            <label>
              <span>Name</span>
              <Input value={form.name} onChange={(event) => setForm({ ...form, name: event.currentTarget.value })} required />
            </label>
            <label>
              <span>Category</span>
              <Input value={form.category} onChange={(event) => setForm({ ...form, category: event.currentTarget.value })} placeholder="News" />
            </label>
            <label className="wide">
              <span>Stream URL</span>
              <Input value={form.streamUrl} onChange={(event) => setForm({ ...form, streamUrl: event.currentTarget.value })} required />
            </label>
            <label className="wide">
              <span>Logo URL</span>
              <Input value={form.logoUrl} onChange={(event) => setForm({ ...form, logoUrl: event.currentTarget.value })} />
            </label>
            <label>
              <span>Sort</span>
              <Input type="number" value={form.sortOrder} onChange={(event) => setForm({ ...form, sortOrder: Number(event.currentTarget.value) || 0 })} />
            </label>
            <label className="iptv-toggle-field">
              <Checkbox checked={form.enabled} onCheckedChange={(enabled) => setForm({ ...form, enabled: enabled === true })} />
              <span>Enabled</span>
            </label>
            <div className="iptv-form-actions">
              <Button type="submit" disabled={busy === 'save'}>
                <CheckIcon />
                {busy === 'save' ? 'Saving' : 'Save'}
              </Button>
              <Button type="button" variant="secondary" onClick={testStream} disabled={busy === 'test' || !form.streamUrl.trim()}>
                <PlayIcon />
                {busy === 'test' ? 'Checking' : 'Validate'}
              </Button>
            </div>
          </form>
          </CardContent>
        </Card>

        <Card className="iptv-import-panel">
          <CardContent className="grid gap-2.5 iptv-import-panel-content">
          <div className="section-heading">
            <div>
              <span>Playlist</span>
              <h2>M3U import</h2>
            </div>
          </div>
          <label className="iptv-import-url">
            <span>Playlist URL</span>
            <Input
              value={m3uUrl}
              onChange={(event) => setM3uUrl(event.currentTarget.value)}
              placeholder="https://iptv-org.github.io/iptv/categories/news.m3u"
            />
          </label>
          <Button type="button" onClick={importPlaylistUrl} disabled={busy === 'import-url' || !m3uUrl.trim()}>
            <CheckIcon />
            {busy === 'import-url' ? 'Importing' : 'Import URL'}
          </Button>
          <Textarea value={m3u} onChange={(event) => setM3u(event.currentTarget.value)} placeholder="#EXTM3U" />
          <Button type="button" onClick={importPlaylist} disabled={busy === 'import' || !m3u.trim()}>
            <CheckIcon />
            {busy === 'import' ? 'Importing' : 'Import M3U'}
          </Button>
          </CardContent>
        </Card>

        <Card className="iptv-list-panel">
          <div className="iptv-list-head">
            <div className="section-heading">
              <div>
                <span>Configured</span>
                <h2>Channels</h2>
              </div>
            </div>
            <label className="iptv-list-search">
              <SearchIcon />
              <Input name="iptv-channel-search" value={query} onChange={(event) => setQuery(event.currentTarget.value)} placeholder="Search channels" />
              {query && (
                <Button type="button" variant="ghost" size="icon-sm" aria-label="Clear IPTV search" onClick={() => setQuery('')}>
                  <XIcon />
                </Button>
              )}
            </label>
          </div>
          <div className="iptv-channel-admin-list">
            {filteredChannels.map((channel) => (
              <article key={channel.id} className={channel.enabled ? 'iptv-admin-row' : 'iptv-admin-row disabled'}>
                {channel.logoUrl ? <img src={channel.logoUrl} alt="" /> : <span><BroadcastIcon /></span>}
                <div>
                  <strong>{channel.name}</strong>
                  <small>{channel.category || 'Uncategorized'}</small>
                  <em>{channel.streamUrl}</em>
                </div>
                <div className="iptv-row-state">
                  <Badge variant={channel.enabled ? 'success' : 'muted'}>{channel.enabled ? 'Enabled' : 'Disabled'}</Badge>
                  <small>Sort {channel.sortOrder || 0}</small>
                </div>
                <div className="iptv-row-actions">
                  <Button type="button" variant="outline" size="sm" onClick={() => setForm(channelToPayload(channel))}>Edit</Button>
                  <Button type="button" variant="ghost" size="sm" onClick={() => toggleChannel(channel)} disabled={busy === channel.id}>
                    {channel.enabled ? 'Disable' : 'Enable'}
                  </Button>
                  <Button type="button" variant="destructive" size="sm" onClick={() => deleteChannel(channel)} disabled={busy === channel.id}>Delete</Button>
                </div>
              </article>
            ))}
            {!filteredChannels.length && (
              <div className="admin-empty-list">
                <BroadcastIcon />
                <strong>No channels found</strong>
              </div>
            )}
          </div>
        </Card>
      </section>
    </main>
  );
}
