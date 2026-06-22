// Minimal line-based diff (LCS), no dependencies. Used by the Versions screen.
// "del" = line in `a` (older) not in `b`; "add" = line in `b` (newer) not in `a`; "same" = unchanged.
export type DiffOp = { type: "same" | "add" | "del"; text: string };

export function lineDiff(a: string, b: string): DiffOp[] {
  const left = a.split("\n");
  const right = b.split("\n");
  const n = left.length;
  const m = right.length;

  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] =
        left[i] === right[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const ops: DiffOp[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (left[i] === right[j]) {
      ops.push({ type: "same", text: left[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      ops.push({ type: "del", text: left[i] });
      i++;
    } else {
      ops.push({ type: "add", text: right[j] });
      j++;
    }
  }
  while (i < n) ops.push({ type: "del", text: left[i++] });
  while (j < m) ops.push({ type: "add", text: right[j++] });
  return ops;
}
