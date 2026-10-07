// Preserve the previous deploy's hashed chunks around `vite build`.
//
// `emptyOutDir: true` deletes every old chunk, but clients mid-session (or on
// a stale service-worker shell) still reference those exact URLs; a 404 on a
// dynamic import dead-ends the route with no recovery. A prebuild hook
// snapshots the current asset list, and a postbuild hook re-copies files that
// the new build no longer produces from a stash kept OUTSIDE the output dir.
// Stale entries drop out of the stash naturally: each generation rewrites the
// stash from the live output, so kept files survive exactly one generation —
// long enough for every open client to have reloaded.
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, statSync, copyFileSync, rmSync } from 'node:fs';
import { join, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const outDir = join(root, '..', 'main', 'server', 'static', 'app');
// The stash must survive `emptyOutDir`, so it lives outside static/app.
const stashDir = join(root, '.vite-asset-stash');
const ledgerPath = join(stashDir, 'ledger.json');

function listFiles(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    return e.isDirectory() ? listFiles(p) : [p];
  });
}

const mode = process.argv[2];

if (mode === 'snapshot') {
  // prebuild: stash current assets + their relative paths; drop the old stash.
  // Read the ledger BEFORE rmSync — the ledger lives inside the stash dir, and
  // deleting first left prevAges empty (ages never advanced past 1).
  const prevLedger = existsSync(ledgerPath)
    ? JSON.parse(readFileSync(ledgerPath, 'utf8'))
    : { files: [] };
  rmSync(stashDir, { recursive: true, force: true });
  mkdirSync(stashDir, { recursive: true });
  const files = listFiles(outDir).filter((p) => !p.endsWith('kept-assets.json'));
  for (const p of files) {
    const dest = join(stashDir, relative(outDir, p));
    mkdirSync(dirname(dest), { recursive: true });
    copyFileSync(p, dest);
  }
  // age = how many consecutive builds a file has been restored without the
  // current build emitting it. Files the live output still emits reset to 0.
  const prevAges = new Map(prevLedger.files.map((f) => [f.path, f.age ?? 0]));
  writeFileSync(ledgerPath, JSON.stringify({
    files: files.map((p) => {
      const rel = relative(outDir, p);
      return { path: rel, age: prevAges.get(rel) ?? 0 };
    }),
  }));
  console.log(`keep-assets: stashed ${files.length} files from the previous build`);
} else if (mode === 'restore') {
  // postbuild: the fresh build is in place. Re-add stashed files that the new
  // build no longer emits (same relative path = same content, since hashed).
  // age cap: a file no client can still be loading (every open tab has
  // re-fetched the new shell after ~one reload cycle) is dropped for good.
  const MAX_AGE = 2;
  if (!existsSync(ledgerPath)) {
    console.log('keep-assets: no stash, nothing to restore');
    process.exit(0);
  }
  const { files } = JSON.parse(readFileSync(ledgerPath, 'utf8'));
  const current = new Set(listFiles(outDir).map((p) => relative(outDir, p)));
  let restored = 0;
  const survivors = [];
  for (const entry of files) {
    const rel = typeof entry === 'string' ? entry : entry.path;
    if (current.has(rel)) continue; // new build still emits it
    const age = (typeof entry === 'string' ? 0 : entry.age ?? 0) + 1;
    if (age > MAX_AGE) continue; // stale beyond any plausible open client
    const src = join(stashDir, rel);
    const dest = join(outDir, rel);
    mkdirSync(dirname(dest), { recursive: true });
    copyFileSync(src, dest);
    survivors.push({ path: rel, age });
    restored += 1;
  }
  console.log(`keep-assets: restored ${restored} previous-generation chunk(s)`);
  // Refresh the stash to the NEW generation. Restored orphans are back in the
  // live output but must keep their surviving age (not reset to 0), or the
  // ledger grows duplicate entries that never reach the age cap.
  const ageByPath = new Map(survivors.map((s) => [s.path, s.age]));
  rmSync(stashDir, { recursive: true, force: true });
  mkdirSync(stashDir, { recursive: true });
  const fresh = listFiles(outDir);
  for (const p of fresh) {
    const dest = join(stashDir, relative(outDir, p));
    mkdirSync(dirname(dest), { recursive: true });
    copyFileSync(p, dest);
  }
  writeFileSync(ledgerPath, JSON.stringify({
    files: fresh.map((p) => {
      const rel = relative(outDir, p);
      return { path: rel, age: ageByPath.get(rel) ?? 0 };
    }),
  }));
} else {
  console.error('usage: node keep-assets.mjs snapshot|restore');
  process.exit(1);
}
