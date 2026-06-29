/** Dedup and join metadata parts, ignoring blank/repeated values (case-insensitive). */
export function joinMetadata(parts: Array<string | number | null | undefined>): string {
  return uniqueMetadataParts(parts).join(' - ');
}

/** Same dedup logic as joinMetadata but returns an array instead of a joined string. */
export function uniqueMetadataParts(parts: Array<string | number | null | undefined>): string[] {
  const seen = new Set<string>();
  return parts
    .map((part) => String(part || '').trim())
    .filter(Boolean)
    .filter((part) => {
      const key = part.toLocaleLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}
