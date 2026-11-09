// Search excerpts and line ranges of the original content. No dependencies: runs under `node --test`.

export interface Piece {
  text: string;
  highlight: boolean;
}

/** Splits a search excerpt at the highlight markers (control characters, never HTML). */
export function splitExcerpt(excerpt: string, start = "\u0002", end = "\u0003"): Piece[] {
  const pieces: Piece[] = [];
  let highlight = false;
  let current = "";
  for (const character of excerpt) {
    if (character === start || character === end) {
      if (current) pieces.push({ text: current, highlight });
      current = "";
      highlight = character === start;
      continue;
    }
    current += character;
  }
  if (current) pieces.push({ text: current, highlight });
  return pieces;
}

export interface ChunkRange {
  ordinal: number;
  line_start: number;
  line_end: number;
}

/** For each (1-based) line of the document, the ordinal of the chunk that contains it, or null. */
export function chunkByLine(totalLines: number, chunks: ChunkRange[]): (number | null)[] {
  const map: (number | null)[] = new Array(totalLines + 1).fill(null);
  for (const c of chunks) {
    for (let line = c.line_start; line <= Math.min(c.line_end, totalLines); line++) map[line] = c.ordinal;
  }
  return map;
}

export function linesOf(content: string): string[] {
  const lines = content.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}
