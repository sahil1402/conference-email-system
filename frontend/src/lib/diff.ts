/**
 * Lightweight word-level text diff (LCS-based) — no external dependency.
 *
 * Tokenizes both strings into words + whitespace, computes the longest common
 * subsequence, and emits a flat list of ops (equal / added / removed) with
 * adjacent same-type tokens coalesced. Inputs here are short reply drafts, so
 * the O(n·m) table is negligible.
 */

export type DiffOpType = "equal" | "added" | "removed";

export interface DiffOp {
  type: DiffOpType;
  value: string;
}

/** Split into word and whitespace tokens, preserving separators for reassembly. */
function tokenize(text: string): string[] {
  if (!text) return [];
  return text.split(/(\s+)/).filter((t) => t.length > 0);
}

/**
 * Word-level diff of ``original`` → ``edited``.
 * `removed` tokens exist only in the original; `added` only in the edited text.
 */
export function wordDiff(original: string, edited: string): DiffOp[] {
  const a = tokenize(original);
  const b = tokenize(edited);
  const n = a.length;
  const m = b.length;

  // LCS length table.
  const lcs: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0)
  );
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] =
        a[i] === b[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  // Backtrack into raw ops.
  const raw: DiffOp[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      raw.push({ type: "equal", value: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      raw.push({ type: "removed", value: a[i] });
      i++;
    } else {
      raw.push({ type: "added", value: b[j] });
      j++;
    }
  }
  while (i < n) raw.push({ type: "removed", value: a[i++] });
  while (j < m) raw.push({ type: "added", value: b[j++] });

  // Coalesce adjacent ops of the same type.
  const merged: DiffOp[] = [];
  for (const op of raw) {
    const last = merged[merged.length - 1];
    if (last && last.type === op.type) last.value += op.value;
    else merged.push({ ...op });
  }
  return merged;
}

/** True when the two texts differ (ignoring only leading/trailing whitespace). */
export function hasMeaningfulDiff(original: string, edited: string): boolean {
  return (original ?? "").trim() !== (edited ?? "").trim();
}
