import { useCallback, useMemo, useRef, useState } from 'react';
import { unzip as fflateUnzip } from 'fflate';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogTitle } from './ui/dialog';
import { Progress } from './ui/progress';
import { importPhotoFile } from '../api';

// Takeout media extensions we accept into the vault (same allowlist shape as
// the server: images and videos only).
const MEDIA_RE = /\.(jpe?g|png|heic|webp|avi|mov|mp4)$/i;
// Takeout sidecars sit next to their media: "IMG_0001.jpg" + "IMG_0001.json".
const JSON_RE = /\.json$/i;
// Takeout dedupes edits as "<name>-edited" / "<name>-original"; those ARE
// uploaded (the user trashes unwanted variants later), but a plain re-import
// of the same basename is skipped client-side.
const BASE_RE = /\s*\(\d+\)$/;

/** Strip " (1)"-style duplicate suffixes and the extension → dedup key. */
function baseKey(name: string): string {
  const stem = name.replace(JSON_RE, '').replace(MEDIA_RE, '');
  return stem.replace(BASE_RE, '').toLowerCase();
}

/** Archive folder name under "Takeout/Google Photos/" → album name. */
function albumFromPath(path: string): string {
  const m = path.match(/^Takeout\/Google Photos\/([^/]+)\//i);
  return m ? m[1] : 'Google Takeout';
}

function guessMime(name: string): string {
  const ext = (name.split('.').pop() || '').toLowerCase();
  const byExt: Record<string, string> = {
    jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png',
    heic: 'image/heic', webp: 'image/webp',
    avi: 'video/avi', mov: 'video/quicktime', mp4: 'video/mp4',
  };
  return byExt[ext] ?? 'application/octet-stream';
}

/** All zip entries as [name, bytes], via fflate's documented callback API.
 *  Takeout zips are processed one at a time; only one archive's entries are
 *  held at once. */
function unzipEntries(buf: Uint8Array): Promise<Array<[string, Uint8Array]>> {
  return new Promise((resolve, reject) => {
    fflateUnzip(buf, (err, entries) => {
      if (err) reject(err);
      else resolve(Object.entries(entries).map(([name, file]) => [name, file] as [string, Uint8Array]));
    });
  });
}

/** fflate hands back ArrayBufferLike views; `new File` needs an
 *  ArrayBuffer-backed view (SharedArrayBuffer can't back a Blob). */
function asBlobPart(data: Uint8Array): BlobPart {
  return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
}

export interface ImportSummary {
  imported: number;
  skipped: number;
  failed: number;
  total: number;
}

type Step = 'pick' | 'importing' | 'done';

export default function PhotoImportWizard({
  open,
  onOpenChange,
  onFinished,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onFinished: () => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [step, setStep] = useState<Step>('pick');
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    setFiles([]);
    setStep('pick');
    setProgress({ done: 0, total: 0 });
    setSummary(null);
    setError('');
  }, []);

  const close = (next: boolean) => {
    if (!next) cancelRef.current?.abort();
    onOpenChange(next);
    if (!next) reset();
  };

  const runImport = useCallback(async () => {
    if (!files.length) return;
    setStep('importing');
    setError('');
    const abort = new AbortController();
    cancelRef.current = abort;
    // Pass 1: count media entries (progress denominator) and index sidecar
    // timestamps keyed by media basename (Takeout dedup suffixes stripped).
    let total = 0;
    const takenByBase = new Map<string, string>();
    try {
      for (const zip of files) {
        const buf = new Uint8Array(await zip.arrayBuffer());
        for (const [name, data] of await unzipEntries(buf)) {
          if (JSON_RE.test(name)) {
            try {
              const sidecar = JSON.parse(new TextDecoder().decode(data));
              const ts = sidecar?.photoTakenTime?.timestamp;
              if (ts) takenByBase.set(baseKey(name), String(ts));
            } catch { /* malformed sidecar — EXIF/_now fallback applies */ }
          } else if (MEDIA_RE.test(name)) {
            total += 1;
          }
        }
      }
    } catch {
      setError('Could not read one of the zip files.');
      setStep('pick');
      return;
    }
    setProgress({ done: 0, total });
    const posted = new Set<string>();
    let imported = 0;
    let skipped = 0;
    let failed = 0;
    // Pass 2: stream entries and upload each media file with its metadata.
    for (const zip of files) {
      if (abort.signal.aborted) break;
      try {
        const buf = new Uint8Array(await zip.arrayBuffer());
        for (const [name, data] of await unzipEntries(buf)) {
          if (abort.signal.aborted) break;
          if (!MEDIA_RE.test(name)) continue;
          const base = baseKey(name);
          const shortName = name.split('/').pop() || name;
          if (posted.has(base)) {
            skipped += 1;
          } else {
            posted.add(base);
            const ts = takenByBase.get(base);
            try {
              await importPhotoFile(
                new File([asBlobPart(data)], shortName, { type: guessMime(shortName) }),
                {
                  takenAt: ts ? new Date(Number(ts) * 1000).toISOString() : '',
                  albumName: albumFromPath(name),
                  importTotal: total,
                  signal: abort.signal,
                },
              );
              imported += 1;
            } catch (err) {
              if (abort.signal.aborted) break;
              failed += 1;
              setError(String((err as Error)?.message || err));
            }
          }
          setProgress((p) => ({ ...p, done: p.done + 1 }));
        }
      } catch {
        failed += 1;
        setError(`Could not read ${zip.name}`);
      }
    }
    cancelRef.current = null;
    setSummary({ imported, skipped, failed, total });
    setStep('done');
    onFinished();
  }, [files, onFinished]);

  const percent = useMemo(
    () => (progress.total ? Math.round((progress.done / progress.total) * 100) : 0),
    [progress],
  );

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="photos-import-wizard">
        <DialogTitle>Import Google Takeout</DialogTitle>
        {step === 'pick' && (
          <div className="photos-import-wizard__pick">
            <p>
              Pick one or more <code>Takeout</code> zip files. Each photo/video is uploaded with
              its original capture time and filed into an album named after its Takeout folder.
            </p>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".zip"
              hidden
              onChange={(event) => {
                setFiles(Array.from(event.target.files ?? []));
                event.target.value = '';
              }}
            />
            <div className="photos-import-wizard__actions">
              <Button variant="secondary" onClick={() => inputRef.current?.click()}>
                Choose zip files
              </Button>
              <Button onClick={() => void runImport()} disabled={!files.length}>
                Import {files.length ? `(${files.length} file${files.length === 1 ? '' : 's'})` : ''}
              </Button>
            </div>
            {files.length > 0 && (
              <ul className="photos-import-wizard__files">
                {files.map((f) => <li key={f.name}>{f.name}</li>)}
              </ul>
            )}
            {error && <p className="photos-import-wizard__error" role="alert">{error}</p>}
          </div>
        )}
        {step === 'importing' && (
          <div className="photos-import-wizard__progress">
            <p>Importing… {progress.done} / {progress.total}</p>
            <Progress value={percent} />
            <Button
              variant="secondary"
              onClick={() => cancelRef.current?.abort()}
              className="photos-import-wizard__cancel"
            >
              Cancel
            </Button>
          </div>
        )}
        {step === 'done' && summary && (
          <div className="photos-import-wizard__summary">
            <p>
              Imported {summary.imported} of {summary.total}
              {summary.skipped > 0 && <> · {summary.skipped} duplicates skipped</>}
              {summary.failed > 0 && <> · {summary.failed} failed</>}
              .
            </p>
            {error && <p className="photos-import-wizard__error" role="alert">{error}</p>}
            <Button onClick={() => close(false)}>Done</Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
