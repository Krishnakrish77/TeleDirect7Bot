import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { deleteBuddyHistory, fetchBuddyHistory, fetchBuddyPrefs, sendBuddyMessage, setBuddyEnabled } from '../api';
import type { BuddyChatContext, BuddyContextInfo, BuddyMessage } from '../types';
import { CouchMateIcon, SparkleIcon } from '../icons';
import { Button } from './ui/button';
import { Input } from './ui/input';

type BuddyStatus = 'loading' | 'signed-out' | 'unavailable' | 'off' | 'on';

/** HTTP status of a failed api call, regardless of the error class identity. */
function errorStatus(err: unknown): number {
  const status = (err as { status?: unknown } | null)?.status;
  return typeof status === 'number' ? status : 0;
}

type BannerSource = {
  title?: string;
  kind?: 'movie' | 'tv';
  seriesTitle?: string;
  season?: number | null;
  episode?: number | null;
  cutoffLabel?: string;
  progress?: number | null;
};

function bannerFor(ctx: BannerSource): string {
  if (ctx.kind === 'tv' && ctx.episode != null) {
    const title = ctx.seriesTitle || ctx.title || 'this series';
    const code = ctx.cutoffLabel
      || (ctx.season != null
        ? `S${String(ctx.season).padStart(2, '0')}E${String(ctx.episode).padStart(2, '0')}`
        : `Episode ${ctx.episode}`);
    const progress = ctx.progress != null ? ` · ${Math.round(ctx.progress * 100)}% through` : '';
    return `Chatting about ${title} ${code}${progress} — no spoilers beyond this episode`;
  }
  if (ctx.title) {
    return `Chatting about ${ctx.title} — no spoilers`;
  }
  // Bare reference: the server hasn't echoed the resolved context yet.
  return 'Chatting about this title — no spoilers';
}

/** One-tap openers shown in the empty state. */
const SUGGESTIONS: ReadonlyArray<{ label: string; prompt: string }> = [
  { label: 'Where am I?', prompt: 'What episode am I on, and how far through it am I?' },
  { label: 'The story so far', prompt: 'Catch me up on the story so far.' },
  { label: 'A character', prompt: 'Which character is the most interesting so far, and why?' },
  { label: 'What to watch next', prompt: 'What should I watch after this?' },
];

/**
 * Shared CouchMate chat. All spoiler decisions happen server-side; this
 * component only forwards the opaque item reference it is given and renders
 * whatever state the prefs/chat endpoints report.
 */
export function BuddyChat({ context }: { context?: BuddyChatContext }) {
  const [status, setStatus] = useState<BuddyStatus>('loading');
  const [messages, setMessages] = useState<BuddyMessage[]>([]);
  const [serverContext, setServerContext] = useState<BuddyContextInfo | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [justCleared, setJustCleared] = useState(false);
  const [error, setError] = useState('');
  const [failedMessage, setFailedMessage] = useState('');
  const listRef = useRef<HTMLDivElement | null>(null);
  // Guards against a slow prefs/history response landing after the context
  // prop changed (e.g. next episode started playing underneath the chat).
  const generation = useRef(0);

  const ctxKey = context ? `${context.itemId}:${context.messageId ?? ''}` : '';

  const loadHistory = (gen: number) => {
    setHistoryLoading(true);
    Promise.resolve()
      .then(() => fetchBuddyHistory(context ? { itemId: context.itemId, messageId: context.messageId } : undefined))
      .then((res) => {
        if (generation.current === gen) setMessages(res.messages || []);
      })
      .catch(() => { /* History is best-effort; the composer still works. */ })
      .finally(() => {
        if (generation.current === gen) setHistoryLoading(false);
      });
  };

  useEffect(() => {
    const gen = ++generation.current;
    setStatus('loading');
    setMessages([]);
    setServerContext(null);
    setError('');
    setFailedMessage('');
    setJustCleared(false);
    Promise.resolve()
      .then(() => fetchBuddyPrefs())
      .then((prefs) => {
        if (generation.current !== gen) return;
        if (!prefs.enabled) {
          setStatus('off');
          return;
        }
        setStatus('on');
        loadHistory(gen);
      })
      .catch((err) => {
        if (generation.current !== gen) return;
        setStatus(errorStatus(err) === 401 ? 'signed-out' : 'unavailable');
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctxKey]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending, status]);

  const enable = () => {
    if (enabling) return;
    // Capture before the POST: if the context prop changes mid-flight, the
    // prefs effect bumps the generation and this success body must not load
    // the stale session's history.
    const gen = generation.current;
    setEnabling(true);
    setError('');
    Promise.resolve()
      .then(() => setBuddyEnabled(true))
      .then(() => {
        if (generation.current !== gen) return;
        setStatus('on');
        loadHistory(gen);
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not enable CouchMate.'))
      .finally(() => setEnabling(false));
  };

  const clearChat = () => {
    if (clearing || sending) return;
    const scope = context ? 'this conversation' : 'all CouchMate conversations';
    if (!window.confirm(`Clear ${scope}? This cannot be undone.`)) return;
    const gen = generation.current;
    setClearing(true);
    setError('');
    Promise.resolve()
      .then(() => deleteBuddyHistory(context ? { itemId: context.itemId, messageId: context.messageId } : undefined))
      .then(() => {
        if (generation.current !== gen) return;
        setMessages([]);
        setJustCleared(true);
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not clear the chat.'))
      .finally(() => setClearing(false));
  };

  const send = (text: string) => {
    const message = text.trim();
    if (!message || sending) return;
    setSending(true);
    setError('');
    setFailedMessage('');
    setJustCleared(false);
    setDraft('');
    const optimistic: BuddyMessage = { role: 'user', text: message, t: Math.floor(Date.now() / 1000) };
    setMessages((current) => [...current, optimistic]);
    Promise.resolve()
      .then(() => sendBuddyMessage({ message, itemId: context?.itemId, messageId: context?.messageId }))
      .then((res) => {
        setMessages((current) => [...current, { role: 'buddy' as const, text: res.reply, t: Math.floor(Date.now() / 1000) }]);
        if (res.context) setServerContext(res.context);
      })
      .catch((err) => {
        // Roll back the optimistic bubble and restore the draft so nothing is lost.
        setMessages((current) => current.filter((entry) => entry !== optimistic));
        setDraft(message);
        setFailedMessage(message);
        setError(err instanceof Error ? err.message : 'CouchMate missed that one. Try again.');
      })
      .finally(() => setSending(false));
  };

  const bannerSource: BannerSource | null = serverContext ?? context ?? null;

  return (
    <div className="buddy-chat">
      {status === 'signed-out' && <p className="buddy-note">Sign in to chat with CouchMate.</p>}
      {status === 'unavailable' && <p className="buddy-note">CouchMate isn't available on this server right now.</p>}

      {status === 'off' && (
        <div className="buddy-enable">
          <p className="buddy-enable-pitch">
            <CouchMateIcon /> Hi, I'm CouchMate — your movie assistant. Chat about what you're watching, find something to play, or ask what to watch next. Always spoiler-safe.
          </p>
          <Button type="button" onClick={enable} disabled={enabling}>
            {enabling ? 'Enabling…' : 'Enable CouchMate'}
          </Button>
          {error && <p className="buddy-error" role="alert">{error}</p>}
        </div>
      )}

      {(status === 'on' || status === 'loading') && (
        <>
          {bannerSource && status === 'on' && <p className="buddy-context-banner">{bannerFor(bannerSource)}</p>}
          <div className="buddy-messages" ref={listRef} role="log" aria-live="polite" aria-label="Buddy conversation">
            {historyLoading && <p className="buddy-note">Loading conversation…</p>}
            {!historyLoading && messages.length === 0 && !sending && status === 'on' && (
              <>
                <p className="buddy-note">
                  {justCleared
                    ? 'Chat cleared — start a fresh one below.'
                    : 'Ask anything — the story so far, a character, what to watch next. Spoiler-safe, always.'}
                </p>
                {!justCleared && (
                  <div className="buddy-suggestions" aria-label="Suggested questions">
                    {SUGGESTIONS.map((suggestion) => (
                      <Button
                        key={suggestion.label}
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="buddy-suggestion"
                        disabled={sending}
                        onClick={() => send(suggestion.prompt)}
                      >
                        {suggestion.label}
                      </Button>
                    ))}
                  </div>
                )}
              </>
            )}
            {messages.map((message, index) => (
              <div key={`${message.t}:${index}`} className={`buddy-message buddy-message--${message.role}`} dir="auto">
                {message.role === 'buddy'
                  ? (
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        // Assistant links stay internal: same-tab, app-relative.
                        a: ({ node, ...props }) => <a {...props} onClick={(e) => { e.preventDefault(); if (props.href) window.location.assign(props.href); }} />,
                      }}
                    >
                      {message.text}
                    </ReactMarkdown>
                  )
                  : message.text}
              </div>
            ))}
            {sending && <p className="buddy-typing" role="status">CouchMate is thinking…</p>}
          </div>
          {error && status === 'on' && (
            <div className="buddy-error" role="alert">
              <span>{error}</span>
              {failedMessage && (
                <Button type="button" variant="outline" size="sm" onClick={() => send(failedMessage)}>
                  Retry
                </Button>
              )}
            </div>
          )}
          <form
            className="buddy-input-row"
            onSubmit={(event) => {
              event.preventDefault();
              send(draft);
            }}
          >
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={context ? 'Ask about this title…' : "Ask CouchMate — 'what should I watch?'…"}
              disabled={sending || status === 'loading'}
              aria-label="Message CouchMate"
              maxLength={2000}
            />
            {status === 'on' && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="buddy-clear"
                onClick={clearChat}
                disabled={clearing || sending || messages.length === 0}
              >
                {clearing ? 'Clearing…' : 'Clear'}
              </Button>
            )}
            <Button type="submit" disabled={sending || !draft.trim()}>Send</Button>
          </form>
        </>
      )}
    </div>
  );
}
