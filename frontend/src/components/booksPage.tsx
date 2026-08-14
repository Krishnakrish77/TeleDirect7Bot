import { useEffect, useRef, useState } from 'react';
import { fetchBooks } from '../api';
import { BookOpenIcon, DownloadIcon, PauseIcon, SearchIcon, VolumeIcon, XIcon } from '../icons';
import type { BookItem } from '../types';
import { Button } from './ui/button';

declare global { interface Window { ePub?: (source: string) => EpubBook; } }
type EpubRendition = { display: (target?: string | number) => Promise<unknown>; next: () => Promise<unknown>; prev: () => Promise<unknown>; getContents?: () => Array<{ document?: Document }>; destroy?: () => void; };
type EpubBook = { renderTo: (element: HTMLElement, options: Record<string, unknown>) => EpubRendition; destroy?: () => void; };
const EPUB_SCRIPT = 'https://cdn.jsdelivr.net/npm/epubjs@0.3.93/dist/epub.min.js';

function loadEpubReader(): Promise<void> {
  if (window.ePub) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement('script'); script.src = EPUB_SCRIPT; script.async = true;
    script.onload = () => window.ePub ? resolve() : reject(new Error('The EPUB reader did not start.'));
    script.onerror = () => reject(new Error('Unable to load the EPUB reader. Check your connection and try again.'));
    document.head.appendChild(script);
  });
}

export function BooksPage() {
  const [items, setItems] = useState<BookItem[]>([]); const [query, setQuery] = useState(''); const [selected, setSelected] = useState<BookItem | null>(null);
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [readerError, setReaderError] = useState(''); const [speaking, setSpeaking] = useState(false);
  const epubRootRef = useRef<HTMLDivElement>(null); const renditionRef = useRef<EpubRendition | null>(null);
  const isEpub = selected?.format.toLowerCase() === 'epub';

  useEffect(() => { const controller = new AbortController(); setLoading(true); setError(''); void fetchBooks(query, controller.signal).then((data) => setItems(data.items)).catch((err) => { if (err.name !== 'AbortError') setError(err.message || 'Unable to load books.'); }).finally(() => setLoading(false)); return () => controller.abort(); }, [query]);
  useEffect(() => { if (!selected || !isEpub || !epubRootRef.current) return undefined; let cancelled = false; let book: EpubBook | null = null; setReaderError(''); void loadEpubReader().then(() => { if (cancelled || !window.ePub || !epubRootRef.current) return; epubRootRef.current.replaceChildren(); book = window.ePub(selected.readUrl); renditionRef.current = book.renderTo(epubRootRef.current, { width: '100%', height: '100%', spread: 'none' }); return renditionRef.current.display(); }).catch((err: unknown) => { if (!cancelled) setReaderError(err instanceof Error ? err.message : 'This EPUB could not be opened.'); }); return () => { cancelled = true; renditionRef.current?.destroy?.(); renditionRef.current = null; book?.destroy?.(); }; }, [isEpub, selected]);
  useEffect(() => () => window.speechSynthesis?.cancel(), []);

  const speak = () => { if (!('speechSynthesis' in window)) { setReaderError('Read aloud is not available in this browser.'); return; } if (speaking) { window.speechSynthesis.cancel(); setSpeaking(false); return; } const selectedText = window.getSelection()?.toString().trim() || ''; const epubText = renditionRef.current?.getContents?.().map((c) => c.document?.body?.innerText || '').join('\n').trim() || ''; const source = selectedText || epubText; if (!source) { setReaderError('Select text in the book first. EPUB text can also be read aloud by chapter.'); return; } const utterance = new SpeechSynthesisUtterance(source.slice(0, 12000)); utterance.onend = utterance.onerror = () => setSpeaking(false); setSpeaking(true); window.speechSynthesis.speak(utterance); };

  if (selected) return <main className="hub-main books-page"><section className="books-reader-shell"><header className="books-reader-toolbar"><div className="books-file-label"><BookOpenIcon /><span><strong>{selected.title}</strong><small>{selected.fileName} · {selected.fileSizeLabel}</small></span></div><div className="books-reader-actions">{isEpub && <><Button variant="secondary" size="sm" onClick={() => void renditionRef.current?.prev()}>Previous</Button><Button variant="secondary" size="sm" onClick={() => void renditionRef.current?.next()}>Next</Button><Button variant="secondary" size="sm" onClick={speak}>{speaking ? <PauseIcon /> : <VolumeIcon />}{speaking ? 'Stop reading' : 'Read aloud'}</Button></>}<a href={selected.downloadUrl}><Button variant="secondary" size="sm"><DownloadIcon /> Download</Button></a><Button variant="ghost" size="icon-sm" onClick={() => { window.speechSynthesis?.cancel(); setSpeaking(false); setSelected(null); }} aria-label="Close book"><XIcon /></Button></div></header>{readerError && <p className="books-reader-error" role="alert">{readerError}</p>}<div className="books-reader">{isEpub ? <div ref={epubRootRef} className="epub-reader" aria-label={`${selected.title} reader`} /> : <iframe title={selected.title} src={selected.readUrl} />}</div>{isEpub && <p className="books-reader-note"><VolumeIcon /> Read aloud uses your device voice. Downloadable audiobook conversion will need a server-side TTS provider.</p>}</section></main>;
  return <main className="hub-main books-page"><section className="books-hero"><div><p className="eyebrow">Library books</p><h1>Your reading library.</h1><p>Browse and read available PDFs and EPUBs from one comfortable place.</p></div></section><label className="books-search"><SearchIcon /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search books" /></label>{loading ? <p className="books-state">Loading books…</p> : error ? <p className="books-reader-error">{error}</p> : items.length ? <div className="books-grid">{items.map((book) => <article className="book-card" key={book.id}><span className="book-cover"><BookOpenIcon /><small>{book.format}</small></span><div><p className="eyebrow">{book.format} · {book.fileSizeLabel}</p><h2>{book.title}</h2><p>{book.description || book.fileName}</p><Button size="sm" onClick={() => setSelected(book)}>Read book</Button></div></article>)}</div> : <section className="books-dropzone books-empty"><BookOpenIcon /><strong>No books in your library yet</strong><span>Check back soon for new titles to read.</span></section>}</main>;
}
