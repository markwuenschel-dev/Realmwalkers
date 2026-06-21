// Diff-screen mapper: two prose strings → the side-by-side DiffRowData[] the screen renders.
import { lineDiff } from "../../legacy/lib/diff";
import type { DiffRowData } from "../types";

// lineDiff emits a flat stream of same/add/del ops. The desk renders aligned left/right rows and
// treats a removed line immediately followed by an added line as a single "change" (old → new). So
// we fold each adjacent del+add pair into one `change` row and pass everything else through.
export function diffRows(oldProse: string, newProse: string): DiffRowData[] {
  const ops = lineDiff(oldProse, newProse);
  const rows: DiffRowData[] = [];
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i];
    const next = ops[i + 1];
    if (op.type === "del" && next && next.type === "add") {
      rows.push({ type: "change", l: op.text, r: next.text });
      i++; // consume the paired add
    } else if (op.type === "same") {
      rows.push({ type: "same", l: op.text, r: op.text });
    } else if (op.type === "add") {
      rows.push({ type: "add", l: "", r: op.text });
    } else {
      rows.push({ type: "del", l: op.text, r: "" });
    }
  }
  return rows;
}
