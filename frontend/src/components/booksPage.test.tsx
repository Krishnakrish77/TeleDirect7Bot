import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { BookItem } from '../types';
import { BooksPage } from './booksPage';

const apiMocks = vi.hoisted(() => ({ fetchBooks: vi.fn() }));
let themes: { register: ReturnType<typeof vi.fn>; select: ReturnType<typeof vi.fn> };

vi.mock('../api', () => ({
  fetchBooks: apiMocks.fetchBooks,
  fetchBookProgress: vi.fn().mockResolvedValue({}),
  fetchBookReaderData: vi.fn().mockResolvedValue({ bookmarks: [], notes: [] }),
  saveBookProgress: vi.fn().mockResolvedValue(undefined),
  saveBookReaderData: vi.fn().mockResolvedValue(undefined),
}));

const epub: BookItem = {
  id: 'book-1', title: 'Example Book', fileName: 'example.epub', format: 'EPUB', fileSize: 1024, fileSizeLabel: '1 MiB', description: '', authors: ['Example Author'], coverUrl: '', publisher: '', language: 'en', pageCount: 0, readUrl: '/book/example/content', downloadUrl: '/book/example/content?download=1',
};
const pdf: BookItem = { ...epub, id: 'book-2', title: 'Example PDF', fileName: 'example.pdf', format: 'PDF', readUrl: '/book/example-pdf/content' };

function standardEpubHeader() {
  const bytes = new Uint8Array(38); const view = new DataView(bytes.buffer);
  view.setUint32(0, 0x04034b50, true); view.setUint16(8, 0, true); view.setUint16(26, 8, true);
  bytes.set(new TextEncoder().encode('mimetype'), 30);
  return bytes;
}

describe('BooksPage EPUB reading settings', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/books');
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as CanvasRenderingContext2D);
    themes = { register: vi.fn(), select: vi.fn() };
    const rendition = { display: vi.fn().mockResolvedValue(undefined), next: vi.fn(), prev: vi.fn(), themes };
    const ePub = vi.fn(() => ({ renderTo: vi.fn(() => rendition), loaded: { navigation: Promise.resolve({ toc: [] }) }, destroy: vi.fn() }));
    Object.assign(window, { ePub, JSZip: {} });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(standardEpubHeader())));
    apiMocks.fetchBooks.mockResolvedValue({ items: [epub] });
  });

  it('applies and persists EPUB reading preferences', async () => {
    render(<BooksPage user={null} />);
    fireEvent.click(await screen.findByRole('button', { name: /Example Book/i }));
    await screen.findByLabelText('Example Book reader');
    fireEvent.click(screen.getByRole('button', { name: 'Open reading settings' }));

    fireEvent.click(screen.getByRole('button', { name: 'Keep controls visible' }));
    expect(screen.getByRole('button', { name: 'Controls stay visible' }).getAttribute('aria-pressed')).toBe('true');

    fireEvent.click(screen.getByRole('button', { name: 'Sepia' }));
    fireEvent.click(screen.getByRole('button', { name: 'Increase text size' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sans' }));
    fireEvent.click(screen.getByRole('button', { name: 'Compact' }));
    fireEvent.click(screen.getByRole('button', { name: 'Narrow margins' }));

    await waitFor(() => {
      expect(JSON.parse(localStorage.getItem('td:epub-preferences') || '{}')).toEqual({ theme: 'sepia', fontSize: 105, fontFamily: 'sans', lineHeight: 'compact', margins: 'narrow' });
      expect(themes.select).toHaveBeenLastCalledWith('teledirect-sepia-105-sans-compact-narrow');
    });
  });

  it('continues PDF read aloud on the next page', async () => {
    const page = (text: string) => ({ getViewport: vi.fn(() => ({ width: 400, height: 600 })), getTextContent: vi.fn().mockResolvedValue({ items: [{ str: text }] }), render: vi.fn(() => ({ promise: Promise.resolve() })) });
    const document = { numPages: 2, getPage: vi.fn((number: number) => Promise.resolve(page(number === 1 ? 'First page.' : 'Second page.'))) };
    const speak = vi.fn();
    class Utterance { text: string; rate = 1; onend: (() => void) | null = null; onerror: ((event: { error: string }) => void) | null = null; constructor(text: string) { this.text = text; } }
    Object.assign(window, { pdfjsLib: { GlobalWorkerOptions: {}, getDocument: vi.fn(() => ({ promise: Promise.resolve(document) })) }, speechSynthesis: { speak, cancel: vi.fn(), pause: vi.fn(), resume: vi.fn() }, SpeechSynthesisUtterance: Utterance });
    apiMocks.fetchBooks.mockResolvedValue({ items: [pdf] });

    const view = render(<BooksPage user={null} />);
    fireEvent.click(await screen.findByRole('button', { name: /Example PDF/i }));
    await screen.findByLabelText('Example PDF PDF page 1');
    fireEvent.click(screen.getByRole('button', { name: 'Open reader tools' }));
    fireEvent.click(screen.getByRole('button', { name: 'Read from here' }));
    await waitFor(() => expect(speak).toHaveBeenCalledTimes(1));

    (speak.mock.calls[0][0] as Utterance).onend?.();
    await waitFor(() => {
      expect(speak).toHaveBeenCalledTimes(2);
      expect(view.container.querySelector('canvas')?.getAttribute('aria-label')).toContain('page 2');
    });
  });

  it('opens shared book links and returns to the library URL', async () => {
    window.history.replaceState(null, '', '/books?book=book-1');

    render(<BooksPage user={null} />);
    await screen.findByLabelText('Example Book reader');
    expect(window.location.search).toBe('?book=book-1');

    fireEvent.click(screen.getByRole('button', { name: 'Back to library' }));
    await waitFor(() => expect(screen.queryByLabelText('Example Book reader')).toBeNull());
    expect(window.location.pathname).toBe('/books');
    expect(window.location.search).toBe('');
  });
});
