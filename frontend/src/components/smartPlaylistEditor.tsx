import { useState } from 'react';
import type { PlaylistDetailResponse, SmartPlaylistRules, SmartRuleNode } from '../types';
import { savePlaylistRules } from '../api';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { CheckIcon, XIcon } from '../icons';

// Field/op metadata mirrors main/utils/smart_playlists.py RULE_FIELDS and
// rule_filter.ALL_OPS. Keep the two in sync.
const FIELDS = [
  { value: 'genre', label: 'Genre', ops: ['eq', 'ne', 'contains'] },
  { value: 'artist', label: 'Artist', ops: ['eq', 'ne', 'contains'] },
  { value: 'album', label: 'Album', ops: ['eq', 'ne', 'contains'] },
  { value: 'title', label: 'Title', ops: ['eq', 'ne', 'contains'] },
  { value: 'year', label: 'Year', ops: ['eq', 'gte', 'lte'] },
  { value: 'playCount', label: 'Play count', ops: ['gte', 'lte'] },
  { value: 'lastPlayedDays', label: 'Last played within (days)', ops: ['inTheLast'] },
  { value: 'rating', label: 'Rating', ops: ['eq', 'gte'] },
] as const;

const OPS: Record<string, string> = {
  eq: 'is',
  ne: 'is not',
  contains: 'contains',
  gt: '>',
  gte: '≥',
  lt: '<',
  lte: '≤',
  inTheLast: 'in the last (days)',
};

type DraftRow = { field: string; op: string; value: string };

function opOptions(field: string): readonly string[] {
  return FIELDS.find((f) => f.value === field)?.ops || ['eq'];
}

function rowValueKind(field: string): 'text' | 'number' {
  return field === 'year' || field === 'playCount' || field === 'lastPlayedDays' || field === 'rating'
    ? 'number'
    : 'text';
}

function isLeaf(node: SmartRuleNode): node is { field: string; op: string; value: unknown } {
  return 'field' in node && typeof node.field === 'string';
}

function rulesToDrafts(rules: SmartPlaylistRules | null | undefined): DraftRow[] {
  if (!rules) return [{ field: 'genre', op: 'eq', value: '' }];
  const children = ('all' in rules ? rules.all : rules.any) || [];
  const leaves = children.filter(isLeaf);
  if (!leaves.length) return [{ field: 'genre', op: 'eq', value: '' }];
  return leaves.map((leaf) => ({
    field: leaf.field,
    op: leaf.op,
    value: leaf.value === null || leaf.value === undefined ? '' : String(leaf.value),
  }));
}

function draftsToRules(mode: 'all' | 'any', drafts: DraftRow[]): SmartPlaylistRules {
  const leaves = drafts.map((row) => {
    const kind = rowValueKind(row.field);
    const value = kind === 'number' ? Number(row.value) : row.value;
    return { field: row.field, op: row.op, value };
  });
  return mode === 'any' ? { any: leaves } : { all: leaves };
}

export function SmartPlaylistEditor({
  data,
  setData,
  onDone,
  onCancel,
}: {
  data: PlaylistDetailResponse;
  setData: (updater: (current: PlaylistDetailResponse | null) => PlaylistDetailResponse | null) => void;
  onDone: (updated: PlaylistDetailResponse) => void;
  onCancel: () => void;
}) {
  const [mode, setMode] = useState<'all' | 'any'>('all');
  const [drafts, setDrafts] = useState<DraftRow[]>(() => rulesToDrafts(data.rules));
  const [limit, setLimit] = useState(String(data.limit || 50));
  const [sort, setSort] = useState(data.sort || 'recentlyAdded');
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');

  const setRow = (index: number, patch: Partial<DraftRow>) => {
    setDrafts((current) => current.map((row, i) => {
      if (i !== index) return row;
      const next = { ...row, ...patch };
      if (patch.field && !opOptions(patch.field).includes(next.op)) next.op = opOptions(patch.field)[0];
      return next;
    }));
  };

  const submit = async () => {
    setWorking(true);
    setError('');
    const clean = drafts.filter((row) => row.value.trim() !== '');
    if (!clean.length) {
      setError('Add at least one rule with a value');
      setWorking(false);
      return;
    }
    try {
      const updated = await savePlaylistRules(data.playlistId, {
        rules: draftsToRules(mode, clean),
        sort,
        limit: Math.max(1, Number(limit) || 50),
      });
      onDone(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to save rules');
      setWorking(false);
    }
  };

  const clearRules = async () => {
    setWorking(true);
    setError('');
    try {
      const updated = await savePlaylistRules(data.playlistId, { rules: null, sort: 'recentlyAdded', limit: 50 });
      onDone(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to clear rules');
      setWorking(false);
    }
  };

  return (
    <section className="smart-playlist-editor" aria-label="Smart playlist rules">
      <div className="smart-editor-head">
        <strong>Smart rules</strong>
        <Select value={mode} onValueChange={(value) => setMode(value as 'all' | 'any')}>
          <SelectTrigger aria-label="Rule match mode" className="smart-mode-select">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Match all rules</SelectItem>
            <SelectItem value="any">Match any rule</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {drafts.map((row, index) => (
        <div key={index} className="smart-rule-row">
          <Select value={row.field} onValueChange={(value) => setRow(index, { field: value })}>
            <SelectTrigger aria-label="Rule field"><SelectValue /></SelectTrigger>
            <SelectContent>
              {FIELDS.map((field) => (
                <SelectItem key={field.value} value={field.value}>{field.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={row.op} onValueChange={(value) => setRow(index, { op: value })}>
            <SelectTrigger aria-label="Rule operator"><SelectValue /></SelectTrigger>
            <SelectContent>
              {opOptions(row.field).map((op) => (
                <SelectItem key={op} value={op}>{OPS[op] || op}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            value={row.value}
            type={rowValueKind(row.field)}
            onChange={(event) => setRow(index, { value: event.currentTarget.value })}
            placeholder={rowValueKind(row.field) === 'number' ? '0' : 'text'}
            aria-label="Rule value"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Remove rule"
            onClick={() => setDrafts((current) => current.filter((_, i) => i !== index))}
          >
            <XIcon />
          </Button>
        </div>
      ))}
      <div className="smart-editor-foot">
        <Button type="button" variant="outline" size="sm" onClick={() => setDrafts((current) => [...current, { field: 'genre', op: 'eq', value: '' }])}>
          Add rule
        </Button>
        <label className="smart-limit">
          <span>Limit</span>
          <Input
            type="number"
            min={1}
            max={500}
            value={limit}
            onChange={(event) => setLimit(event.currentTarget.value)}
            aria-label="Track limit"
          />
        </label>
        <label className="smart-sort">
          <span>Sort</span>
          <Select value={sort} onValueChange={setSort}>
            <SelectTrigger aria-label="Smart playlist sort"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="recentlyAdded">Recently added</SelectItem>
              <SelectItem value="playCount">Most played</SelectItem>
              <SelectItem value="title">Title</SelectItem>
              <SelectItem value="random">Random</SelectItem>
            </SelectContent>
          </Select>
        </label>
        <div className="smart-editor-actions">
          {data.smart && (
            <Button type="button" variant="outline" onClick={clearRules} disabled={working}>
              Turn off
            </Button>
          )}
          <Button type="button" variant="outline" onClick={onCancel} disabled={working}>
            Cancel
          </Button>
          <Button type="button" onClick={submit} disabled={working}>
            <CheckIcon />
            <span>Save</span>
          </Button>
        </div>
      </div>
      {error && <p className="playlist-status" role="alert"><span>{error}</span></p>}
    </section>
  );
}
