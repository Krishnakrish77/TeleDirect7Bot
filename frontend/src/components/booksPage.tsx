import { type PointerEvent as ReactPointerEvent, type ReactNode, useEffect, useRef, useState } from 'react';
import { fetchBookProgress, fetchBookReaderData, fetchBooks, saveBookProgress, saveBookReaderData } from '../api';
import { BookOpenIcon, DownloadIcon, PauseIcon, SearchIcon, VolumeIcon } from '../icons';
import type { BookItem, BookProgressMap } from '../types';
import { Button } from './ui/button';

declare global { interface Window { ePub?: (source: string) => EpubBook; pdfjsLib?: PdfJs; } }
type EpubLocation = { start?: { cfi?: string; percentage?: number } };
type EpubTocItem = { label: string; href: string; subitems?: EpubTocItem[] };
type EpubRendition = { display: (target?: string | number) => Promise<unknown>; next: () => Promise<unknown>; prev: () => Promise<unknown>; on?: (event: string, callback: (location: EpubLocation) => void) => void; getContents?: () => Array<{ document?: Document }>; destroy?: () => void; };
type EpubBook = { renderTo: (element: HTMLElement, options: Record<string, unknown>) => EpubRendition; loaded?: { navigation?: Promise<{ toc?: EpubTocItem[] }> }; destroy?: () => void; };
type PdfViewport = { width: number; height: number };
type PdfPage = { getViewport: (params: { scale: number }) => PdfViewport; render: (params: { canvasContext: CanvasRenderingContext2D; viewport: PdfViewport }) => { promise: Promise<unknown> } };
type PdfDocument = { numPages: number; getPage: (page: number) => Promise<PdfPage>; destroy?: () => void };
type PdfJs = { GlobalWorkerOptions: { workerSrc: string }; getDocument: (url: string) => { promise: Promise<PdfDocument>; destroy?: () => void } };
const EPUB_SCRIPT = 'https://cdn.jsdelivr.net/npm/epubjs@0.3.93/dist/epub.min.js';
const PDFJS_SCRIPT = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js';
const PDFJS_WORKER = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
const PROGRESS_KEY = 'td:book-progress';
const BOOKMARKS_KEY = 'td:book-bookmarks';
type BookBookmark = { locator: string; label: string; progress: number; t: number };
const NOTES_KEY = 'td:book-notes';
type BookNote = { text: string; progress: number; t: number };

function localProgress(): BookProgressMap { try { return JSON.parse(localStorage.getItem(PROGRESS_KEY) || '{}') || {}; } catch (_) { return {}; } }
function writeProgress(value: BookProgressMap) { try { localStorage.setItem(PROGRESS_KEY, JSON.stringify(value)); } catch (_) {} }
function localBookmarks(): Record<string, BookBookmark[]> { try { return JSON.parse(localStorage.getItem(BOOKMARKS_KEY) || '{}') || {}; } catch (_) { return {}; } }
function writeBookmarks(value: Record<string, BookBookmark[]>) { try { localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(value)); } catch (_) {} }
function localNotes(): Record<string, BookNote[]> { try { return JSON.parse(localStorage.getItem(NOTES_KEY) || '{}') || {}; } catch (_) { return {}; } }
function writeNotes(value: Record<string, BookNote[]>) { try { localStorage.setItem(NOTES_KEY, JSON.stringify(value)); } catch (_) {} }
function loadEpubReader(): Promise<void> {
  if (window.ePub) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement('script'); script.src = EPUB_SCRIPT; script.async = true;
    script.onload = () => window.ePub ? resolve() : reject(new Error('The EPUB reader did not start.'));
    script.onerror = () => reject(new Error('Unable to load the EPUB reader. Check your connection and try again.'));
    document.head.appendChild(script);
  });
}
function loadPdfReader(): Promise<void> {
  if (window.pdfjsLib) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement('script'); script.src = PDFJS_SCRIPT; script.async = true;
    script.onload = () => window.pdfjsLib ? resolve() : reject(new Error('The PDF reader did not start.'));
    script.onerror = () => reject(new Error('Unable to load the PDF reader. Check your connection and try again.'));
    document.head.appendChild(script);
  });
}

export function BooksPage() {
  const [items, setItems] = useState<BookItem[]>([]); const [query, setQuery] = useState(''); const [selected, setSelected] = useState<BookItem | null>(null);
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [readerError, setReaderError] = useState(''); const [speaking, setSpeaking] = useState(false);
  const [progress, setProgress] = useState<BookProgressMap>(() => localProgress());
  const [bookmarks, setBookmarks] = useState<Record<string, BookBookmark[]>>(() => localBookmarks()); const [notes, setNotes] = useState<Record<string, BookNote[]>>(() => localNotes()); const [toc, setToc] = useState<EpubTocItem[]>([]); const [readerPanel, setReaderPanel] = useState<'contents' | 'bookmarks' | 'notes' | null>(null); const [findQuery, setFindQuery] = useState(''); const [findStatus, setFindStatus] = useState(''); const [speechRate, setSpeechRate] = useState(1); const [sessionMinutes, setSessionMinutes] = useState(0); const [pdfPage, setPdfPage] = useState(1); const [pdfDocument, setPdfDocument] = useState<PdfDocument | null>(null); const [pdfPages, setPdfPages] = useState(0); const [controlsVisible, setControlsVisible] = useState(true);
  const epubRootRef = useRef<HTMLDivElement>(null); const renditionRef = useRef<EpubRendition | null>(null); const pdfCanvasRef = useRef<HTMLCanvasElement>(null); const gestureStart = useRef<{ x: number; y: number } | null>(null);
  const isEpub = selected?.format.toLowerCase() === 'epub';

  useEffect(() => { const controller = new AbortController(); setLoading(true); void fetchBooks(query, controller.signal).then((data) => setItems(data.items)).catch((err) => { if (err.name !== 'AbortError') setError(err.message || 'Unable to load books.'); }).finally(() => setLoading(false)); return () => controller.abort(); }, [query]);
  useEffect(() => { void fetchBookProgress().then((server) => { const local = localProgress(); const merged: BookProgressMap = { ...local }; Object.entries(server).forEach(([bookId, value]) => { if (!merged[bookId] || value.t > merged[bookId].t) merged[bookId] = value; }); writeProgress(merged); setProgress(merged); }).catch(() => undefined); }, []);
  useEffect(() => { if (!selected) return; let cancelled = false; void fetchBookReaderData(selected.id).then((data) => { if (cancelled) return; if (data.bookmarks.length) setBookmarks((current) => { const next = { ...current, [selected.id]: data.bookmarks }; writeBookmarks(next); return next; }); if (data.notes.length) setNotes((current) => { const next = { ...current, [selected.id]: data.notes }; writeNotes(next); return next; }); }).catch(() => undefined); return () => { cancelled = true; }; }, [selected]);
  useEffect(() => { document.body.classList.toggle('books-reading-mode', Boolean(selected)); return () => document.body.classList.remove('books-reading-mode'); }, [selected]);
  useEffect(() => { document.body.classList.toggle('books-reader-controls-visible', Boolean(selected && controlsVisible)); return () => document.body.classList.remove('books-reader-controls-visible'); }, [selected, controlsVisible]);
  useEffect(() => { if (!selected) return undefined; const started = Date.now(); const timer = window.setInterval(() => setSessionMinutes(Math.floor((Date.now() - started) / 60000)), 30_000); setSessionMinutes(0); return () => window.clearInterval(timer); }, [selected]);
  useEffect(() => { if (!selected || !controlsVisible) return undefined; const timer = window.setTimeout(() => setControlsVisible(false), 3500); return () => window.clearTimeout(timer); }, [selected, controlsVisible]);
  useEffect(() => { if (!selected || isEpub) return; const page = Number((progress[selected.id]?.locator || '').replace(/^page:/, '')); setPdfPage(Number.isFinite(page) && page > 0 ? page : 1); }, [isEpub, selected]);
  useEffect(() => () => window.speechSynthesis?.cancel(), []);

  const saveProgress = (book: BookItem, locator: string, fraction: number) => {
    const value = { locator, progress: Math.max(0, Math.min(1, fraction || 0)), t: Date.now() };
    setProgress((current) => { const next = { ...current, [book.id]: value }; writeProgress(next); return next; });
    void saveBookProgress(book.id, value).catch(() => undefined);
  };
  useEffect(() => {
    if (!selected || !isEpub || !epubRootRef.current) return undefined;
    let cancelled = false; let book: EpubBook | null = null; setReaderError(''); setToc([]); setReaderPanel(null); setFindStatus('');
    void loadEpubReader().then(() => {
      if (cancelled || !window.ePub || !epubRootRef.current) return;
      epubRootRef.current.replaceChildren(); book = window.ePub(selected.readUrl);
      void book.loaded?.navigation?.then((navigation) => { if (!cancelled) setToc(navigation.toc || []); });
      const rendition = book.renderTo(epubRootRef.current, { width: '100%', height: '100%', spread: 'none' }); renditionRef.current = rendition;
      rendition.on?.('relocated', (location) => { const start = location.start; if (start?.cfi) saveProgress(selected, start.cfi, start.percentage || 0); });
      return rendition.display(progress[selected.id]?.locator || undefined);
    }).catch((err: unknown) => { if (!cancelled) setReaderError(err instanceof Error ? err.message : 'This EPUB could not be opened.'); });
    return () => { cancelled = true; renditionRef.current?.destroy?.(); renditionRef.current = null; book?.destroy?.(); };
  }, [isEpub, selected]);
  useEffect(() => {
    if (!selected || isEpub) return undefined;
    let cancelled = false; let task: { promise: Promise<PdfDocument>; destroy?: () => void } | null = null; setPdfDocument(null); setPdfPages(0); setReaderError('');
    void loadPdfReader().then(async () => {
      if (!window.pdfjsLib || cancelled) return;
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
      task = window.pdfjsLib.getDocument(selected.readUrl);
      const document = await task.promise;
      if (cancelled) { document.destroy?.(); return; }
      setPdfPages(document.numPages); setPdfDocument(document);
      const restored = Number((progress[selected.id]?.locator || '').replace(/^page:/, ''));
      if (restored > 0) setPdfPage(Math.min(restored, document.numPages));
    }).catch((err: unknown) => { if (!cancelled) setReaderError(err instanceof Error ? err.message : 'This PDF could not be opened.'); });
    return () => { cancelled = true; task?.destroy?.(); };
  }, [isEpub, selected]);
  useEffect(() => {
    if (!pdfDocument || !pdfCanvasRef.current) return;
    let cancelled = false;
    void pdfDocument.getPage(Math.max(1, Math.min(pdfPage, pdfDocument.numPages))).then((page) => {
      if (cancelled || !pdfCanvasRef.current) return;
      const viewport = page.getViewport({ scale: 1.5 }); const canvas = pdfCanvasRef.current; const context = canvas.getContext('2d');
      if (!context) return;
      canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height);
      return page.render({ canvasContext: context, viewport }).promise;
    }).catch((err: unknown) => { if (!cancelled) setReaderError(err instanceof Error ? err.message : 'Could not render this PDF page.'); });
    return () => { cancelled = true; };
  }, [pdfDocument, pdfPage]);

  const speak = () => { if (!('speechSynthesis' in window)) { setReaderError('Read aloud is not available in this browser.'); return; } if (speaking) { window.speechSynthesis.cancel(); setSpeaking(false); return; } const selectedText = window.getSelection()?.toString().trim() || ''; const epubText = renditionRef.current?.getContents?.().map((content) => content.document?.body?.innerText || '').join('\n').trim() || ''; const source = selectedText || epubText; if (!source) { setReaderError('Select text in the book first. EPUB chapters can also be read aloud.'); return; } const utterance = new SpeechSynthesisUtterance(source.slice(0, 12000)); utterance.rate = speechRate; utterance.onend = utterance.onerror = () => setSpeaking(false); setSpeaking(true); window.speechSynthesis.speak(utterance); };
  const closeReader = () => { window.speechSynthesis?.cancel(); setSpeaking(false); setSelected(null); };
  const changePdfPage = (page: number) => { if (!selected) return; const next = Math.max(1, Math.min(pdfPages || Number.MAX_SAFE_INTEGER, Math.floor(page))); setPdfPage(next); saveProgress(selected, `page:${next}`, pdfPages ? next / pdfPages : 0); };
  const turnPage = (direction: 1 | -1) => { setControlsVisible(true); if (isEpub) void (direction > 0 ? renditionRef.current?.next() : renditionRef.current?.prev()); else changePdfPage(pdfPage + direction); };
  useEffect(() => { if (!selected) return undefined; const onKey = (event: KeyboardEvent) => { if ((event.target as HTMLElement)?.tagName === 'INPUT') return; if (event.key === 'ArrowLeft') { event.preventDefault(); turnPage(-1); } if (event.key === 'ArrowRight') { event.preventDefault(); turnPage(1); } if (event.key === 'Escape') setControlsVisible(false); }; window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey); }, [selected, isEpub, pdfPage]);
  const onReaderPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => { gestureStart.current = { x: event.clientX, y: event.clientY }; };
  const onReaderPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => { const start = gestureStart.current; gestureStart.current = null; if (!start) return; const dx = event.clientX - start.x; if (Math.abs(dx) > 52) { turnPage(dx < 0 ? 1 : -1); return; } if (Math.abs(event.clientY - start.y) > 28) return; const width = event.currentTarget.clientWidth; if (!controlsVisible && event.clientX < width * .25) turnPage(-1); else if (!controlsVisible && event.clientX > width * .75) turnPage(1); else setControlsVisible((visible) => !visible); };
  useEffect(() => { if (!selected) return undefined; const down = (event: PointerEvent) => { gestureStart.current = { x: event.clientX, y: event.clientY }; }; const up = (event: PointerEvent) => { const start = gestureStart.current; gestureStart.current = null; if (!start || controlsVisible) return; const dx = event.clientX - start.x; if (Math.abs(dx) > 52) { turnPage(dx < 0 ? 1 : -1); return; } if (Math.abs(event.clientY - start.y) > 28) return; if (event.clientX < window.innerWidth * .25) turnPage(-1); else if (event.clientX > window.innerWidth * .75) turnPage(1); else setControlsVisible(true); }; window.addEventListener('pointerdown', down); window.addEventListener('pointerup', up); return () => { window.removeEventListener('pointerdown', down); window.removeEventListener('pointerup', up); }; }, [selected, controlsVisible, isEpub, pdfPage]);
  const addBookmark = () => { if (!selected || !isEpub) return; const current = progress[selected.id]; if (!current?.locator) { setReaderError('Open a chapter before saving a bookmark.'); return; } const bookEntries = [{ locator: current.locator, label: `${Math.round(current.progress * 100)}% through`, progress: current.progress, t: Date.now() }, ...(bookmarks[selected.id] || []).filter((entry) => entry.locator !== current.locator)].slice(0, 30); const next = { ...bookmarks, [selected.id]: bookEntries }; setBookmarks(next); writeBookmarks(next); void saveBookReaderData(selected.id, { bookmarks: bookEntries, notes: notes[selected.id] || [] }).catch(() => undefined); setReaderPanel('bookmarks'); };
  const addNote = () => { if (!selected || !isEpub) return; const text = window.getSelection()?.toString().trim() || renditionRef.current?.getContents?.().map((entry) => entry.document?.getSelection()?.toString().trim() || '').find(Boolean) || ''; if (!text) { setReaderError('Select text in the open chapter before adding a note.'); return; } const noteEntries = [{ text: text.slice(0, 1200), progress: progress[selected.id]?.progress || 0, t: Date.now() }, ...(notes[selected.id] || [])].slice(0, 50); const next = { ...notes, [selected.id]: noteEntries }; setNotes(next); writeNotes(next); void saveBookReaderData(selected.id, { bookmarks: bookmarks[selected.id] || [], notes: noteEntries }).catch(() => undefined); setReaderPanel('notes'); };
  const findInBook = () => { const query = findQuery.trim().toLowerCase(); if (!query) { setFindStatus('Enter text to find.'); return; } const docs = renditionRef.current?.getContents?.() || []; const doc = docs.map((entry) => entry.document).find((entry) => (entry?.body?.innerText || '').toLowerCase().includes(query)); if (!doc) { setFindStatus('No match in the open chapter. Move chapters and try again.'); return; } const node = Array.from(doc.body.querySelectorAll('*')).find((entry) => (entry.textContent || '').toLowerCase().includes(query)); (node as HTMLElement | undefined)?.scrollIntoView({ block: 'center' }); setFindStatus('Match found in the open chapter.'); };
  const renderToc = (entries: EpubTocItem[], depth = 0): ReactNode[] => entries.flatMap((entry) => [<button key={`${depth}-${entry.href}`} type="button" className="books-reader-panel-link" style={{ paddingLeft: `${0.7 + depth * 0.8}rem` }} onClick={() => { void renditionRef.current?.display(entry.href); setReaderPanel(null); }}>{entry.label}</button>, ...renderToc(entry.subitems || [], depth + 1)]);

  if (selected) return <main className="books-reader-page"><section className="books-reader-shell"><header className="books-reader-toolbar"><Button variant="ghost" size="sm" onClick={closeReader}>← Library</Button><div className="books-file-label"><BookOpenIcon /><span><strong>{selected.title}</strong><small>{selected.fileName} · {selected.fileSizeLabel}</small></span></div><div className="books-reader-actions">{isEpub ? <><Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'contents' ? null : 'contents')}>Contents</Button><Button variant="secondary" size="sm" onClick={addBookmark}>Bookmark</Button><Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'bookmarks' ? null : 'bookmarks')}>Bookmarks</Button><Button variant="secondary" size="sm" onClick={addNote}>Add note</Button><Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'notes' ? null : 'notes')}>Notes</Button><Button variant="secondary" size="sm" onClick={() => void renditionRef.current?.prev()}>Previous</Button><Button variant="secondary" size="sm" onClick={() => void renditionRef.current?.next()}>Next</Button><label className="books-speech-rate">Speed<select value={speechRate} onChange={(event) => setSpeechRate(Number(event.currentTarget.value))}><option value={0.8}>0.8×</option><option value={1}>1×</option><option value={1.25}>1.25×</option><option value={1.5}>1.5×</option></select></label><Button variant="secondary" size="sm" onClick={speak}>{speaking ? <PauseIcon /> : <VolumeIcon />}{speaking ? 'Stop reading' : 'Listen'}</Button></> : <><Button variant="secondary" size="sm" onClick={() => changePdfPage(pdfPage - 1)} disabled={pdfPage <= 1}>Previous page</Button><label className="books-speech-rate">Page<input type="number" min="1" max={pdfPages || undefined} value={pdfPage} onChange={(event) => changePdfPage(Number(event.currentTarget.value))} /></label><Button variant="secondary" size="sm" onClick={() => changePdfPage(pdfPage + 1)} disabled={Boolean(pdfPages && pdfPage >= pdfPages)}>Next page</Button></>}<a href={selected.downloadUrl}><Button variant="secondary" size="sm"><DownloadIcon /> Download</Button></a></div></header>{readerError && <p className="books-reader-error" role="alert">{readerError}</p>}{isEpub && <div className="books-reader-find"><input value={findQuery} onChange={(event) => setFindQuery(event.currentTarget.value)} onKeyDown={(event) => { if (event.key === 'Enter') findInBook(); }} placeholder="Find in open chapter" /><Button variant="secondary" size="sm" onClick={findInBook}>Find</Button>{findStatus && <small>{findStatus}</small>}</div>}{readerPanel && isEpub && <aside className="books-reader-panel"><strong>{readerPanel === 'contents' ? 'Contents' : readerPanel === 'bookmarks' ? 'Bookmarks' : 'Notes'}</strong>{readerPanel === 'contents' ? (toc.length ? renderToc(toc) : <small>Contents unavailable for this EPUB.</small>) : readerPanel === 'bookmarks' ? ((bookmarks[selected.id] || []).length ? (bookmarks[selected.id] || []).map((entry) => <button key={entry.t} type="button" className="books-reader-panel-link" onClick={() => { void renditionRef.current?.display(entry.locator); setReaderPanel(null); }}>{entry.label}</button>) : <small>No bookmarks yet.</small>) : ((notes[selected.id] || []).length ? (notes[selected.id] || []).map((entry) => <p key={entry.t} className="books-reader-note">{entry.text}</p>) : <small>Select text and choose Add note to save a highlight.</small>)}</aside>}<div className="books-reader">{isEpub ? <div ref={epubRootRef} className="epub-reader" aria-label={`${selected.title} reader`} /> : <div className="pdf-reader"><canvas ref={pdfCanvasRef} aria-label={`${selected.title} PDF page ${pdfPage}`} /></div>}</div><footer className="books-reader-footer"><span>{isEpub && progress[selected.id]?.progress ? `${Math.round(progress[selected.id].progress * 100)}% read` : isEpub ? 'Starting chapter' : `Page ${pdfPage}${pdfPages ? ` of ${pdfPages}` : ''} saved`}{sessionMinutes ? ` · ${sessionMinutes} min this session` : ''}</span><span>{isEpub ? 'Your place, bookmarks, and notes sync to your library account.' : 'PDF page location syncs to your library account.'}</span></footer></section></main>;
  return <main className="hub-main books-page"><section className="books-hero"><div><p className="eyebrow">Library books</p><h1>Your reading library.</h1><p>Browse and read available PDFs and EPUBs from one comfortable place.</p></div></section><label className="books-search"><SearchIcon /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search books" /></label>{loading ? <p className="books-state">Loading books…</p> : error ? <p className="books-reader-error">{error}</p> : items.length ? <div className="books-grid">{items.map((book) => <article className="book-card" key={book.id}>{book.coverUrl ? <img className="book-cover book-cover-image" src={book.coverUrl} alt="" /> : <span className="book-cover"><BookOpenIcon /><small>{book.format}</small></span>}<div><p className="eyebrow">{book.format} · {book.fileSizeLabel}{progress[book.id]?.progress ? ` · ${Math.round(progress[book.id].progress * 100)}% read` : ''}</p><h2>{book.title}</h2>{book.authors.length > 0 && <p className="book-author">{book.authors.join(', ')}</p>}<p>{book.description || book.fileName}</p><Button size="sm" onClick={() => setSelected(book)}>{progress[book.id]?.progress ? 'Resume reading' : 'Read book'}</Button></div></article>)}</div> : <section className="books-dropzone books-empty"><BookOpenIcon /><strong>No books in your library yet</strong><span>Check back soon for new titles to read.</span></section>}</main>;
}
