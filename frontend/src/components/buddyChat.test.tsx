import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, deleteBuddyHistory, fetchBuddyHistory, fetchBuddyPrefs, sendBuddyMessage, setBuddyEnabled } from '../api';
import type * as ApiModule from '../api';
import type { BuddyChatContext } from '../types';
import { BuddyChat } from './buddyChat';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof ApiModule>();
  return {
    ...actual,
    fetchBuddyPrefs: vi.fn(),
    setBuddyEnabled: vi.fn(),
    fetchBuddyHistory: vi.fn(),
    sendBuddyMessage: vi.fn(),
    deleteBuddyHistory: vi.fn(),
  };
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const episodeContext: BuddyChatContext = {
  itemId: 'item-1',
  messageId: 42,
  title: 'The Wire',
  kind: 'tv',
  seriesTitle: 'The Wire',
  season: 3,
  episode: 5,
};

describe('BuddyChat', () => {
  it('enables the opt-in flag from the empty state and then loads history', async () => {
    vi.mocked(fetchBuddyPrefs).mockResolvedValue({ enabled: false });
    vi.mocked(setBuddyEnabled).mockResolvedValue({ enabled: true });
    vi.mocked(fetchBuddyHistory).mockResolvedValue({ messages: [] });
    render(<BuddyChat />);

    const enableButton = await screen.findByRole('button', { name: 'Enable CouchMate' });
    fireEvent.click(enableButton);

    await waitFor(() => expect(setBuddyEnabled).toHaveBeenCalledWith(true));
    await waitFor(() => expect(fetchBuddyHistory).toHaveBeenCalled());
    expect(await screen.findByLabelText('Message CouchMate')).toBeTruthy();
  });

  it('shows the spoiler-safe context banner and sends messages with the item reference', async () => {
    vi.mocked(fetchBuddyPrefs).mockResolvedValue({ enabled: true });
    vi.mocked(fetchBuddyHistory).mockResolvedValue({ messages: [] });
    vi.mocked(sendBuddyMessage).mockResolvedValue({
      reply: 'Stringer is running the co-op meeting this season.',
      context: {
        title: 'The Wire', kind: 'tv', seriesTitle: 'The Wire', season: 3, episode: 5,
        completed: false, cutoffLabel: 'S03E05',
      },
    });
    render(<BuddyChat context={episodeContext} />);

    expect(await screen.findByText('Chatting about The Wire S03E05 — no spoilers beyond this episode')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Message CouchMate'), { target: { value: 'Who is Stringer?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(sendBuddyMessage).toHaveBeenCalledWith({ message: 'Who is Stringer?', itemId: 'item-1', messageId: 42 }));
    expect(await screen.findByText('Stringer is running the co-op meeting this season.')).toBeTruthy();
    // The user's own message stays visible ahead of the reply.
    expect(screen.getByText('Who is Stringer?')).toBeTruthy();
  });

  it('shows a sign-in hint when prefs return 401', async () => {
    vi.mocked(fetchBuddyPrefs).mockRejectedValue(new ApiError('Unauthorized', 401));
    render(<BuddyChat />);

    expect(await screen.findByText('Sign in to chat with CouchMate.')).toBeTruthy();
    expect(screen.queryByLabelText('Message CouchMate')).toBeNull();
  });

  it('shows an unavailable note when prefs fail for a non-auth reason', async () => {
    vi.mocked(fetchBuddyPrefs).mockRejectedValue(new ApiError('Not Found', 404));
    render(<BuddyChat />);

    expect(await screen.findByText("CouchMate isn't available on this server right now.")).toBeTruthy();
    expect(screen.queryByLabelText('Message CouchMate')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Enable CouchMate' })).toBeNull();
  });

  it('shows watch progress in the banner and offers suggestion chips in the empty state', async () => {
    vi.mocked(fetchBuddyPrefs).mockResolvedValue({ enabled: true });
    vi.mocked(fetchBuddyHistory).mockResolvedValue({ messages: [] });
    vi.mocked(sendBuddyMessage).mockResolvedValue({
      reply: "You're on S03E05, about 40% through.",
      context: {
        title: 'The Wire', kind: 'tv', seriesTitle: 'The Wire', season: 3, episode: 5,
        completed: false, cutoffLabel: 'S03E05', progress: 0.4,
      },
    });
    render(<BuddyChat context={episodeContext} />);

    // Empty state suggests one-tap openers; tapping one sends its prompt.
    fireEvent.click(await screen.findByRole('button', { name: 'Where am I?' }));
    await waitFor(() => expect(sendBuddyMessage).toHaveBeenCalledWith({
      message: 'What episode am I on, and how far through it am I?',
      itemId: 'item-1', messageId: 42,
    }));

    // The server-derived context (now including progress) updates the banner.
    expect(await screen.findByText('Chatting about The Wire S03E05 · 40% through — no spoilers beyond this episode')).toBeTruthy();
  });

  it('surfaces a failed enable (e.g. 503 when Mongo is down) as a retry-able inline error', async () => {
    vi.mocked(fetchBuddyPrefs).mockResolvedValue({ enabled: false });
    vi.mocked(setBuddyEnabled).mockRejectedValueOnce(new ApiError('Buddy preferences need the database. Try again shortly.', 503));
    render(<BuddyChat />);

    const enableButton = await screen.findByRole('button', { name: 'Enable CouchMate' });
    fireEvent.click(enableButton);

    expect((await screen.findByRole('alert')).textContent).toContain('Buddy preferences need the database. Try again shortly.');
    // The opt-in state is preserved: the button is back and can be retried.
    expect(screen.getByRole('button', { name: 'Enable CouchMate' })).toBeTruthy();
    expect(fetchBuddyHistory).not.toHaveBeenCalled();

    vi.mocked(setBuddyEnabled).mockResolvedValueOnce({ enabled: true });
    vi.mocked(fetchBuddyHistory).mockResolvedValue({ messages: [] });
    fireEvent.click(screen.getByRole('button', { name: 'Enable CouchMate' }));
    expect(await screen.findByLabelText('Message CouchMate')).toBeTruthy();
  });

  it('keeps a failed message retryable and restores it to the input', async () => {
    vi.mocked(fetchBuddyPrefs).mockResolvedValue({ enabled: true });
    vi.mocked(fetchBuddyHistory).mockResolvedValue({ messages: [] });
    vi.mocked(sendBuddyMessage).mockRejectedValueOnce(new ApiError('Buddy is thinking too hard.', 502));
    render(<BuddyChat context={episodeContext} />);

    const input = await screen.findByLabelText('Message CouchMate');
    fireEvent.change(input, { target: { value: 'Is this safe to ask?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByRole('alert')).toBeTruthy();
    // The draft is restored and the optimistic bubble rolled back.
    expect((input as HTMLInputElement).value).toBe('Is this safe to ask?');
    expect(screen.queryByText('Is this safe to ask?', { selector: '.buddy-message' })).toBeNull();

    vi.mocked(sendBuddyMessage).mockResolvedValueOnce({ reply: 'Yes, ask away.', context: null });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(sendBuddyMessage).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Yes, ask away.')).toBeTruthy();
  });

  it('clears the context session after confirmation and empties the list', async () => {
    vi.mocked(fetchBuddyPrefs).mockResolvedValue({ enabled: true });
    vi.mocked(fetchBuddyHistory).mockResolvedValue({
      messages: [
        { role: 'user', text: 'Who is Avon?', t: 100 },
        { role: 'buddy', text: 'A Baltimore gangster.', t: 101 },
      ],
    });
    vi.mocked(deleteBuddyHistory).mockResolvedValue({ ok: true });
    const confirmMock = vi.fn().mockReturnValue(true);
    vi.stubGlobal('confirm', confirmMock);
    render(<BuddyChat context={episodeContext} />);

    expect(await screen.findByText('Who is Avon?')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));

    await waitFor(() => expect(deleteBuddyHistory).toHaveBeenCalledWith({ itemId: 'item-1', messageId: 42 }));
    expect(confirmMock).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText('Who is Avon?')).toBeNull());
    expect(await screen.findByText('Chat cleared — start a fresh one below.')).toBeTruthy();
  });

  it('clears all sessions from the context-free panel chat', async () => {
    vi.mocked(fetchBuddyPrefs).mockResolvedValue({ enabled: true });
    vi.mocked(fetchBuddyHistory).mockResolvedValue({ messages: [{ role: 'user', text: 'Hi', t: 100 }] });
    vi.mocked(deleteBuddyHistory).mockResolvedValue({ ok: true });
    const confirmMock = vi.fn().mockReturnValue(true);
    vi.stubGlobal('confirm', confirmMock);
    render(<BuddyChat />);

    expect(await screen.findByText('Hi')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));

    await waitFor(() => expect(deleteBuddyHistory).toHaveBeenCalledWith(undefined));
    expect(confirmMock.mock.calls[0][0]).toContain('all CouchMate conversations');
    await waitFor(() => expect(screen.queryByText('Hi')).toBeNull());
  });

  it('keeps the conversation and shows an error when clearing fails', async () => {
    vi.mocked(fetchBuddyPrefs).mockResolvedValue({ enabled: true });
    vi.mocked(fetchBuddyHistory).mockResolvedValue({ messages: [{ role: 'user', text: 'Keep me', t: 100 }] });
    vi.mocked(deleteBuddyHistory).mockRejectedValue(new ApiError('Could not clear right now.', 502));
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(true));
    render(<BuddyChat context={episodeContext} />);

    expect(await screen.findByText('Keep me')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Could not clear right now.');
    expect(screen.getByText('Keep me')).toBeTruthy();
  });

  it('does not call the API when the clear is not confirmed', async () => {
    vi.mocked(fetchBuddyPrefs).mockResolvedValue({ enabled: true });
    vi.mocked(fetchBuddyHistory).mockResolvedValue({ messages: [{ role: 'user', text: 'Still here', t: 100 }] });
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(false));
    render(<BuddyChat context={episodeContext} />);

    expect(await screen.findByText('Still here')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));

    expect(deleteBuddyHistory).not.toHaveBeenCalled();
    expect(screen.getByText('Still here')).toBeTruthy();
  });
});
