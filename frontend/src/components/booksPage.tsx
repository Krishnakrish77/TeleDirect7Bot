import { type ReactNode, type UIEvent, useEffect, useRef, useState } from 'react';
import { fetchBookProgress, fetchBookReaderData, fetchBooks, saveBookProgress, saveBookReaderData } from '../api';
import { BookOpenIcon, BookmarkIcon, DownloadIcon, ListIcon, ListPlusIcon, MoreVerticalIcon, PauseIcon, SearchIcon, SkipBackIcon, SkipForwardIcon, VolumeIcon } from '../icons';
import type { BookItem, BookProgressMap, User } from '../types';
import { Button } from './ui/button';

declare global { interface Window { ePub?: (source: string | ArrayBuffer, options?: { openAs?: 'epub' | 'binary' }) => EpubBook; pdfjsLib?: PdfJs; JSZip?: JsZipStatic; } }
type EpubLocation = { start?: { cfi?: string; percentage?: number } };
type EpubTocItem = { label: string; href: string; subitems?: EpubTocItem[] };
type EpubRendition = { display: (target?: string | number) => Promise<unknown>; next: () => Promise<unknown>; prev: () => Promise<unknown>; on?: (event: string, callback: (location: EpubLocation) => void) => void; getContents?: () => Array<{ document?: Document }>; themes?: { register: (name: string, rules: Record<string, Record<string, string>>) => void; select: (name: string) => void }; destroy?: () => void; };
type EpubSearchMatch = { cfi: string; excerpt?: string };
type EpubSection = { load: () => Promise<Document>; find: (query: string) => EpubSearchMatch[]; unload?: () => void };
type EpubBook = { renderTo: (element: HTMLElement, options: Record<string, unknown>) => EpubRendition; loaded?: { navigation?: Promise<{ toc?: EpubTocItem[] }> }; spine?: { each: (callback: (section: EpubSection) => void) => void }; destroy?: () => void; };
type PdfViewport = { width: number; height: number };
type PdfPage = { getViewport: (params: { scale: number }) => PdfViewport; getTextContent: () => Promise<{ items: Array<{ str?: string }> }>; render: (params: { canvasContext: CanvasRenderingContext2D; viewport: PdfViewport }) => { promise: Promise<unknown>; cancel?: () => void } };
type PdfOutlineItem = { title: string; dest?: string | unknown[] | null; items?: PdfOutlineItem[] };
type PdfDocument = { numPages: number; getPage: (page: number) => Promise<PdfPage>; getOutline?: () => Promise<PdfOutlineItem[] | null>; getDestination?: (name: string) => Promise<unknown[] | null>; getPageIndex?: (ref: unknown) => Promise<number>; destroy?: () => void };
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
const EPUB_PREFERENCES_KEY = 'td:epub-preferences';
type ReaderTheme = 'light' | 'sepia' | 'dark';
type BookFormatFilter = 'all' | 'pdf' | 'epub';
type BookReadingFilter = 'all' | 'reading' | 'unread' | 'finished';
type BookSort = 'added' | 'title' | 'author' | 'progress';
type EpubPreferences = { theme: ReaderTheme; fontSize: number; fontFamily: 'serif' | 'sans'; lineHeight: 'compact' | 'relaxed'; margins: 'narrow' | 'wide' };
const DEFAULT_EPUB_PREFERENCES: EpubPreferences = { theme: 'light', fontSize: 100, fontFamily: 'serif', lineHeight: 'relaxed', margins: 'wide' };

function localProgress(): BookProgressMap { try { return JSON.parse(localStorage.getItem(PROGRESS_KEY) || '{}') || {}; } catch (_) { return {}; } }
function writeProgress(value: BookProgressMap) { try { localStorage.setItem(PROGRESS_KEY, JSON.stringify(value)); } catch (_) {} }
function localBookmarks(): Record<string, BookBookmark[]> { try { return JSON.parse(localStorage.getItem(BOOKMARKS_KEY) || '{}') || {}; } catch (_) { return {}; } }
function writeBookmarks(value: Record<string, BookBookmark[]>) { try { localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(value)); } catch (_) {} }
function localNotes(): Record<string, BookNote[]> { try { return JSON.parse(localStorage.getItem(NOTES_KEY) || '{}') || {}; } catch (_) { return {}; } }
function writeNotes(value: Record<string, BookNote[]>) { try { localStorage.setItem(NOTES_KEY, JSON.stringify(value)); } catch (_) {} }
function localEpubPreferences(): EpubPreferences {
  try {
    const stored = JSON.parse(localStorage.getItem(EPUB_PREFERENCES_KEY) || '{}') as Partial<EpubPreferences>;
    return {
      theme: stored.theme === 'dark' || stored.theme === 'sepia' ? stored.theme : 'light',
      fontSize: typeof stored.fontSize === 'number' ? Math.max(85, Math.min(135, Math.round(stored.fontSize / 5) * 5)) : DEFAULT_EPUB_PREFERENCES.fontSize,
      fontFamily: stored.fontFamily === 'sans' ? 'sans' : 'serif',
      lineHeight: stored.lineHeight === 'compact' ? 'compact' : 'relaxed',
      margins: stored.margins === 'narrow' ? 'narrow' : 'wide',
    };
  } catch (_) { return DEFAULT_EPUB_PREFERENCES; }
}
function writeEpubPreferences(value: EpubPreferences) { try { localStorage.setItem(EPUB_PREFERENCES_KEY, JSON.stringify(value)); } catch (_) {} }
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
function speechChunks(text: string): string[] {
  const normalized = text.replace(/\s+/g, ' ').trim();
  if (!normalized) return [];
  const chunks: string[] = []; let remainder = normalized;
  while (remainder.length > 0) {
    if (remainder.length <= 2800) { chunks.push(remainder); break; }
    const boundary = Math.max(remainder.lastIndexOf('. ', 2800), remainder.lastIndexOf('? ', 2800), remainder.lastIndexOf('! ', 2800), remainder.lastIndexOf(' ', 2800));
    const end = boundary > 300 ? boundary + 1 : 2800;
    chunks.push(remainder.slice(0, end).trim()); remainder = remainder.slice(end).trim();
  }
  return chunks;
}
function applyEpubTheme(rendition: EpubRendition, preferences: EpubPreferences) {
  const palette = preferences.theme === 'dark' ? { background: '#121416', color: '#f1f5f9', link: '#fdba74' } : preferences.theme === 'sepia' ? { background: '#f4ecd8', color: '#3f3224', link: '#9a3412' } : { background: '#f8f4e9', color: '#1c1917', link: '#9a3412' };
  const themeName = `teledirect-${preferences.theme}-${preferences.fontSize}-${preferences.fontFamily}-${preferences.lineHeight}-${preferences.margins}`;
  rendition.themes?.register(themeName, { body: { background: `${palette.background} !important`, color: `${palette.color} !important`, 'font-family': `${preferences.fontFamily === 'serif' ? 'Georgia, Cambria, serif' : 'system-ui, -apple-system, BlinkMacSystemFont, sans-serif'} !important`, 'font-size': `${preferences.fontSize}% !important`, 'line-height': `${preferences.lineHeight === 'relaxed' ? '1.8' : '1.5'} !important`, padding: `${preferences.margins === 'wide' ? '0 8%' : '0 3%'} !important`, 'box-sizing': 'border-box !important' }, p: { 'line-height': 'inherit !important' }, a: { color: `${palette.link} !important` } });
  rendition.themes?.select(themeName);
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

function PdfThumbnail({ document, pageNumber, selected, onSelect }: { document: PdfDocument; pageNumber: number; selected: boolean; onSelect: (page: number) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let cancelled = false; let task: { promise: Promise<unknown>; cancel?: () => void } | null = null;
    void document.getPage(pageNumber).then((page) => {
      if (cancelled || !canvasRef.current) return;
      const viewport = page.getViewport({ scale: .22 }); const canvas = canvasRef.current; const context = canvas.getContext('2d');
      if (!context) return;
      canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height);
      task = page.render({ canvasContext: context, viewport });
      return task.promise;
    }).catch(() => undefined);
    return () => { cancelled = true; task?.cancel?.(); };
  }, [document, pageNumber]);
  return <button type="button" className={`pdf-thumbnail${selected ? ' is-current' : ''}`} aria-label={`Open page ${pageNumber}`} aria-current={selected ? 'page' : undefined} onClick={() => onSelect(pageNumber)}><canvas ref={canvasRef} aria-hidden="true" /><span>{pageNumber}</span></button>;
}

export function BooksPage({ user }: { user: User | null }) {
  const [items, setItems] = useState<BookItem[]>([]); const [query, setQuery] = useState(''); const [debouncedQuery, setDebouncedQuery] = useState(''); const [selected, setSelected] = useState<BookItem | null>(null);
  const [formatFilter, setFormatFilter] = useState<BookFormatFilter>('all'); const [readingFilter, setReadingFilter] = useState<BookReadingFilter>('all'); const [bookSort, setBookSort] = useState<BookSort>('added'); const [bookIdFromUrl, setBookIdFromUrl] = useState(() => new URLSearchParams(window.location.search).get('book'));
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [readerError, setReaderError] = useState(''); const [readerLoading, setReaderLoading] = useState(false); const [speaking, setSpeaking] = useState(false); const [keepControlsVisible, setKeepControlsVisible] = useState(false); const [epubPreferences, setEpubPreferences] = useState<EpubPreferences>(() => localEpubPreferences());
  const [progress, setProgress] = useState<BookProgressMap>(() => localProgress());
  const [bookmarks, setBookmarks] = useState<Record<string, BookBookmark[]>>(() => localBookmarks()); const [notes, setNotes] = useState<Record<string, BookNote[]>>(() => localNotes()); const [toc, setToc] = useState<EpubTocItem[]>([]); const [readerPanel, setReaderPanel] = useState<'contents' | 'bookmarks' | 'notes' | 'pages' | 'outline' | null>(null); const [readerMenuOpen, setReaderMenuOpen] = useState(false); const [findQuery, setFindQuery] = useState(''); const [findStatus, setFindStatus] = useState(''); const [epubSearchMatches, setEpubSearchMatches] = useState<EpubSearchMatch[]>([]); const [epubSearchIndex, setEpubSearchIndex] = useState(0); const [epubSearchBusy, setEpubSearchBusy] = useState(false); const [pdfSearchMatches, setPdfSearchMatches] = useState<number[]>([]); const [pdfSearchIndex, setPdfSearchIndex] = useState(0); const [speechRate, setSpeechRate] = useState(1); const [speechPaused, setSpeechPaused] = useState(false); const [speechPage, setSpeechPage] = useState<number | null>(null); const [sessionMinutes, setSessionMinutes] = useState(0); const [pdfPage, setPdfPage] = useState(1); const [pdfDocument, setPdfDocument] = useState<PdfDocument | null>(null); const [pdfPages, setPdfPages] = useState(0); const [pdfOutline, setPdfOutline] = useState<PdfOutlineItem[]>([]); const [pdfZoom, setPdfZoom] = useState<number | 'fit'>('fit'); const [pdfReaderWidth, setPdfReaderWidth] = useState(0); const [pdfSearchBusy, setPdfSearchBusy] = useState(false); const [pdfNoteDraft, setPdfNoteDraft] = useState(''); const [controlsVisible, setControlsVisible] = useState(true);
  const epubRootRef = useRef<HTMLDivElement>(null); const epubBookRef = useRef<EpubBook | null>(null); const renditionRef = useRef<EpubRendition | null>(null); const pdfCanvasRef = useRef<HTMLCanvasElement>(null); const pdfRootRef = useRef<HTMLDivElement>(null); const gestureStart = useRef<{ x: number; y: number } | null>(null); const pdfScrollTopRef = useRef(0); const pdfPageTurnLockedRef = useRef(false); const pdfPendingScrollRef = useRef<'top' | 'bottom' | null>(null); const touchStartRef = useRef<{ x: number; y: number; scrollLeft: number; panning: boolean } | null>(null); const pdfPinchRef = useRef<{ distance: number; scale: number } | null>(null); const epubSearchTokenRef = useRef(0); const pdfSearchTokenRef = useRef(0); const pdfTextCacheRef = useRef(new Map<number, string>()); const speechTokenRef = useRef(0); const readerMenuOpenRef = useRef(false);
  const isEpub = selected?.format.toLowerCase() === 'epub';

  useEffect(() => { const timer = window.setTimeout(() => setDebouncedQuery(query), 250); return () => window.clearTimeout(timer); }, [query]);
  useEffect(() => { const controller = new AbortController(); let active = true; setLoading(true); void fetchBooks(debouncedQuery, controller.signal).then((data) => { if (active) setItems(data.items); }).catch((err) => { if (active && err.name !== 'AbortError') setError(err.message || 'Unable to load books.'); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; controller.abort(); }; }, [debouncedQuery]);
  useEffect(() => { const onPopState = () => setBookIdFromUrl(new URLSearchParams(window.location.search).get('book')); window.addEventListener('popstate', onPopState); return () => window.removeEventListener('popstate', onPopState); }, []);
  useEffect(() => { if (!bookIdFromUrl) { setSelected(null); return; } const book = items.find((entry) => entry.id === bookIdFromUrl); if (book) setSelected(book); }, [bookIdFromUrl, items]);
  useEffect(() => { if (!user) return; void fetchBookProgress().then((server) => { const local = localProgress(); const merged: BookProgressMap = { ...local }; Object.entries(server).forEach(([bookId, value]) => { if (!merged[bookId] || value.t > merged[bookId].t) merged[bookId] = value; }); writeProgress(merged); setProgress(merged); }).catch(() => undefined); }, [user]);
  useEffect(() => { if (!selected || !user) return; let cancelled = false; void fetchBookReaderData(selected.id).then((data) => { if (cancelled) return; if (data.bookmarks.length) setBookmarks((current) => { const next = { ...current, [selected.id]: data.bookmarks }; writeBookmarks(next); return next; }); if (data.notes.length) setNotes((current) => { const next = { ...current, [selected.id]: data.notes }; writeNotes(next); return next; }); }).catch(() => undefined); return () => { cancelled = true; }; }, [selected, user]);
  useEffect(() => { document.body.classList.toggle('books-reading-mode', Boolean(selected)); return () => document.body.classList.remove('books-reading-mode'); }, [selected]);
  useEffect(() => { document.body.classList.toggle('books-reader-controls-visible', Boolean(selected && controlsVisible)); return () => document.body.classList.remove('books-reader-controls-visible'); }, [selected, controlsVisible]);
  useEffect(() => { readerMenuOpenRef.current = readerMenuOpen; }, [readerMenuOpen]);
  useEffect(() => { if (!selected) return undefined; const started = Date.now(); const timer = window.setInterval(() => setSessionMinutes(Math.floor((Date.now() - started) / 60000)), 30_000); setSessionMinutes(0); return () => window.clearInterval(timer); }, [selected]);
  useEffect(() => { if (!selected || !controlsVisible || readerMenuOpen || keepControlsVisible) return undefined; const timer = window.setTimeout(() => { if (!document.querySelector('.books-reader-page :focus-visible')) setControlsVisible(false); }, 3500); return () => window.clearTimeout(timer); }, [selected, controlsVisible, readerMenuOpen, keepControlsVisible]);
  useEffect(() => { if (!selected || isEpub) return; const page = Number((progress[selected.id]?.locator || '').replace(/^page:/, '')); setPdfPage(Number.isFinite(page) && page > 0 ? page : 1); }, [isEpub, selected]);
  useEffect(() => () => window.speechSynthesis?.cancel(), []);

  const saveProgress = (book: BookItem, locator: string, fraction: number) => {
    const value = { locator, progress: Math.max(0, Math.min(1, fraction || 0)), t: Date.now() };
    setProgress((current) => { const next = { ...current, [book.id]: value }; writeProgress(next); return next; });
    if (user) void saveBookProgress(book.id, value).catch(() => undefined);
  };
  useEffect(() => {
    if (!selected || !isEpub || !epubRootRef.current) return undefined;
    let cancelled = false; let book: EpubBook | null = null; let timeout = 0; const epubTouchCleanups: Array<() => void> = []; const boundEpubDocuments = new Set<Document>(); epubSearchTokenRef.current += 1; setEpubSearchMatches([]); setEpubSearchIndex(0); setReaderError(''); setReaderLoading(true); setToc([]); setReaderPanel(null); setReaderMenuOpen(false); setFindStatus('');
    // epub.js captures JSZip when its script executes. Loading both scripts in
    // parallel lets epub.js permanently capture an undefined dependency.
    void loadZipRepair().then(loadEpubReader).then(() => epubSource(selected.readUrl)).then((source) => {
      if (cancelled || !window.ePub || !epubRootRef.current) return;
      epubRootRef.current.replaceChildren(); book = window.ePub(source, { openAs: 'binary' }); epubBookRef.current = book;
      void book.loaded?.navigation?.then((navigation) => { if (!cancelled) setToc(navigation.toc || []); });
      const rendition = book.renderTo(epubRootRef.current, { width: '100%', height: '100%', spread: 'none' }); renditionRef.current = rendition; applyEpubTheme(rendition, epubPreferences);
      const bindEpubTouch = () => rendition.getContents?.().forEach((content) => { const doc = content.document; if (!doc || boundEpubDocuments.has(doc)) return; boundEpubDocuments.add(doc); let start: { x: number; y: number } | null = null; const showControls = () => { if (!readerMenuOpenRef.current) setControlsVisible(true); }; const onStart = (event: TouchEvent) => { const touch = event.changedTouches[0]; if (touch) start = { x: touch.clientX, y: touch.clientY }; }; const onEnd = (event: TouchEvent) => { const touch = event.changedTouches[0]; if (!start || !touch || readerMenuOpenRef.current) return; const dx = touch.clientX - start.x; const dy = touch.clientY - start.y; start = null; if (Math.abs(dx) < 56 || Math.abs(dy) > Math.abs(dx)) { showControls(); return; } setControlsVisible(true); void (dx < 0 ? rendition.next() : rendition.prev()); }; doc.addEventListener('click', showControls); doc.addEventListener('touchstart', onStart, { passive: true }); doc.addEventListener('touchend', onEnd, { passive: true }); epubTouchCleanups.push(() => { doc.removeEventListener('click', showControls); doc.removeEventListener('touchstart', onStart); doc.removeEventListener('touchend', onEnd); }); });
      rendition.on?.('rendered', bindEpubTouch);
      rendition.on?.('relocated', (location) => { const start = location.start; if (start?.cfi) saveProgress(selected, start.cfi, start.percentage || 0); });
      timeout = window.setTimeout(() => { if (!cancelled) { setReaderLoading(false); setReaderError('This EPUB is taking too long to open. Try downloading it and check that the file is a valid EPUB.'); } }, 12_000);
      return rendition.display(progress[selected.id]?.locator || undefined).then((result) => { window.clearTimeout(timeout); if (!cancelled) { setReaderLoading(false); setReaderError(''); } bindEpubTouch(); return result; });
    }).catch((err: unknown) => { if (!cancelled) { setReaderLoading(false); setReaderError(err instanceof Error ? err.message : 'This EPUB could not be opened.'); } });
    return () => { cancelled = true; epubSearchTokenRef.current += 1; window.clearTimeout(timeout); epubTouchCleanups.forEach((cleanup) => cleanup()); renditionRef.current?.destroy?.(); renditionRef.current = null; epubBookRef.current = null; book?.destroy?.(); };
  }, [isEpub, selected]);
  useEffect(() => { if (isEpub && renditionRef.current) applyEpubTheme(renditionRef.current, epubPreferences); }, [isEpub, epubPreferences]);
  useEffect(() => {
    if (!selected || isEpub) return undefined;
    let cancelled = false; let task: { promise: Promise<PdfDocument>; destroy?: () => void } | null = null; pdfSearchTokenRef.current += 1; pdfTextCacheRef.current.clear(); setPdfSearchMatches([]); setPdfSearchIndex(0); setPdfDocument(null); setPdfPages(0); setPdfOutline([]); setReaderError(''); setReaderLoading(true);
    void loadPdfReader().then(async () => {
      if (!window.pdfjsLib || cancelled) return;
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
      task = window.pdfjsLib.getDocument(selected.readUrl);
      const document = await task.promise;
      if (cancelled) { document.destroy?.(); return; }
      setPdfPages(document.numPages); setPdfDocument(document); setReaderLoading(false); void document.getOutline?.().then((outline) => { if (!cancelled) setPdfOutline(outline || []); }).catch(() => undefined);
      const restored = Number((progress[selected.id]?.locator || '').replace(/^page:/, ''));
      if (restored > 0) setPdfPage(Math.min(restored, document.numPages));
    }).catch((err: unknown) => { if (!cancelled) { setReaderLoading(false); setReaderError(err instanceof Error ? err.message : 'This PDF could not be opened.'); } });
    return () => { cancelled = true; task?.destroy?.(); };
  }, [isEpub, selected]);
  useEffect(() => {
    if (!selected || isEpub || !pdfRootRef.current) return undefined;
    const root = pdfRootRef.current; const updateWidth = () => setPdfReaderWidth(root.clientWidth); updateWidth(); if (!('ResizeObserver' in window)) return undefined;
    const observer = new ResizeObserver(updateWidth); observer.observe(root);
    return () => observer.disconnect();
  }, [isEpub, selected]);
  useEffect(() => {
    if (!pdfDocument || !pdfCanvasRef.current) return;
    let cancelled = false; let renderTask: { promise: Promise<unknown>; cancel?: () => void } | null = null;
    void pdfDocument.getPage(Math.max(1, Math.min(pdfPage, pdfDocument.numPages))).then((page) => {
      if (cancelled || !pdfCanvasRef.current) return;
      const baseViewport = page.getViewport({ scale: 1 }); const fitScale = pdfReaderWidth ? Math.max(.5, (pdfReaderWidth - 32) / baseViewport.width) : 1.15; const displayScale = pdfZoom === 'fit' ? fitScale : pdfZoom; const deviceScale = Math.min(window.devicePixelRatio || 1, 2); const viewport = page.getViewport({ scale: displayScale * deviceScale }); const displayViewport = page.getViewport({ scale: displayScale }); const canvas = pdfCanvasRef.current; const context = canvas.getContext('2d');
      if (!context) return;
      canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height); canvas.style.width = `${Math.ceil(displayViewport.width)}px`; canvas.style.height = `${Math.ceil(displayViewport.height)}px`;
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
  }, [pdfDocument, pdfPage, pdfReaderWidth, pdfZoom]);

  const stopSpeech = () => { speechTokenRef.current += 1; window.speechSynthesis?.cancel(); setSpeaking(false); setSpeechPaused(false); setSpeechPage(null); };
  const playSpeech = (chunks: string[], page: number | null, onComplete?: () => void) => {
    if (!('speechSynthesis' in window)) { setReaderError('Read aloud is not available in this browser.'); return; }
    const token = speechTokenRef.current + 1; speechTokenRef.current = token; window.speechSynthesis.cancel(); setReaderError(''); setSpeaking(true); setSpeechPaused(false); setSpeechPage(page);
    const playChunk = (index: number) => {
      if (token !== speechTokenRef.current) return;
      if (index >= chunks.length) { if (onComplete) { onComplete(); return; } setSpeaking(false); setSpeechPaused(false); setSpeechPage(null); return; }
      const utterance = new SpeechSynthesisUtterance(chunks[index]); utterance.rate = speechRate;
      utterance.onend = () => playChunk(index + 1);
      utterance.onerror = (event) => { if (token !== speechTokenRef.current || event.error === 'canceled' || event.error === 'interrupted') return; setReaderError('Reading this page stopped unexpectedly.'); setSpeaking(false); setSpeechPaused(false); setSpeechPage(null); };
      window.speechSynthesis.speak(utterance);
    };
    playChunk(0);
  };
  const speak = () => {
    if (!('speechSynthesis' in window)) { setReaderError('Read aloud is not available in this browser.'); return; }
    if (speaking) { if (speechPaused) { window.speechSynthesis.resume(); setSpeechPaused(false); } else { window.speechSynthesis.pause(); setSpeechPaused(true); } return; }
    const selectedText = window.getSelection()?.toString().trim() || ''; const epubText = renditionRef.current?.getContents?.().map((content) => content.document?.body?.innerText || '').join('\n').trim() || ''; const chunks = speechChunks(selectedText || epubText);
    if (!chunks.length) { setReaderError('Select text in the book first. EPUB chapters can also be read aloud.'); return; }
    playSpeech(chunks, null);
  };
  const speakPdfPage = (page = pdfPage) => {
    if (!pdfDocument) { setReaderError('The PDF is still opening. Try Read page again in a moment.'); return; }
    if (speaking && speechPage === page) { speak(); return; }
    const token = speechTokenRef.current + 1; speechTokenRef.current = token; window.speechSynthesis?.cancel(); setSpeaking(false); setSpeechPaused(false); setSpeechPage(null); if (page !== pdfPage) changePdfPage(page);
    void pdfDocument.getPage(page).then((entry) => entry.getTextContent()).then((content) => {
      if (token !== speechTokenRef.current) return;
      const chunks = speechChunks(content.items.map((item) => item.str || '').join(' '));
      if (!chunks.length) { setReaderError('This PDF page has no readable text.'); return; }
      playSpeech(chunks, page, page < pdfDocument.numPages ? () => speakPdfPage(page + 1) : undefined);
    }).catch(() => { if (token === speechTokenRef.current) setReaderError('Could not read text from this PDF page.'); });
  };
  const openBook = (book: BookItem) => { const url = new URL(window.location.href); url.searchParams.set('book', book.id); window.history.pushState(null, '', `${url.pathname}${url.search}${url.hash}`); setBookIdFromUrl(book.id); stopSpeech(); setReaderError(''); setReaderMenuOpen(false); setReaderPanel(null); setControlsVisible(true); setSelected(book); };
  const closeReader = () => { const url = new URL(window.location.href); url.searchParams.delete('book'); window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`); setBookIdFromUrl(null); pdfSearchTokenRef.current += 1; stopSpeech(); setReaderMenuOpen(false); setReaderPanel(null); setSelected(null); };
  const changePdfPage = (page: number, scrollTo: 'top' | 'bottom' = 'top') => { if (!selected) return; const next = Math.max(1, Math.min(pdfPages || Number.MAX_SAFE_INTEGER, Math.floor(page))); if (next === pdfPage) return; if (speechPage !== null && next !== speechPage) stopSpeech(); pdfPendingScrollRef.current = scrollTo; setPdfPage(next); saveProgress(selected, `page:${next}`, pdfPages ? next / pdfPages : 0); };
  const turnPage = (direction: 1 | -1) => { setControlsVisible(true); if (isEpub) void (direction > 0 ? renditionRef.current?.next() : renditionRef.current?.prev()); else changePdfPage(pdfPage + direction); };
  const onPdfScroll = (event: UIEvent<HTMLDivElement>) => { const root = event.currentTarget; const current = root.scrollTop; const direction = current - pdfScrollTopRef.current; pdfScrollTopRef.current = current; if (pdfPageTurnLockedRef.current || Math.abs(direction) < 2 || root.scrollHeight <= root.clientHeight + 4) return; if (direction > 0 && current + root.clientHeight >= root.scrollHeight - 28 && (!pdfPages || pdfPage < pdfPages)) { pdfPageTurnLockedRef.current = true; changePdfPage(pdfPage + 1, 'top'); } else if (direction < 0 && current <= 2 && pdfPage > 1) { pdfPageTurnLockedRef.current = true; changePdfPage(pdfPage - 1, 'bottom'); } };
  useEffect(() => { if (!selected || isEpub || !pdfRootRef.current) return undefined; const root = pdfRootRef.current; const touchStart = (event: TouchEvent) => { if (event.touches.length === 2) { const [first, second] = Array.from(event.touches); pdfPinchRef.current = { distance: Math.hypot(first.clientX - second.clientX, first.clientY - second.clientY), scale: typeof pdfZoom === 'number' ? pdfZoom : 1.2 }; return; } const touch = event.changedTouches[0]; if (touch) touchStartRef.current = { x: touch.clientX, y: touch.clientY, scrollLeft: root.scrollLeft, panning: false }; }; const touchMove = (event: TouchEvent) => { const pinch = pdfPinchRef.current; if (pinch && event.touches.length === 2) { const [first, second] = Array.from(event.touches); const distance = Math.hypot(first.clientX - second.clientX, first.clientY - second.clientY); if (!distance) return; event.preventDefault(); setPdfZoom(Math.max(.6, Math.min(2.5, pinch.scale * distance / pinch.distance))); return; } const start = touchStartRef.current; const touch = event.touches[0]; if (!start || !touch || readerMenuOpen || root.scrollWidth <= root.clientWidth + 4) return; const dx = touch.clientX - start.x; const dy = touch.clientY - start.y; if (Math.abs(dx) < 4 || Math.abs(dx) <= Math.abs(dy)) return; event.preventDefault(); start.panning = true; root.scrollLeft = Math.max(0, Math.min(root.scrollWidth - root.clientWidth, start.scrollLeft - dx)); }; const touchEnd = (event: TouchEvent) => { if (pdfPinchRef.current) { if (event.touches.length < 2) pdfPinchRef.current = null; return; } const start = touchStartRef.current; touchStartRef.current = null; const touch = event.changedTouches[0]; if (!start || !touch || readerMenuOpen) return; if (start.panning) { setControlsVisible(true); return; } const dx = touch.clientX - start.x; if (Math.abs(dx) < 56 || Math.abs(touch.clientY - start.y) > Math.abs(dx)) { setControlsVisible(true); return; } setControlsVisible(true); changePdfPage(pdfPage + (dx < 0 ? 1 : -1), dx < 0 ? 'top' : 'bottom'); }; root.addEventListener('touchstart', touchStart, { passive: true }); root.addEventListener('touchmove', touchMove, { passive: false }); root.addEventListener('touchend', touchEnd, { passive: true }); return () => { root.removeEventListener('touchstart', touchStart); root.removeEventListener('touchmove', touchMove); root.removeEventListener('touchend', touchEnd); }; }, [selected, isEpub, pdfPage, pdfPages, pdfZoom, readerMenuOpen]);
  useEffect(() => { if (!selected) return undefined; const onKey = (event: KeyboardEvent) => { setControlsVisible(true); if ((event.target as HTMLElement)?.tagName === 'INPUT') return; if (event.key === 'ArrowLeft') { event.preventDefault(); turnPage(-1); } if (event.key === 'ArrowRight') { event.preventDefault(); turnPage(1); } if (event.key === 'Escape') { setReaderMenuOpen(false); setReaderPanel(null); setControlsVisible(false); } }; window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey); }, [selected, isEpub, pdfPage]);
  useEffect(() => { if (!selected) return undefined; const down = (event: PointerEvent) => { const target = event.target as HTMLElement | null; if (!target?.closest('.books-reader')) return; gestureStart.current = { x: event.clientX, y: event.clientY }; }; const up = (event: PointerEvent) => { const target = event.target as HTMLElement | null; const start = gestureStart.current; gestureStart.current = null; if (!start || !target?.closest('.books-reader') || readerMenuOpen) return; if (!isEpub && event.pointerType === 'touch' && (pdfRootRef.current?.scrollWidth || 0) > (pdfRootRef.current?.clientWidth || 0) + 4) return; const dx = event.clientX - start.x; if (Math.abs(dx) > 52) { turnPage(dx < 0 ? 1 : -1); return; } if (Math.abs(event.clientY - start.y) > 28) return; if (controlsVisible) { setControlsVisible(false); return; } if (event.clientX < window.innerWidth * .25) turnPage(-1); else if (event.clientX > window.innerWidth * .75) turnPage(1); else setControlsVisible(true); }; window.addEventListener('pointerdown', down); window.addEventListener('pointerup', up); return () => { window.removeEventListener('pointerdown', down); window.removeEventListener('pointerup', up); }; }, [selected, controlsVisible, readerMenuOpen, isEpub, pdfPage]);
  const addBookmark = () => { if (!selected) return; const current = isEpub ? progress[selected.id] : { locator: `page:${pdfPage}`, progress: pdfPages ? pdfPage / pdfPages : 0, t: Date.now() }; if (!current?.locator) { setReaderError('Open a page before saving a bookmark.'); return; } const label = isEpub ? `${Math.round(current.progress * 100)}% through` : `Page ${pdfPage}`; const bookEntries = [{ locator: current.locator, label, progress: current.progress, t: Date.now() }, ...(bookmarks[selected.id] || []).filter((entry) => entry.locator !== current.locator)].slice(0, 30); const next = { ...bookmarks, [selected.id]: bookEntries }; setBookmarks(next); writeBookmarks(next); if (user) void saveBookReaderData(selected.id, { bookmarks: bookEntries, notes: notes[selected.id] || [] }).catch(() => undefined); setReaderPanel('bookmarks'); };
  const addNote = () => { if (!selected) return; const text = isEpub ? window.getSelection()?.toString().trim() || renditionRef.current?.getContents?.().map((entry) => entry.document?.getSelection()?.toString().trim() || '').find(Boolean) || '' : pdfNoteDraft.trim(); if (!text) { setReaderError(isEpub ? 'Select text in the open chapter before adding a note.' : 'Write a note for this page first.'); return; } const noteEntries = [{ text: text.slice(0, 1200), progress: isEpub ? progress[selected.id]?.progress || 0 : pdfPages ? pdfPage / pdfPages : 0, t: Date.now() }, ...(notes[selected.id] || [])].slice(0, 50); const next = { ...notes, [selected.id]: noteEntries }; setNotes(next); writeNotes(next); if (user) void saveBookReaderData(selected.id, { bookmarks: bookmarks[selected.id] || [], notes: noteEntries }).catch(() => undefined); setPdfNoteDraft(''); setReaderPanel('notes'); };
  const openPdfSearchMatch = (index: number) => { if (!pdfSearchMatches.length) return; const next = (index + pdfSearchMatches.length) % pdfSearchMatches.length; const page = pdfSearchMatches[next]; setPdfSearchIndex(next); changePdfPage(page); setFindStatus(`${next + 1} of ${pdfSearchMatches.length} matching page${pdfSearchMatches.length === 1 ? '' : 's'} · page ${page}`); };
  const findPdf = () => { const query = findQuery.trim().toLocaleLowerCase(); if (!query) { setFindStatus('Enter text to search this PDF.'); return; } if (!pdfDocument) return; const token = pdfSearchTokenRef.current + 1; pdfSearchTokenRef.current = token; setPdfSearchBusy(true); setPdfSearchMatches([]); setPdfSearchIndex(0); setFindStatus('Searching this PDF…'); void (async () => { const matches: number[] = []; let nextPage = 1; const readPage = async () => { while (nextPage <= pdfDocument.numPages) { const page = nextPage; nextPage += 1; let text = pdfTextCacheRef.current.get(page); if (text === undefined) { const content = await pdfDocument.getPage(page).then((entry) => entry.getTextContent()); text = content.items.map((item) => item.str || '').join(' ').toLocaleLowerCase(); pdfTextCacheRef.current.set(page, text); } if (text.includes(query)) matches.push(page); if (token !== pdfSearchTokenRef.current) return; } }; await Promise.all(Array.from({ length: Math.min(6, pdfDocument.numPages) }, readPage)); if (token !== pdfSearchTokenRef.current) return; matches.sort((left, right) => left - right); if (matches.length) { setPdfSearchMatches(matches); setPdfSearchIndex(0); changePdfPage(matches[0]); setFindStatus(`1 of ${matches.length} matching page${matches.length === 1 ? '' : 's'} · page ${matches[0]}`); } else setFindStatus('No matches in this PDF.'); })().catch(() => { if (token === pdfSearchTokenRef.current) setFindStatus('Unable to search this PDF.'); }).finally(() => { if (token === pdfSearchTokenRef.current) setPdfSearchBusy(false); }); };
  const openEpubSearchMatch = (index: number) => { if (!epubSearchMatches.length) return; const next = (index + epubSearchMatches.length) % epubSearchMatches.length; const match = epubSearchMatches[next]; setEpubSearchIndex(next); void renditionRef.current?.display(match.cfi); setFindStatus(`${next + 1} of ${epubSearchMatches.length} matches${match.excerpt ? ` · ${match.excerpt}` : ''}`); };
  const findInEpub = () => { const query = findQuery.trim(); const book = epubBookRef.current; if (!query) { setFindStatus('Enter text to search this EPUB.'); return; } if (!book?.spine?.each) { setFindStatus('Search is unavailable until this EPUB finishes opening.'); return; } const sections: EpubSection[] = []; book.spine.each((section) => sections.push(section)); if (!sections.length) { setFindStatus('This EPUB does not expose searchable chapters.'); return; } const token = epubSearchTokenRef.current + 1; epubSearchTokenRef.current = token; setEpubSearchBusy(true); setEpubSearchMatches([]); setEpubSearchIndex(0); setFindStatus(`Searching ${sections.length} chapters…`); void (async () => { const matches: EpubSearchMatch[] = []; for (let index = 0; index < sections.length; index += 1) { const section = sections[index]; await section.load(); if (token !== epubSearchTokenRef.current) { section.unload?.(); return; } matches.push(...section.find(query)); section.unload?.(); if ((index + 1) % 8 === 0 && index + 1 < sections.length) setFindStatus(`Searching chapter ${index + 1} of ${sections.length}…`); } if (token !== epubSearchTokenRef.current) return; if (matches.length) { setEpubSearchMatches(matches); setEpubSearchIndex(0); void renditionRef.current?.display(matches[0].cfi); setFindStatus(`1 of ${matches.length} matches${matches[0].excerpt ? ` · ${matches[0].excerpt}` : ''}`); } else setFindStatus('No matches in this EPUB.'); })().catch(() => { if (token === epubSearchTokenRef.current) setFindStatus('Unable to search this EPUB.'); }).finally(() => { if (token === epubSearchTokenRef.current) setEpubSearchBusy(false); }); };
  const renderToc = (entries: EpubTocItem[], depth = 0): ReactNode[] => entries.flatMap((entry) => [<button key={`${depth}-${entry.href}`} type="button" className="books-reader-panel-link" style={{ paddingLeft: `${0.7 + depth * 0.8}rem` }} onClick={() => { void renditionRef.current?.display(entry.href); setReaderPanel(null); }}>{entry.label}</button>, ...renderToc(entry.subitems || [], depth + 1)]);
  const openPdfOutlineItem = (entry: PdfOutlineItem) => { if (!pdfDocument || !entry.dest) return; void (async () => { const destination = typeof entry.dest === 'string' ? await pdfDocument.getDestination?.(entry.dest) : entry.dest; const pageRef = destination?.[0]; if (pageRef === undefined || pageRef === null) return; const pageIndex = typeof pageRef === 'number' ? pageRef : await pdfDocument.getPageIndex?.(pageRef); if (pageIndex === undefined) return; changePdfPage(pageIndex + 1); setReaderPanel(null); })().catch(() => setReaderError('Could not open this outline entry.')); };
  const renderPdfOutline = (entries: PdfOutlineItem[], depth = 0): ReactNode[] => entries.flatMap((entry, index) => [<button key={`${depth}-${index}-${entry.title}`} type="button" className="books-reader-panel-link" style={{ paddingLeft: `${0.7 + depth * 0.8}rem` }} onClick={() => openPdfOutlineItem(entry)}>{entry.title}</button>, ...renderPdfOutline(entry.items || [], depth + 1)]);
  const nearbyPdfPages = () => { const start = Math.max(1, pdfPage - 8); const end = Math.min(pdfPages, pdfPage + 8); return Array.from({ length: Math.max(0, end - start + 1) }, (_, index) => start + index); };
  const bookSummary = (book: BookItem) => { const authors = book.authors.join(', '); const description = book.description.trim(); if (description && description.toLocaleLowerCase() !== authors.toLocaleLowerCase()) return description; return [book.publisher, book.language, book.pageCount ? `${book.pageCount} pages` : '', book.format].filter(Boolean).join(' · ') || authors || 'Book'; };
  const updateEpubPreferences = (next: Partial<EpubPreferences>) => setEpubPreferences((current) => { const updated = { ...current, ...next }; writeEpubPreferences(updated); return updated; });
  const matchesReadingFilter = (book: BookItem) => { const value = progress[book.id]?.progress || 0; if (readingFilter === 'reading') return value > 0 && value < .98; if (readingFilter === 'unread') return value === 0; if (readingFilter === 'finished') return value >= .98; return true; };
  const visibleBooks = items.filter((book) => (formatFilter === 'all' || book.format.toLowerCase() === formatFilter) && matchesReadingFilter(book)).sort((left, right) => {
    if (bookSort === 'title') return left.title.localeCompare(right.title, undefined, { sensitivity: 'base' });
    if (bookSort === 'author') return (left.authors[0] || left.title).localeCompare(right.authors[0] || right.title, undefined, { sensitivity: 'base' });
    if (bookSort === 'progress') return (progress[right.id]?.t || 0) - (progress[left.id]?.t || 0) || left.title.localeCompare(right.title);
    return 0;
  });
  const continueBooks = visibleBooks.filter((book) => { const value = progress[book.id]?.progress || 0; return value > 0 && value < .98; }).sort((left, right) => (progress[right.id]?.t || 0) - (progress[left.id]?.t || 0)).slice(0, 6);
  const bookCard = (book: BookItem) => <article className="book-card" key={book.id} role="button" tabIndex={0} onClick={() => openBook(book)} onKeyDown={(event) => { if (event.currentTarget !== event.target) return; if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openBook(book); } }}><span className="book-cover book-cover-image">{book.coverUrl && <img src={book.coverUrl} alt="" loading="lazy" decoding="async" onError={(event) => { event.currentTarget.hidden = true; }} />}<BookOpenIcon /><small>{book.format}</small></span><div><p className="eyebrow">{book.format} · {book.fileSizeLabel}{progress[book.id]?.progress ? ` · ${Math.round(progress[book.id].progress * 100)}% read` : ''}</p><h2>{book.title}</h2>{book.authors.length > 0 && <p className="book-author">{book.authors.join(', ')}</p>}<p>{bookSummary(book)}</p></div></article>;

  if (selected) return (
    <main className="books-reader-page" onFocusCapture={() => setControlsVisible(true)}>
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
          <Button className="books-reader-chrome-toggle" variant="secondary" size="sm" aria-pressed={keepControlsVisible} onClick={() => setKeepControlsVisible((value) => !value)}>{keepControlsVisible ? 'Controls stay visible' : 'Keep controls visible'}</Button>
          {isEpub ? <>
            <section className="books-reader-appearance" aria-label="Reading appearance"><span>Appearance</span><div className="books-reader-theme-options"><Button className={`books-reader-theme-option${epubPreferences.theme === 'light' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={epubPreferences.theme === 'light'} onClick={() => updateEpubPreferences({ theme: 'light' })}><i className="books-reader-theme-swatch books-reader-theme-light" />Light</Button><Button className={`books-reader-theme-option${epubPreferences.theme === 'sepia' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={epubPreferences.theme === 'sepia'} onClick={() => updateEpubPreferences({ theme: 'sepia' })}><i className="books-reader-theme-swatch books-reader-theme-sepia" />Sepia</Button><Button className={`books-reader-theme-option${epubPreferences.theme === 'dark' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={epubPreferences.theme === 'dark'} onClick={() => updateEpubPreferences({ theme: 'dark' })}><i className="books-reader-theme-swatch books-reader-theme-dark" />Dark</Button></div></section>
            <section className="books-reader-appearance books-reader-type-controls" aria-label="Typography"><span>Typography</span><div className="books-reader-font-size"><Button variant="secondary" size="sm" aria-label="Decrease text size" disabled={epubPreferences.fontSize <= 85} onClick={() => updateEpubPreferences({ fontSize: epubPreferences.fontSize - 5 })}>A−</Button><strong aria-live="polite">{epubPreferences.fontSize}%</strong><Button variant="secondary" size="sm" aria-label="Increase text size" disabled={epubPreferences.fontSize >= 135} onClick={() => updateEpubPreferences({ fontSize: epubPreferences.fontSize + 5 })}>A+</Button></div><div><Button className={`books-reader-theme-option${epubPreferences.fontFamily === 'serif' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={epubPreferences.fontFamily === 'serif'} onClick={() => updateEpubPreferences({ fontFamily: 'serif' })}>Serif</Button><Button className={`books-reader-theme-option${epubPreferences.fontFamily === 'sans' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={epubPreferences.fontFamily === 'sans'} onClick={() => updateEpubPreferences({ fontFamily: 'sans' })}>Sans</Button></div><div><Button className={`books-reader-theme-option${epubPreferences.lineHeight === 'compact' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={epubPreferences.lineHeight === 'compact'} onClick={() => updateEpubPreferences({ lineHeight: 'compact' })}>Compact</Button><Button className={`books-reader-theme-option${epubPreferences.lineHeight === 'relaxed' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={epubPreferences.lineHeight === 'relaxed'} onClick={() => updateEpubPreferences({ lineHeight: 'relaxed' })}>Relaxed</Button></div><div><Button className={`books-reader-theme-option${epubPreferences.margins === 'narrow' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={epubPreferences.margins === 'narrow'} onClick={() => updateEpubPreferences({ margins: 'narrow' })}>Narrow margins</Button><Button className={`books-reader-theme-option${epubPreferences.margins === 'wide' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={epubPreferences.margins === 'wide'} onClick={() => updateEpubPreferences({ margins: 'wide' })}>Wide margins</Button></div></section>
            <p className="books-reader-menu-section-label">Reading tools</p>
            <div className="books-reader-menu-actions">
              <Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'contents' ? null : 'contents')}><BookOpenIcon />Contents</Button>
              <Button variant="secondary" size="sm" onClick={addBookmark}><BookmarkIcon /> Bookmark</Button>
              <Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'bookmarks' ? null : 'bookmarks')}><BookmarkIcon />Bookmarks</Button>
              <Button variant="secondary" size="sm" onClick={addNote}><ListPlusIcon />Add note</Button>
              <Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'notes' ? null : 'notes')}><ListIcon />Notes</Button>
              <Button variant="secondary" size="sm" onClick={speak}>{speaking ? <PauseIcon /> : <VolumeIcon />}{speaking ? (speechPaused ? 'Resume reading' : 'Pause reading') : 'Listen'}</Button>
            </div>
            <label className="books-reader-menu-rate">Reading speed<select value={speechRate} onChange={(event) => setSpeechRate(Number(event.currentTarget.value))}><option value={0.8}>0.8×</option><option value={1}>1×</option><option value={1.25}>1.25×</option><option value={1.5}>1.5×</option></select></label>
            <label className="books-reader-find"><SearchIcon /><input value={findQuery} onChange={(event) => { setFindQuery(event.currentTarget.value); setEpubSearchMatches([]); setEpubSearchIndex(0); }} onKeyDown={(event) => { if (event.key === 'Enter') findInEpub(); }} placeholder="Search this EPUB" /><Button variant="secondary" size="sm" disabled={epubSearchBusy} onClick={findInEpub}>{epubSearchBusy ? 'Searching…' : 'Search'}</Button>{epubSearchMatches.length > 1 && <span className="books-reader-find-results"><Button variant="secondary" size="sm" aria-label="Previous EPUB search result" onClick={() => openEpubSearchMatch(epubSearchIndex - 1)}>Previous</Button><Button variant="secondary" size="sm" aria-label="Next EPUB search result" onClick={() => openEpubSearchMatch(epubSearchIndex + 1)}>Next</Button></span>}{findStatus && <small aria-live="polite">{findStatus}</small>}</label>
          </> : <>
            <section className="books-reader-appearance books-reader-pdf-view" aria-label="PDF view controls"><span>View</span><div><Button className={`books-reader-theme-option${pdfZoom === 'fit' ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={pdfZoom === 'fit'} onClick={() => setPdfZoom('fit')}>Fit width</Button><Button className={`books-reader-theme-option${pdfZoom === 1 ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={pdfZoom === 1} onClick={() => setPdfZoom(1)}>100%</Button><Button className={`books-reader-theme-option${pdfZoom === 1.5 ? ' is-active' : ''}`} variant="secondary" size="sm" aria-pressed={pdfZoom === 1.5} onClick={() => setPdfZoom(1.5)}>150%</Button></div></section>
            <p className="books-reader-menu-section-label">PDF tools</p>
            <div className="books-reader-menu-actions">
              <Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'pages' ? null : 'pages')}><ListIcon />Page thumbnails</Button>
              {pdfOutline.length > 0 && <Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'outline' ? null : 'outline')}><BookOpenIcon />Outline</Button>}
              <Button variant="secondary" size="sm" onClick={addBookmark}><BookmarkIcon />Bookmark page</Button>
              <Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'bookmarks' ? null : 'bookmarks')}><BookmarkIcon />Bookmarks</Button>
              <Button variant="secondary" size="sm" onClick={addNote}><ListPlusIcon />Save note</Button>
              <Button variant="secondary" size="sm" onClick={() => setReaderPanel(readerPanel === 'notes' ? null : 'notes')}><ListIcon />Notes</Button>
            </div>
            <p className="books-reader-menu-section-label">Read aloud</p>
            <div className="books-reader-speech-actions">
              <Button variant="secondary" size="sm" onClick={() => speakPdfPage(pdfPage)}>{speaking && speechPage === pdfPage ? <PauseIcon /> : <VolumeIcon />}{speaking && speechPage === pdfPage ? (speechPaused ? 'Resume page' : 'Pause reading') : 'Read from here'}</Button>
              <Button variant="secondary" size="sm" disabled={pdfPage <= 1} onClick={() => speakPdfPage(pdfPage - 1)}><SkipBackIcon />Previous page</Button>
              <Button variant="secondary" size="sm" disabled={Boolean(pdfPages && pdfPage >= pdfPages)} onClick={() => speakPdfPage(pdfPage + 1)}><SkipForwardIcon />Next page</Button>
            </div>
            {speechPage !== null && <p className="books-reader-speech-status">{speechPaused ? 'Paused' : 'Reading'} page {speechPage}</p>}
            <label className="books-reader-menu-rate">Go to page<input type="number" min="1" max={pdfPages || undefined} value={pdfPage} onChange={(event) => changePdfPage(Number(event.currentTarget.value))} /></label>
            <label className="books-reader-find"><SearchIcon /><input value={findQuery} onChange={(event) => { setFindQuery(event.currentTarget.value); setPdfSearchMatches([]); setPdfSearchIndex(0); }} onKeyDown={(event) => { if (event.key === 'Enter') findPdf(); }} placeholder="Search this PDF" /><Button variant="secondary" size="sm" disabled={pdfSearchBusy} onClick={findPdf}>{pdfSearchBusy ? 'Searching…' : 'Search'}</Button>{pdfSearchMatches.length > 1 && <span className="books-reader-find-results"><Button variant="secondary" size="sm" aria-label="Previous search result" onClick={() => openPdfSearchMatch(pdfSearchIndex - 1)}>Previous</Button><Button variant="secondary" size="sm" aria-label="Next search result" onClick={() => openPdfSearchMatch(pdfSearchIndex + 1)}>Next</Button></span>}{findStatus && <small aria-live="polite">{findStatus}</small>}</label>
            <label className="books-reader-note-draft">Note for page {pdfPage}<textarea value={pdfNoteDraft} maxLength={1200} onChange={(event) => setPdfNoteDraft(event.currentTarget.value)} placeholder="Write a private note" /></label>
            <a className="books-reader-download" href={selected.downloadUrl}><DownloadIcon /> Download original PDF</a>
          </>}
          {readerPanel && isEpub && <section className="books-reader-panel"><strong>{readerPanel === 'contents' ? 'Contents' : readerPanel === 'bookmarks' ? 'Bookmarks' : 'Notes'}</strong>{readerPanel === 'contents' ? (toc.length ? renderToc(toc) : <small>Contents unavailable for this EPUB.</small>) : readerPanel === 'bookmarks' ? ((bookmarks[selected.id] || []).length ? (bookmarks[selected.id] || []).map((entry) => <button key={entry.t} type="button" className="books-reader-panel-link" onClick={() => { void renditionRef.current?.display(entry.locator); setReaderPanel(null); }}>{entry.label}</button>) : <small>No bookmarks yet.</small>) : ((notes[selected.id] || []).length ? (notes[selected.id] || []).map((entry) => <p key={entry.t} className="books-reader-note">{entry.text}</p>) : <small>Select text and choose Add note to save a highlight.</small>)}</section>}
          {readerPanel && !isEpub && <section className="books-reader-panel"><strong>{readerPanel === 'pages' ? 'Pages' : readerPanel === 'outline' ? 'Outline' : readerPanel === 'bookmarks' ? 'Bookmarks' : 'Notes'}</strong>{readerPanel === 'pages' ? <div className="pdf-thumbnail-grid">{nearbyPdfPages().map((page) => pdfDocument && <PdfThumbnail key={page} document={pdfDocument} pageNumber={page} selected={page === pdfPage} onSelect={(next) => { changePdfPage(next); setReaderPanel(null); }} />)}</div> : readerPanel === 'outline' ? (pdfOutline.length ? renderPdfOutline(pdfOutline) : <small>This PDF does not include an outline.</small>) : readerPanel === 'bookmarks' ? ((bookmarks[selected.id] || []).length ? (bookmarks[selected.id] || []).map((entry) => <button key={entry.t} type="button" className="books-reader-panel-link" onClick={() => { const page = Number(entry.locator.replace(/^page:/, '')); if (page) changePdfPage(page); setReaderPanel(null); }}>{entry.label}</button>) : <small>No bookmarks yet.</small>) : ((notes[selected.id] || []).length ? (notes[selected.id] || []).map((entry) => <p key={entry.t} className="books-reader-note"><small>Page {Math.max(1, Math.round(entry.progress * (pdfPages || 1)))}</small>{entry.text}</p>) : <small>No notes yet. Write one above and choose Save note.</small>)}</section>}
        </aside>}

        {readerLoading && <p className="books-reader-loading" role="status">Opening {isEpub ? 'EPUB' : 'PDF'}…</p>}
        {readerError && <p className="books-reader-error" role="alert">{readerError}</p>}
        <div className="books-reader">
          {isEpub ? <div ref={epubRootRef} className={`epub-reader epub-reader-${epubPreferences.theme}`} aria-label={`${selected.title} reader`} /> : <div ref={pdfRootRef} className="pdf-reader" onScroll={onPdfScroll}><canvas ref={pdfCanvasRef} aria-label={`${selected.title} PDF page ${pdfPage}`} /></div>}
        </div>
        <p className="books-reader-reveal-hint" aria-hidden="true">Tap the centre for controls · swipe or tap an edge to turn pages</p>
        {!isEpub && <nav className="books-reader-pagination" aria-label="PDF page navigation">
          <Button variant="ghost" size="sm" onClick={() => changePdfPage(pdfPage - 1)} disabled={pdfPage <= 1}>Previous</Button>
          <button type="button" className="books-reader-page-indicator" onClick={() => { setControlsVisible(true); setReaderMenuOpen(true); }}>Page {pdfPage}{pdfPages ? ` / ${pdfPages}` : ''}</button>
          <Button variant="ghost" size="sm" onClick={() => changePdfPage(pdfPage + 1)} disabled={Boolean(pdfPages && pdfPage >= pdfPages)}>Next</Button>
        </nav>}
        <footer className="books-reader-footer"><span>{isEpub && progress[selected.id]?.progress ? `${Math.round(progress[selected.id].progress * 100)}% read` : isEpub ? 'Swipe or tap the edge to turn pages' : `Page ${pdfPage}${pdfPages ? ` of ${pdfPages}` : ''} saved`}{sessionMinutes ? ` · ${sessionMinutes} min this session` : ''}</span><span>{user ? (isEpub ? 'Your place, bookmarks, and notes sync to your library account.' : 'Tap the page number for more options.') : 'Saved on this device. Sign in to sync across devices.'}</span></footer>
      </section>
    </main>
  );
  return <main className="hub-main books-page"><section className="books-hero"><div><p className="eyebrow">Library books</p><h1>Your reading library.</h1><p>Browse and read available PDFs and EPUBs from one comfortable place.</p></div></section><div className="books-library-controls"><label className="books-search"><SearchIcon /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search books" /></label><div className="books-filter-row" aria-label="Book library controls"><label>Format<select value={formatFilter} onChange={(event) => setFormatFilter(event.target.value as BookFormatFilter)}><option value="all">All formats</option><option value="pdf">PDF</option><option value="epub">EPUB</option></select></label><label>Reading<select value={readingFilter} onChange={(event) => setReadingFilter(event.target.value as BookReadingFilter)}><option value="all">All books</option><option value="reading">In progress</option><option value="unread">Unread</option><option value="finished">Finished</option></select></label><label>Sort<select value={bookSort} onChange={(event) => setBookSort(event.target.value as BookSort)}><option value="added">Recently added</option><option value="progress">Recently read</option><option value="title">Title</option><option value="author">Author</option></select></label></div></div>{loading ? <p className="books-state">Loading books…</p> : error ? <p className="books-reader-error">{error}</p> : items.length ? <>{continueBooks.length > 0 && <section className="books-continue" aria-labelledby="continue-reading-title"><div><p className="eyebrow">Pick up where you left off</p><h2 id="continue-reading-title">Continue reading</h2></div><div className="books-continue-grid">{continueBooks.map(bookCard)}</div></section>}{visibleBooks.length ? <section aria-label="Books"><div className="books-library-heading"><h2>{continueBooks.length ? 'All books' : 'Books'}</h2><span>{visibleBooks.length} {visibleBooks.length === 1 ? 'book' : 'books'}</span></div><div className="books-grid">{visibleBooks.map(bookCard)}</div></section> : <section className="books-dropzone books-empty"><BookOpenIcon /><strong>No books match these filters</strong><span>Try another format, reading state, or sort order.</span><Button variant="secondary" size="sm" onClick={() => { setFormatFilter('all'); setReadingFilter('all'); setBookSort('added'); }}>Reset filters</Button></section>}</> : <section className="books-dropzone books-empty"><BookOpenIcon /><strong>{query.trim() ? `No books match “${query.trim()}”` : 'No books in your library yet'}</strong><span>{query.trim() ? 'Try another title, author, or filename.' : 'Check back soon for new titles to read.'}</span>{query.trim() && <Button variant="secondary" size="sm" onClick={() => setQuery('')}>Clear search</Button>}</section>}</main>;
}
