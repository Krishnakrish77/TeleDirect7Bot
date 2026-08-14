import { type ReactNode, type UIEvent, useEffect, useRef, useState } from 'react';
import { fetchBookProgress, fetchBookReaderData, fetchBooks, saveBookProgress, saveBookReaderData } from '../api';
import { BookOpenIcon, BookmarkIcon, DownloadIcon, MoreVerticalIcon, PauseIcon, SearchIcon, VolumeIcon } from '../icons';
import type { BookItem, BookProgressMap, User } from '../types';
import { Button } from './ui/button';

declare global { interface Window { ePub?: (source: string | ArrayBuffer, options?: { openAs?: 'epub' | 'binary' }) => EpubBook; pdfjsLib?: PdfJs; JSZip?: JsZipStatic; } }
type EpubLocation = { start?: { cfi?: string; percentage?: number } };
type EpubTocItem = { label: string; href: string; subitems?: EpubTocItem[] };
type EpubRendition = { display: (target?: string | number) => Promise<unknown>; next: () => Promise<unknown>; prev: () => Promise<unknown>; on?: (event: string, callback: (location: EpubLocation) => void) => void; getContents?: () => Array<{ document?: Document }>; themes?: { register: (name: string, rules: Record<string, Record<string, string>>) => void; select: (name: string) => void }; destroy?: () => void; };
type EpubBook = { renderTo: (element: HTMLElement, options: Record<string, unknown>) => EpubRendition; loaded?: { navigation?: Promise<{ toc?: EpubTocItem[] }> }; destroy?: () => void; };
type PdfViewport = { width: number; height: number };
type PdfPage = { getViewport: (params: { scale: number }) => PdfViewport; render: (params: { canvasContext: CanvasRenderingContext2D; viewport: PdfViewport }) => { promise: Promise<unknown>; cancel?: () => void } };
type PdfDocument = { numPages: number; getPage: (page: number) => Promise<PdfPage>; destroy?: () => void };
type PdfJs = { GlobalWorkerOptions: { workerSrc: string }; getDocument: (url: string) => { promise: Promise<PdfDocument>; destroy?: () => void } };
type JsZipEntry = { dir: boolean; async: (type: 'uint8array') => Promise<Uint8Array> };
type JsZip = { files: Record<string, JsZipEntry>; file: (name: string, data: Uint8Array, options?: { compression?: 'STORE' | 'DEFLATE'; createFolders?: boolean }) => JsZip; generateAsync: (options: { type: 'arraybuffer'; compression: 'STORE' | 'DEFLATE' }) => Promise<ArrayBuffer> };
type JsZipStatic = { new (): JsZip; loadAsync: (data: ArrayBuffer) => Promise<JsZip> };
const EPUB_SCRIPT = 'https://cdn.jsdelivr.net/npm/epubjs@0.3.93/dist/epub.min.js';
const JSZIP_SCRIPT = 'https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js';
const PDFJS_SCRIPT = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js';
const PDFJS_WORKER = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
const PROGRESS_KEY = 'td:book-progress';
const BOOKMARKS_KEY = 'td:book-bookmarks';
type BookBookmark = { locator: string; label: string; progress: number; t: number };
const NOTES_KEY = 'td:book-notes';
type BookNote = { text: string; progress: number; t: number };
type ReaderTheme = 'light' | 'dark';

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
function loadZipRepair(): Promise<void> {
  if (window.JSZip) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement('script'); script.src = JSZIP_SCRIPT; script.async = true;
    script.onload = () => window.JSZip ? resolve() : reject(new Error('The EPUB compatibility reader did not start.'));
    script.onerror = () => reject(new Error('Unable to load the EPUB compatibility reader. Check your connection and try again.'));
    document.head.appendChild(script);
  });
}
async function epubSource(url: string): Promise<ArrayBuffer> {
  const response = await fetch(url);
  if (!response.ok) throw new Error('Unable to download this EPUB.');
  const original = await response.arrayBuffer();
  // A standards-compliant EPUB already has an uncompressed `mimetype` as its
  // first ZIP entry. Avoid loading JSZip and duplicating the whole book in
  // memory for the common case.
  if (hasStandardEpubMimetype(original)) return original;
  if (!window.JSZip) throw new Error('The EPUB reader dependency did not load. Refresh and try again.');
  const archive = await window.JSZip.loadAsync(original);
  const mimetype = archive.files.mimetype;
  // EPUB readers in the wild are permissive about this, but epub.js is not:
  // rebuild the archive with an uncompressed `mimetype` entry first.
  if (!mimetype || mimetype.dir) return original;
  const repaired = new window.JSZip();
  repaired.file('mimetype', await mimetype.async('uint8array'), { compression: 'STORE' });
  await Promise.all(Object.entries(archive.files).filter(([path, entry]) => path !== 'mimetype' && !entry.dir).map(async ([path, entry]) => {
    repaired.file(path, await entry.async('uint8array'), { compression: 'DEFLATE', createFolders: true });
  }));
  return repaired.generateAsync({ type: 'arraybuffer', compression: 'DEFLATE' });
}
function hasStandardEpubMimetype(archive: ArrayBuffer): boolean {
  const bytes = new Uint8Array(archive);
  if (bytes.length < 30) return false;
  const header = new DataView(archive);
  if (header.getUint32(0, true) !== 0x04034b50 || header.getUint16(8, true) !== 0) return false;
  const nameLength = header.getUint16(26, true);
  return 30 + nameLength <= bytes.length && new TextDecoder().decode(bytes.subarray(30, 30 + nameLength)) === 'mimetype';
}
function applyEpubTheme(rendition: EpubRendition, theme: ReaderTheme) {
  rendition.themes?.register('teledirect-light', { body: { background: '#f8f4e9 !important', color: '#1c1917 !important' }, a: { color: '#9a3412 !important' } });
  rendition.themes?.register('teledirect-dark', { body: { background: '#121416 !important', color: '#f1f5f9 !important' }, a: { color: '#fdba74 !important' } });
  rendition.themes?.select(`teledirect-${theme}`);
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

export function BooksPage({ user }: { user: User | null }) {
  const [items, setItems] = useState<BookItem[]>([]); const [query, setQuery] = useState(''); const [debouncedQuery, setDebouncedQuery] = useState(''); const [selected, setSelected] = useState<BookItem | null>(null);
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [readerError, setReaderError] = useState(''); const [readerLoading, setReaderLoading] = useState(false); const [speaking, setSpeaking] = useState(false); const [readerTheme, setReaderTheme] = useState<ReaderTheme>('light');
  const [progress, setProgress] = useState<BookProgressMap>(() => localProgress());
  const [bookmarks, setBookmarks] = useState<Record<string, BookBookmark[]>>(() => localBookmarks()); const [notes, setNotes] = useState<Record<string, BookNote[]>>(() => localNotes()); const [toc, setToc] = useState<EpubTocItem[]>([]); const [readerPanel, setReaderPanel] = useState<'contents' | 'bookmarks' | 'notes' | null>(null); const [readerMenuOpen, setReaderMenuOpen] = useState(false); const [findQuery, setFindQuery] = useState(''); const [findStatus, setFindStatus] = useState(''); const [speechRate, setSpeechRate] = useState(1); const [sessionMinutes, setSessionMinutes] = useState(0); const [pdfPage, setPdfPage] = useState(1); const [pdfDocument, setPdfDocument] = useState<PdfDocument | null>(null); const [pdfPages, setPdfPages] = useState(0); const [controlsVisible, setControlsVisible] = useState(true);
  const epubRootRef = useRef<HTMLDivElement>(null); const renditionRef = useRef<EpubRendition | null>(null); const pdfCanvasRef = useRef<HTMLCanvasElement>(null); const pdfRootRef = useRef<HTMLDivElement>(null); const gestureStart = useRef<{ x: number; y: number } | null>(null); const pdfScrollTopRef = useRef(0); const pdfPageTurnLockedRef = useRef(false); const pdfPendingScrollRef = useRef<'top' | 'bottom' | null>(null); const touchStartRef = useRef<{ x: number; y: number } | null>(null);
  const isEpub = selected?.format.toLowerCase() === 'epub';

  useEffect(() => { const timer = window.setTimeout(() => setDebouncedQuery(query), 250); return () => window.clearTimeout(timer); }, [query]);
  useEffect(() => { const controller = new AbortController(); let active = true; setLoading(true); void fetchBooks(debouncedQuery, controller.signal).then((data) => { if (active) setItems(data.items); }).catch((err) => { if (active && err.name !== 'AbortError') setError(err.message || 'Unable to load books.'); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; controller.abort(); }; }, [debouncedQuery]);
  useEffect(() => { if (!user) return; void fetchBookProgress().then((server) => { const local = localProgress(); const merged: BookProgressMap = { ...local }; Object.entries(server).forEach(([bookId, value]) => { if (!merged[bookId] || value.t > merged[bookId].t) merged[bookId] = value; }); writeProgress(merged); setProgress(merged); }).catch(() => undefined); }, [user]);
  useEffect(() => { if (!selected || !user) return; let cancelled = false; void fetchBookReaderData(selected.id).then((data) => { if (cancelled) return; if (data.bookmarks.length) setBookmarks((current) => { const next = { ...current, [selected.id]: data.bookmarks }; writeBookmarks(next); return next; }); if (data.notes.length) setNotes((current) => { const next = { ...current, [selected.id]: data.notes }; writeNotes(next); return next; }); }).catch(() => undefined); return () => { cancelled = true; }; }, [selected, user]);
  useEffect(() => { document.body.classList.toggle('books-reading-mode', Boolean(selected)); return () => document.body.classList.remove('books-reading-mode'); }, [selected]);
  useEffect(() => { document.body.classList.toggle('books-reader-controls-visible', Boolean(selected && controlsVisible)); return () => document.body.classList.remove('books-reader-controls-visible'); }, [selected, controlsVisible]);
  useEffect(() => { if (!selected) return undefined; const started = Date.now(); const timer = window.setInterval(() => setSessionMinutes(Math.floor((Date.now() - started) / 60000)), 30_000); setSessionMinutes(0); return () => window.clearInterval(timer); }, [selected]);
  useEffect(() => { if (!selected || !controlsVisible || readerMenuOpen) return undefined; const timer = window.setTimeout(() => setControlsVisible(false), 3500); return () => window.clearTimeout(timer); }, [selected, controlsVisible, readerMenuOpen]);
  useEffect(() => { if (!selected || isEpub) return; const page = Number((progress[selected.id]?.locator || '').replace(/^page:/, '')); setPdfPage(Number.isFinite(page) && page > 0 ? page : 1); }, [isEpub, selected]);
  useEffect(() => () => window.speechSynthesis?.cancel(), []);

  const saveProgress = (book: BookItem, locator: string, fraction: number) => {
    const value = { locator, progress: Math.max(0, Math.min(1, fraction || 0)), t: Date.now() };
    setProgress((current) => { const next = { ...current, [book.id]: value }; writeProgress(next); return next; });
    if (user) void saveBookProgress(book.id, value).catch(() => undefined);
  };
  useEffect(() => {
    if (!selected || !isEpub || !epubRootRef.current) return undefined;
    let cancelled = false; let book: EpubBook | null = null; let timeout = 0; const epubTouchCleanups: Array<() => void> = []; const boundEpubDocuments = new Set<Document>(); setReaderError(''); setReaderLoading(true); setToc([]); setReaderPanel(null); setReaderMenuOpen(false); setFindStatus('');
    // epub.js captures JSZip when its script executes. Loading both scripts in
    // parallel lets epub.js permanently capture an undefined dependency.
    void loadZipRepair().then(loadEpubReader).then(() => epubSource(selected.readUrl)).then((source) => {
      if (cancelled || !window.ePub || !epubRootRef.current) return;
      epubRootRef.current.replaceChildren(); book = window.ePub(source, { openAs: 'binary' });
      void book.loaded?.navigation?.then((navigation) => { if (!cancelled) setToc(navigation.toc || []); });
      const rendition = book.renderTo(epubRootRef.current, { width: '100%', height: '100%', spread: 'none' }); renditionRef.current = rendition; applyEpubTheme(rendition, readerTheme);
      const bindEpubTouch = () => rendition.getContents?.().forEach((content) => { const doc = content.document; if (!doc || boundEpubDocuments.has(doc)) return; boundEpubDocuments.add(doc); let start: { x: number; y: number } | null = null; const onStart = (event: TouchEvent) => { const touch = event.changedTouches[0]; if (touch) start = { x: touch.clientX, y: touch.clientY }; }; const onEnd = (event: TouchEvent) => { const touch = event.changedTouches[0]; if (!start || !touch || readerMenuOpen) return; const dx = touch.clientX - start.x; const dy = touch.clientY - start.y; start = null; if (Math.abs(dx) < 56 || Math.abs(dy) > Math.abs(dx)) return; setControlsVisible(true); void (dx < 0 ? rendition.next() : rendition.prev()); }; doc.addEventListener('touchstart', onStart, { passive: true }); doc.addEventListener('touchend', onEnd, { passive: true }); epubTouchCleanups.push(() => { doc.removeEventListener('touchstart', onStart); doc.removeEventListener('touchend', onEnd); }); });
      rendition.on?.('rendered', bindEpubTouch);
      rendition.on?.('relocated', (location) => { const start = location.start; if (start?.cfi) saveProgress(selected, start.cfi, start.percentage || 0); });
      timeout = window.setTimeout(() => { if (!cancelled) { setReaderLoading(false); setReaderError('This EPUB is taking too long to open. Try downloading it and check that the file is a valid EPUB.'); } }, 12_000);
      return rendition.display(progress[selected.id]?.locator || undefined).then((result) => { window.clearTimeout(timeout); if (!cancelled) setReaderLoading(false); bindEpubTouch(); return result; });
    }).catch((err: unknown) => { if (!cancelled) { setReaderLoading(false); setReaderError(err instanceof Error ? err.message : 'This EPUB could not be opened.'); } });
    return () => { cancelled = true; window.clearTimeout(timeout); epubTouchCleanups.forEach((cleanup) => cleanup()); renditionRef.current?.destroy?.(); renditionRef.current = null; book?.destroy?.(); };
  }, [isEpub, selected]);
  useEffect(() => { if (isEpub && renditionRef.current) applyEpubTheme(renditionRef.current, readerTheme); }, [isEpub, readerTheme]);
  useEffect(() => {
    if (!selected || isEpub) return undefined;
    let cancelled = false; let task: { promise: Promise<PdfDocument>; destroy?: () => void } | null = null; setPdfDocument(null); setPdfPages(0); setReaderError(''); setReaderLoading(true);
    void loadPdfReader().then(async () => {
      if (!window.pdfjsLib || cancelled) return;
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
      task = window.pdfjsLib.getDocument(selected.readUrl);
      const document = await task.promise;
      if (cancelled) { document.destroy?.(); return; }
      setPdfPages(document.numPages); setPdfDocument(document); setReaderLoading(false);
      const restored = Number((progress[selected.id]?.locator || '').replace(/^page:/, ''));
      if (restored > 0) setPdfPage(Math.min(restored, document.numPages));
    }).catch((err: unknown) => { if (!cancelled) { setReaderLoading(false); setReaderError(err instanceof Error ? err.message : 'This PDF could not be opened.'); } });
    return () => { cancelled = true; task?.destroy?.(); };
  }, [isEpub, selected]);
  useEffect(() => {
    if (!pdfDocument || !pdfCanvasRef.current) return;
    let cancelled = false; let renderTask: { promise: Promise<unknown>; cancel?: () => void } | null = null;
    void pdfDocument.getPage(Math.max(1, Math.min(pdfPage, pdfDocument.numPages))).then((page) => {
      if (cancelled || !pdfCanvasRef.current) return;
      const viewport = page.getViewport({ scale: 1.5 }); const canvas = pdfCanvasRef.current; const context = canvas.getContext('2d');
      if (!context) return;
      canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height);
      renderTask = page.render({ canvasContext: context, viewport });
      return renderTask.promise;
    }).then(() => {
      if (cancelled || !pdfPendingScrollRef.current || !pdfRootRef.current) return;
      const position = pdfPendingScrollRef.current; pdfPendingScrollRef.current = null;
      requestAnimationFrame(() => {
        const root = pdfRootRef.current;
        if (!root) return;
        root.scrollTop = position === 'bottom' ? root.scrollHeight : 0;
        pdfScrollTopRef.current = root.scrollTop;
        pdfPageTurnLockedRef.current = false;
      });
    }).catch((err: unknown) => { if (!cancelled) setReaderError(err instanceof Error ? err.message : 'Could not render this PDF page.'); });
    return () => { cancelled = true; renderTask?.cancel?.(); };
  }, [pdfDocument, pdfPage]);

  const speak = () => { if (!('speechSynthesis' in window)) { setReaderError('Read aloud is not available in this browser.'); return; } if (speaking) { window.speechSynthesis.cancel(); setSpeaking(false); return; } const selectedText = window.getSelection()?.toString().trim() || ''; const epubText = renditionRef.current?.getContents?.().map((content) => content.document?.body?.innerText || '').join('\n').trim() || ''; const source = selectedText || epubText; if (!source) { setReaderError('Select text in the book first. EPUB chapters can also be read aloud.'); return; } const utterance = new SpeechSynthesisUtterance(source.slice(0, 12000)); utterance.rate = speechRate; utterance.onend = utterance.onerror = () => setSpeaking(false); setSpeaking(true); window.speechSynthesis.speak(utterance); };
  const openBook = (book: BookItem) => { setReaderError(''); setReaderMenuOpen(false); setReaderPanel(null); setControlsVisible(true); setSelected(book); };
  const closeReader = () => { window.speechSynthesis?.cancel(); setSpeaking(false); setReaderMenuOpen(false); setReaderPanel(null); setSelected(null); };
  const changePdfPage = (page: number, scrollTo: 'top' | 'bottom' = 'top') => { if (!selected) return; const next = Math.max(1, Math.min(pdfPages || Number.MAX_SAFE_INTEGER, Math.floor(page))); if (next === pdfPage) return; pdfPendingScrollRef.current = scrollTo; setPdfPage(next); saveProgress(selected, `page:${next}`, pdfPages ? next / pdfPages : 0); };
  const turnPage = (direction: 1 | -1) => { setControlsVisible(true); if (isEpub) void (direction > 0 ? renditionRef.current?.next() : renditionRef.current?.prev()); else changePdfPage(pdfPage + direction); };
  const onPdfScroll = (event: UIEvent<HTMLDivElement>) => { const root = event.currentTarget; const current = root.scrollTop; const direction = current - pdfScrollTopRef.current; pdfScrollTopRef.current = current; if (pdfPageTurnLockedRef.current || Math.abs(direction) < 2 || root.scrollHeight <= root.clientHeight + 4) return; if (direction > 0 && current + root.clientHeight >= root.scrollHeight - 28 && (!pdfPages || pdfPage < pdfPages)) { pdfPageTurnLockedRef.current = true; changePdfPage(pdfPage + 1, 'top'); } else if (direction < 0 && current <= 2 && pdfPage > 1) { pdfPageTurnLockedRef.current = true; changePdfPage(pdfPage - 1, 'bottom'); } };
  useEffect(() => { if (!selected || isEpub || !pdfRootRef.current) return undefined; const root = pdfRootRef.current; const touchStart = (event: TouchEvent) => { const touch = event.changedTouches[0]; if (touch) touchStartRef.current = { x: touch.clientX, y: touch.clientY }; }; const touchEnd = (event: TouchEvent) => { const start = touchStartRef.current; touchStartRef.current = null; const touch = event.changedTouches[0]; if (!start || !touch || readerMenuOpen) return; const dx = touch.clientX - start.x; if (Math.abs(dx) < 56 || Math.abs(touch.clientY - start.y) > Math.abs(dx)) return; setControlsVisible(true); changePdfPage(pdfPage + (dx < 0 ? 1 : -1), dx < 0 ? 'top' : 'bottom'); }; root.addEventListener('touchstart', touchStart, { passive: true }); root.addEventListener('touchend', touchEnd, { passive: true }); return () => { root.removeEventListener('touchstart', touchStart); root.removeEventListener('touchend', touchEnd); }; }, [selected, isEpub, pdfPage, pdfPages, readerMenuOpen]);
  useEffect(() => { if (!selected) return undefined; const onKey = (event: KeyboardEvent) => { if ((event.target as HTMLElement)?.tagName === 'INPUT') return; if (event.key === 'ArrowLeft') { event.preventDefault(); turnPage(-1); } if (event.key === 'ArrowRight') { event.preventDefault(); turnPage(1); } if (event.key === 'Escape') { setReaderMenuOpen(false); setReaderPanel(null); setControlsVisible(false); } }; window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey); }, [selected, isEpub, pdfPage]);
  useEffect(() => { if (!selected) return undefined; const down = (event: PointerEvent) => { const target = event.target as HTMLElement | null; if (!target?.closest('.books-reader')) return; gestureStart.current = { x: event.clientX, y: event.clientY }; }; const up = (event: PointerEvent) => { const target = event.target as HTMLElement | null; const start = gestureStart.current; gestureStart.current = null; if (!start || !target?.closest('.books-reader') || readerMenuOpen) return; const dx = event.clientX - start.x; if (Math.abs(dx) > 52) { turnPage(dx < 0 ? 1 : -1); return; } if (Math.abs(event.clientY - start.y) > 28) return; if (controlsVisible) { setControlsVisible(false); return; } if (event.clientX < window.innerWidth * .25) turnPage(-1); else if (event.clientX > window.innerWidth * .75) turnPage(1); else setControlsVisible(true); }; window.addEventListener('pointerdown', down); window.addEventListener('pointerup', up); return () => { window.removeEventListener('pointerdown', down); window.removeEventListener('pointerup', up); }; }, [selected, controlsVisible, readerMenuOpen, isEpub, pdfPage]);
  const addBookmark = () => { if (!selected || !isEpub) return; const current = progress[selected.id]; if (!current?.locator) { setReaderError('Open a chapter before saving a bookmark.'); return; } const bookEntries = [{ locator: current.locator, label: `${Math.round(current.progress * 100)}% through`, progress: current.progress, t: Date.now() }, ...(bookmarks[selected.id] || []).filter((entry) => entry.locator !== current.locator)].slice(0, 30); const next = { ...bookmarks, [selected.id]: bookEntries }; setBookmarks(next); writeBookmarks(next); if (user) void saveBookReaderData(selected.id, { bookmarks: bookEntries, notes: notes[selected.id] || [] }).catch(() => undefined); setReaderPanel('bookmarks'); };
  const addNote = () => { if (!selected || !isEpub) return; const text = window.getSelection()?.toString().trim() || renditionRef.current?.getContents?.().map((entry) => entry.document?.getSelection()?.toString().trim() || '').find(Boolean) || ''; if (!text) { setReaderError('Select text in the open chapter before adding a note.'); return; } const noteEntries = [{ text: text.slice(0, 1200), progress: progress[selected.id]?.progress || 0, t: Date.now() }, ...(notes[selected.id] || [])].slice(0, 50); const next = { ...notes, [selected.id]: noteEntries }; setNotes(next); writeNotes(next); if (user) void saveBookReaderData(selected.id, { bookmarks: bookmarks[selected.id] || [], notes: noteEntries }).catch(() => undefined); setReaderPanel('notes'); };
  const findInBook = () => { const query = findQuery.trim().toLowerCase(); if (!query) { setFindStatus('Enter text to find.'); return; } const docs = renditionRef.current?.getContents?.() || []; const doc = docs.map((entry) => entry.document).find((entry) => (entry?.body?.innerText || '').toLowerCase().includes(query)); if (!doc) { setFindStatus('No match in the open chapter. Move chapters and try again.'); return; } const node = Array.from(doc.body.querySelectorAll('*')).find((entry) => (entry.textContent || '').toLowerCase().includes(query)); (node as HTMLElement | undefined)?.scrollIntoView({ block: 'center' }); setFindStatus('Match found in the open chapter.'); };
  const renderToc = (entries: EpubTocItem[], depth = 0): ReactNode[] => entries.flatMap((entry) => [<button key={`${depth}-${entry.href}`} type="button" className="books-reader-panel-link" style={{ paddingLeft: `${0.7 + depth * 0.8}rem` }} onClick={() => { void renditionRef.current?.display(entry.href); setReaderPanel(null); }}>{entry.label}</button>, ...renderToc(entry.subitems || [], depth + 1)]);
  const bookSummary = (book: BookItem) => book.description || book.authors.join(', ') || [book.publisher, book.language, book.pageCount ? `${book.pageCount} pages` : '', book.format].filter(Boolean).join(' · ') || 'Book';

  if (selected) return (
    <main className="books-reader-page">
      <section className="books-reader-shell">
        <header className="books-reader-toolbar">
          <Button className="books-reader-back" variant="ghost" size="sm" aria-label="Back to library" onClick={closeReader}>←</Button>
          <div className="books-file-label">
            <BookOpenIcon />
            <span><strong>{selected.title}</strong><small>{selected.fileName} · {selected.fileSizeLabel}</small></span>
          </div>
          <Button className="books-reader-tools" variant="ghost" size="sm" aria-label={isEpub ? 'Open reading settings' : 'Open reader tools'} aria-expanded={readerMenuOpen} onClick={() => { setControlsVisible(true); setReaderMenuOpen((open) => !open); }}>{isEpub ? <span className="books-reader-appearance-glyph" aria-hidden="true">A<span>a</span></span> : <MoreVerticalIcon />}</Button>
        </header>

        {readerMenuOpen && <aside className="books-reader-menu" aria-label={isEpub ? 'Reading settings' : 'Reader tools'}>
          <div className="books-reader-menu-heading"><strong>{isEpub ? 'Reading settings' : 'Reader tools'}</strong><Button variant="ghost" size="sm" onClick={() => { setReaderMenuOpen(false); setReaderPanel(null); }}>Done</Button></div>
          {isEpub ? <>
            <section className="books-reader-appearance" aria-label="Reading theme"><span>Appearance</span><div><Button className={`books-reader-theme-option${readerTheme === 'light' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={readerTheme === 'light'} onClick={() => setReaderTheme('light')}><i className="books-reader-theme-swatch books-reader-theme-light" />Light</Button><Button className={`books-reader-theme-option${readerTheme === 'dark' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={readerTheme === 'dark'} onClick={() => setReaderTheme('dark')}><i className="books-reader-theme-swatch books-reader-theme-dark" />Dark</Button></div></section>
            <p className="books-reader-menu-section-label">Reading tools</p>
            <div className="books-reader-menu-actions">
              <Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'contents' ? null : 'contents')}>Contents</Button>
              <Button variant="secondary" size="sm" onClick={addBookmark}><BookmarkIcon /> Bookmark</Button>
              <Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'bookmarks' ? null : 'bookmarks')}>Bookmarks</Button>
              <Button variant="secondary" size="sm" onClick={addNote}>Add note</Button>
              <Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'notes' ? null : 'notes')}>Notes</Button>
              <Button variant="secondary" size="sm" onClick={speak}>{speaking ? <PauseIcon /> : <VolumeIcon />}{speaking ? 'Stop reading' : 'Listen'}</Button>
            </div>
            <label className="books-reader-menu-rate">Reading speed<select value={speechRate} onChange={(event) => setSpeechRate(Number(event.currentTarget.value))}><option value={0.8}>0.8×</option><option value={1}>1×</option><option value={1.25}>1.25×</option><option value={1.5}>1.5×</option></select></label>
          </> : <>
            <label className="books-reader-menu-rate">Go to page<input type="number" min="1" max={pdfPages || undefined} value={pdfPage} onChange={(event) => changePdfPage(Number(event.currentTarget.value))} /></label>
            <a className="books-reader-download" href={selected.downloadUrl}><DownloadIcon /> Download original PDF</a>
          </>}
          {readerPanel && isEpub && <section className="books-reader-panel"><strong>{readerPanel === 'contents' ? 'Contents' : readerPanel === 'bookmarks' ? 'Bookmarks' : 'Notes'}</strong>{readerPanel === 'contents' ? (toc.length ? renderToc(toc) : <small>Contents unavailable for this EPUB.</small>) : readerPanel === 'bookmarks' ? ((bookmarks[selected.id] || []).length ? (bookmarks[selected.id] || []).map((entry) => <button key={entry.t} type="button" className="books-reader-panel-link" onClick={() => { void renditionRef.current?.display(entry.locator); setReaderPanel(null); }}>{entry.label}</button>) : <small>No bookmarks yet.</small>) : ((notes[selected.id] || []).length ? (notes[selected.id] || []).map((entry) => <p key={entry.t} className="books-reader-note">{entry.text}</p>) : <small>Select text and choose Add note to save a highlight.</small>)}</section>}
        </aside>}

        {readerLoading && <p className="books-reader-loading" role="status">Opening {isEpub ? 'EPUB' : 'PDF'}…</p>}
        {readerError && <p className="books-reader-error" role="alert">{readerError}</p>}
        <div className="books-reader">
          {isEpub ? <div ref={epubRootRef} className={`epub-reader epub-reader-${readerTheme}`} aria-label={`${selected.title} reader`} /> : <div ref={pdfRootRef} className="pdf-reader" onScroll={onPdfScroll}><canvas ref={pdfCanvasRef} aria-label={`${selected.title} PDF page ${pdfPage}`} /></div>}
        </div>
        {!isEpub && <nav className="books-reader-pagination" aria-label="PDF page navigation">
          <Button variant="ghost" size="sm" onClick={() => changePdfPage(pdfPage - 1)} disabled={pdfPage <= 1}>Previous</Button>
          <button type="button" className="books-reader-page-indicator" onClick={() => { setControlsVisible(true); setReaderMenuOpen(true); }}>Page {pdfPage}{pdfPages ? ` / ${pdfPages}` : ''}</button>
          <Button variant="ghost" size="sm" onClick={() => changePdfPage(pdfPage + 1)} disabled={Boolean(pdfPages && pdfPage >= pdfPages)}>Next</Button>
        </nav>}
        <footer className="books-reader-footer"><span>{isEpub && progress[selected.id]?.progress ? `${Math.round(progress[selected.id].progress * 100)}% read` : isEpub ? 'Swipe or tap the edge to turn pages' : `Page ${pdfPage}${pdfPages ? ` of ${pdfPages}` : ''} saved`}{sessionMinutes ? ` · ${sessionMinutes} min this session` : ''}</span><span>{user ? (isEpub ? 'Your place, bookmarks, and notes sync to your library account.' : 'Tap the page number for more options.') : 'Saved on this device. Sign in to sync across devices.'}</span></footer>
      </section>
    </main>
  );
  return <main className="hub-main books-page"><section className="books-hero"><div><p className="eyebrow">Library books</p><h1>Your reading library.</h1><p>Browse and read available PDFs and EPUBs from one comfortable place.</p></div></section><label className="books-search"><SearchIcon /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search books" /></label>{loading ? <p className="books-state">Loading books…</p> : error ? <p className="books-reader-error">{error}</p> : items.length ? <div className="books-grid">{items.map((book) => <article className="book-card" key={book.id} role="button" tabIndex={0} onClick={() => openBook(book)} onKeyDown={(event) => { if (event.currentTarget !== event.target) return; if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openBook(book); } }}><span className="book-cover book-cover-image">{book.coverUrl && <img src={book.coverUrl} alt="" loading="lazy" decoding="async" onError={(event) => { event.currentTarget.hidden = true; }} />}<BookOpenIcon /><small>{book.format}</small></span><div><p className="eyebrow">{book.format} · {book.fileSizeLabel}{progress[book.id]?.progress ? ` · ${Math.round(progress[book.id].progress * 100)}% read` : ''}</p><h2>{book.title}</h2>{book.authors.length > 0 && <p className="book-author">{book.authors.join(', ')}</p>}<p>{bookSummary(book)}</p></div></article>)}</div> : <section className="books-dropzone books-empty"><BookOpenIcon /><strong>{query.trim() ? `No books match “${query.trim()}”` : 'No books in your library yet'}</strong><span>{query.trim() ? 'Try another title, author, or filename.' : 'Check back soon for new titles to read.'}</span>{query.trim() && <Button variant="secondary" size="sm" onClick={() => setQuery('')}>Clear search</Button>}</section>}</main>;
}
