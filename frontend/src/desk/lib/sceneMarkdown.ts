// Export a single proposed scene as Markdown — the prose as proposed, followed by every piece of
// reviewer feedback gathered on it: continuity conflicts, advisory review-pass notes, margin notes,
// and tracked-change suggestions. Mirrors the client-side Blob download the Manuscript screen uses,
// kept here so the Scene screen stays presentational.
import type { AnnotationOut, ChapterOut, SceneDetail, SuggestionOut } from "../api/types";
import { sceneLabel } from "./format";

// Continuity critiques carry a prose↔ledger mismatch in their payload; everything else is an advisory
// note. (Same split the Scene screen uses to fill its Continuity vs. Notes tabs.)
const isConflict = (c: SceneDetail["critiques"][number]): boolean =>
  !!c.payload && c.payload.prose_value != null && c.payload.ledger_value != null;
const pstr = (c: SceneDetail["critiques"][number], key: string): string => {
  const v = c.payload?.[key];
  return v == null ? "" : String(v);
};

export function buildSceneMarkdown(
  scene: SceneDetail,
  chapter: ChapterOut | null,
  annotations: AnnotationOut[],
  suggestions: SuggestionOut[],
): string {
  const conflicts = scene.critiques.filter(isConflict);
  const notes = scene.critiques.filter((c) => !isConflict(c));

  const meta = [
    chapter ? `Chapter ${chapter.chapter_no}` : null,
    chapter?.pov ? `POV · ${chapter.pov}` : null,
    `Scene ${scene.scene_no}`,
    `v${scene.version}`,
    scene.status.replace(/_/g, " "),
  ]
    .filter(Boolean)
    .join(" · ");

  const lines: string[] = [`# ${sceneLabel(scene)}`, "", `_${meta}_`, ""];
  lines.push((scene.prose ?? "").trim() || "_(no prose)_", "");

  // Only emit the feedback section if there's something to say about the scene.
  const hasFeedback = conflicts.length || notes.length || annotations.length || suggestions.length;
  if (hasFeedback) {
    lines.push("---", "", "## Reviewer feedback", "");

    if (conflicts.length) {
      lines.push("### Continuity conflicts", "");
      for (const c of conflicts) {
        const attr = pstr(c, "attribute") || c.reviewer;
        lines.push(
          `- **${attr}** — prose \`${pstr(c, "prose_value")}\` vs ledger \`${pstr(c, "ledger_value")}\``,
        );
        const ctx = pstr(c, "context_sentence");
        if (ctx) lines.push(`  - context: "${ctx}"`);
      }
      lines.push("");
    }

    if (notes.length) {
      lines.push("### Reviewer notes", "");
      for (const n of notes) {
        lines.push(`- **${n.reviewer}** (${n.severity}): ${n.note ?? ""}`);
      }
      lines.push("");
    }

    if (annotations.length) {
      lines.push("### Margin notes", "");
      for (const a of annotations) {
        if (a.quote) lines.push(`- > "${a.quote}"`);
        lines.push(`${a.quote ? "  " : "- "}${a.note ?? ""}${a.author ? ` — *${a.author}*` : ""}`);
      }
      lines.push("");
    }

    if (suggestions.length) {
      lines.push("### Suggested changes", "");
      for (const s of suggestions) {
        const repl = s.new_text?.trim() || "_(delete)_";
        lines.push(`- \`${s.quote}\` → ${repl}${s.status !== "pending" ? ` _(${s.status})_` : ""}`);
        if (s.why) lines.push(`  - why: ${s.why}`);
      }
      lines.push("");
    }
  }

  return lines.join("\n").replace(/\n+$/, "") + "\n";
}

/** Safe-ish filename stem from a scene's chapter/scene numbers. */
export function sceneMarkdownFilename(scene: SceneDetail, chapter: ChapterOut | null): string {
  const ch = chapter ? `ch${chapter.chapter_no}_` : "";
  return `scene_${ch}s${scene.scene_no}_v${scene.version}.md`;
}

// One scene's worth of everything the export needs — the detail plus the feedback collections the
// data layer only loads for the active scene (so bulk export fetches them per scene on demand).
export interface SceneExportItem {
  scene: SceneDetail;
  chapter: ChapterOut | null;
  annotations: AnnotationOut[];
  suggestions: SuggestionOut[];
}

/** Bundle several scenes into one Markdown document — each scene rendered as it is singly, with a
 *  rule between them so the scene boundaries read clearly. */
export function buildScenesMarkdown(items: SceneExportItem[]): string {
  return (
    items
      .map((it) =>
        buildSceneMarkdown(it.scene, it.chapter, it.annotations, it.suggestions).trimEnd(),
      )
      .join("\n\n---\n\n") + "\n"
  );
}

/** Build a Markdown blob and download it client-side (no deps, no server call). */
export function downloadMarkdown(filename: string, content: string): void {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
