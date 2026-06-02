import { useEffect, useState } from 'react';

/** Convert [0-255] RGB to [0-360, 0-1, 0-1] HSL. */
function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  const nr = r / 255, ng = g / 255, nb = b / 255;
  const max = Math.max(nr, ng, nb), min = Math.min(nr, ng, nb);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = 0;
  if (max === nr) h = ((ng - nb) / d + (ng < nb ? 6 : 0)) / 6;
  else if (max === ng) h = ((nb - nr) / d + 2) / 6;
  else h = ((nr - ng) / d + 4) / 6;
  return [h * 360, s, l];
}

const SAMPLE_SIZE = 48;

/** Extract the dominant vibrant colour from an image URL.
 *  Returns [r, g, b] in 0-255 range, or null if the image can't be sampled
 *  (e.g. CORS restriction, not yet loaded).
 */
function extractColor(img: HTMLImageElement): [number, number, number] | null {
  try {
    const canvas = document.createElement('canvas');
    canvas.width = SAMPLE_SIZE;
    canvas.height = SAMPLE_SIZE;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, SAMPLE_SIZE, SAMPLE_SIZE);
    const { data } = ctx.getImageData(0, 0, SAMPLE_SIZE, SAMPLE_SIZE);

    // Bucket pixels by hue (36 buckets × 10°) and track best vibrant sample
    // per bucket (highest saturation × (1 - |lightness - 0.45|)).
    const bucketCounts = new Uint32Array(36);
    const bestR = new Uint8Array(36);
    const bestG = new Uint8Array(36);
    const bestB = new Uint8Array(36);
    const bestScore = new Float32Array(36);

    for (let i = 0; i < data.length; i += 4) {
      const r = data[i], g = data[i + 1], b = data[i + 2];
      const [h, s, l] = rgbToHsl(r, g, b);
      // Discard near-black, near-white, and desaturated pixels.
      if (s < 0.18 || l < 0.10 || l > 0.90) continue;
      const bucket = Math.min(35, Math.floor(h / 10));
      bucketCounts[bucket]++;
      const score = s * (1 - Math.abs(l - 0.45) * 1.6);
      if (score > bestScore[bucket]) {
        bestScore[bucket] = score;
        bestR[bucket] = r;
        bestG[bucket] = g;
        bestB[bucket] = b;
      }
    }

    // Find the most populous bucket; break ties by vibrance score.
    let dominant = -1;
    let maxCount = 0;
    for (let i = 0; i < 36; i++) {
      if (bucketCounts[i] > maxCount) { maxCount = bucketCounts[i]; dominant = i; }
    }
    if (dominant < 0 || maxCount < 4) return null;

    return [bestR[dominant], bestG[dominant], bestB[dominant]];
  } catch {
    // SecurityError from a CORS-restricted image — silently skip.
    return null;
  }
}

/** React hook: extracts dominant vibrant colour from `url`.
 *  Returns a stable [r, g, b] tuple that updates whenever the URL changes.
 */
export function useArtColor(url: string | null | undefined): [number, number, number] | null {
  const [color, setColor] = useState<[number, number, number] | null>(null);

  useEffect(() => {
    if (!url) { setColor(null); return; }
    let cancelled = false;
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      if (cancelled) return;
      setColor(extractColor(img));
    };
    img.onerror = () => { if (!cancelled) setColor(null); };
    img.src = url;
    return () => { cancelled = true; };
  }, [url]);

  return color;
}
