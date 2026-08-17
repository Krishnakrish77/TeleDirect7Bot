// @vitest-environment node

import { readFile } from 'node:fs/promises';
import { describe, expect, it, vi } from 'vitest';
import * as pdfjs from 'pdfjs-dist/legacy/build/pdf.mjs';

const wasmUrl = new URL('../../node_modules/pdfjs-dist/wasm/', import.meta.url).href;
const fixtures = ['pdfjs-bug-jpx.pdf', 'pdfjs-jpx-smask.pdf'];

describe('PDF JPEG-2000 decoder', () => {
  it.each(fixtures)('decodes %s with the bundled OpenJPEG WebAssembly asset', async (fixture) => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const data = new Uint8Array(await readFile(new URL(`./fixtures/${fixture}`, import.meta.url)));
    const task = pdfjs.getDocument({ data, wasmUrl });

    try {
      const document = await task.promise;
      const page = await document.getPage(1);
      const operators = await page.getOperatorList();
      expect(document.numPages).toBeGreaterThan(0);
      expect(operators.fnArray.length).toBeGreaterThan(0);
      expect(warning).not.toHaveBeenCalled();
    } finally {
      await task.destroy();
      warning.mockRestore();
    }
  });
});
